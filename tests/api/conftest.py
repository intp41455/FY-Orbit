"""Shared fixtures for API/Runtime tests.

* Isolated in-memory SQLite with the same ORM metadata as the migration.
* A TestClient whose ASGI scope client is forced to ``127.0.0.1`` so the loopback
  local dev-token gate passes (without weakening it for real non-loopback peers).
* A real loopback uvicorn server running a local OIDC stub issuer (RSA-signed,
  discovery + JWKS + token endpoint), so the sync OIDC client talks over a real
  listening socket — covering wrong issuer/audience/subject/expiry (S02).

Test-only shim
--------------
``find_yourself.db.types.TZDateTime`` defines ``process_bind_param`` but no
``process_result_value``. On PostgreSQL (``TIMESTAMPTZ``) reads return aware
datetimes; on SQLite a cross-request row re-read yields a naive datetime, which
then breaks ``expires_at <= utcnow()`` in Core services. This is a Core SQLite
round-trip gap (Postgres production is unaffected). We patch the type's result
handler in the test process only; no Core source file is modified.
"""

from __future__ import annotations

import threading
import time
import base64
from datetime import timezone
from typing import Iterator

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.api.oidc import OIDCClient
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.types import TZDateTime
import find_yourself.db.models  # noqa: F401

# --- Test-only SQLite TZ shim (see module docstring; Core defect noted) -------
def _tz_result_value(self, value, dialect):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

TZDateTime.process_result_value = _tz_result_value

OWNER_SUB = "owner-sub-123"
CLIENT_ID = "fy-web"
LOCAL_TOKEN = "dev-token-secret"


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture()
def session_maker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )


def _pick_loopback_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_issuer_app(base: str, private_pem: bytes, pub_pem: bytes) -> FastAPI:
    from authlib.jose import jwt

    app = FastAPI()

    @app.get("/.well-known/openid-configuration")
    def disc():
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "jwks_uri": f"{base}/jwks",
            "response_types_supported": ["code"],
        }

    @app.get("/jwks")
    def jwks():
        from authlib.jose import JsonWebKey
        key = JsonWebKey.import_key(pub_pem)
        j = key.as_dict(is_private=False, alg="RS256", use="sig")
        j["kid"] = "test-key-1"
        return {"keys": [j]}

    @app.post("/token")
    async def token(request: Request):
        from urllib.parse import parse_qs
        raw = (await request.body()).decode()
        form = {k: v[0] for k, v in parse_qs(raw).items()}
        code = form.get("code", "valid")
        now = int(time.time())
        iss, aud, sub, exp = base, CLIENT_ID, OWNER_SUB, now + 600
        if code == "wrong-issuer":
            iss = "https://evil.example"
        elif code == "wrong-audience":
            aud = "other-client"
        elif code == "wrong-subject":
            sub = "attacker"
        elif code == "expired":
            exp = now - 100
        header = {"alg": "RS256", "kid": "test-key-1", "typ": "JWT"}
        payload = {"iss": iss, "aud": aud, "sub": sub, "nonce": "test-nonce",
                   "exp": exp, "iat": now}
        signed = jwt.encode(header, payload, private_pem)
        return {"access_token": "at", "id_token": signed.decode("ascii"), "token_type": "Bearer"}

    return app


@pytest.fixture()
def issuer() -> Iterator[str]:
    """Run the OIDC stub on a real loopback socket; yield its base URL."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    port = _pick_loopback_port()
    base = f"http://127.0.0.1:{port}"
    app = _build_issuer_app(base, private_pem, pub_pem)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if getattr(server, "servers", None):
            break
        time.sleep(0.1)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def oidc(issuer: str) -> OIDCClient:
    import httpx
    http = httpx.Client(trust_env=False, timeout=10.0)
    return OIDCClient(issuer=issuer, client_id=CLIENT_ID, owner_sub=OWNER_SUB, http=http)


@pytest.fixture()
def app(session_maker, settings, oidc) -> FastAPI:
    return create_app(session_maker=session_maker, settings=settings, oidc=oidc)


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)
    return wrapper


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c
