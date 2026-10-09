"""S01, S03, S10, S02: anonymous access, CSRF/Origin, production config guards, OIDC rejection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from helpers import LOCAL_TOKEN, OWNER_SUB, login_owner
from itsdangerous import URLSafeTimedSerializer

from find_yourself.config import Settings
from find_yourself.services.errors import Unauthenticated


def test_health_live_ready(client: TestClient):
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # In tests Temporal is not wired; ready must report it honestly, never claim it.
    assert body.get("temporal") in ("disabled", "ready")


def test_s01_anonymous_cannot_read_private(client: TestClient):
    r = client.get("/api/conversations")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"
    assert "conversations" not in r.json()


def test_s01_anonymous_write_rejected(client: TestClient):
    r = client.post("/api/conversations", json={"title": "x", "domain": "personal"})
    assert r.status_code == 401


def test_s03_csrf_required_for_owner_mutating_request(client: TestClient):
    login_owner(client)
    r = client.post("/api/conversations", json={"title": "t", "domain": "personal"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf"


def test_s03_correct_csrf_allows_write(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/conversations", json={"title": "ok", "domain": "personal"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "ok"


def test_s03_cross_site_origin_rejected(client: TestClient):
    headers = login_owner(client)
    headers["Origin"] = "https://evil.example"
    r = client.post("/api/conversations", json={"title": "x", "domain": "personal"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "origin"


def test_s10_production_requires_oidc_and_strong_secret():
    base = dict(environment="production", session_secret="x" * 40,
                database_url="postgresql://x", public_url="https://example.com",
                oidc_issuer="https://idp", oidc_client_id="c", oidc_owner_sub="s",
                s3_endpoint="http://s3", temporal_address="localhost:7233")
    # Weak session secret rejected.
    with pytest.raises(Exception):
        Settings(**{**base, "session_secret": "short"})
    # Production without OIDC owner sub rejected.
    with pytest.raises(Exception):
        Settings(**{**base, "oidc_issuer": "", "oidc_client_id": "", "oidc_owner_sub": ""})
    # Production local token disabled.
    with pytest.raises(Exception):
        Settings(**{**base, "local_token": "leaked"})


def test_s02_oidc_verify_rejects_wrong_issuer_aud_sub_expired(oidc):
    """Real RSA-signed tokens from the local issuer; verify must reject each."""
    cases = ["wrong-issuer", "wrong-audience", "wrong-subject", "expired"]
    for code in cases:
        tok = oidc.exchange_code(code=code, code_verifier="v", redirect_uri="http://127.0.0.1:8000/auth/callback")
        with pytest.raises(Unauthenticated):
            oidc.verify_id_token(tok["id_token"], expected_nonce="test-nonce")


def test_s02_oidc_happy_path_issues_owner_session(client: TestClient, oidc, settings):
    # Build a signed state cookie whose nonce matches the stub's "test-nonce".
    ser = URLSafeTimedSerializer(settings.session_secret, salt="fy-oidc-state")
    state = "fixed-state"
    cookie = ser.dumps({
        "state": state, "nonce": "test-nonce", "code_verifier": "v",
        "redirect_uri": "http://127.0.0.1:8000/auth/callback",
    })
    client.cookies.set("fy_oauth", cookie)
    r = client.get("/auth/callback", params={"code": "valid", "state": state},
                  follow_redirects=False)
    assert r.status_code in (302, 307)
    assert client.cookies.get("fy_session") is not None
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["subject_type"] == "owner"
    assert me.json()["owner_id"] == OWNER_SUB


def test_s02_callback_without_state_cookie_rejected(client: TestClient):
    r = client.get("/auth/callback", params={"code": "valid", "state": "x"})
    assert r.status_code in (400, 401)
    assert client.cookies.get("fy_session") is None


def test_local_dev_token_rejected_non_loopback(app, settings):
    from starlette.testclient import TestClient
    # Default TestClient reports client "testserver" -> loopback gate rejects.
    c = TestClient(app)
    r = c.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "local_token_loopback"
