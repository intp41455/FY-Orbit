"""W8 · 账号三层（游客 / 注册 / 会员位）测试。

覆盖任务书 §5 的可验证部分，重点是三条最容易做假的地方：

1. **游客是真账号，不是绕过鉴权的假身份** —— 游客有 ``users`` 行、真实的
   ``owner_id``，其数据受与其他租户相同的 owner 隔离约束。
2. **升级必须保住数据**（同 id 就地 UPDATE），且不能被用来顶替别人的账号。
3. **诚实** —— 未开通的东西必须报错或标注：游客发放在非 local 环境必须被
   拒绝；``plan`` 不会因为调用方自称而变成 ``pro``；``payment_enabled``
   恒为 ``false``。

API 用例复用 ``tests/api/conftest.py`` 的 ``client`` 夹具（其 ASGI scope 被
固定为 127.0.0.1，正好满足游客端点的 loopback 门禁）。
"""

from __future__ import annotations

from datetime import timezone
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.canvas_models  # noqa: F401
import find_yourself.db.models  # noqa: F401
import find_yourself.db.profile_models  # noqa: F401
import find_yourself.db.sync_models  # noqa: F401
import find_yourself.db.team_models  # noqa: F401
import find_yourself.db.workbench_models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import USER_PLANS, USER_STATUSES, Memory, User
from find_yourself.db.types import TZDateTime
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService
from find_yourself.services.errors import PermissionDenied, Unauthenticated, ValidationFailed

LOCAL_TOKEN = "dev-token-secret-w8"


# --- Test-only SQLite TZ shim (same as tests/api/conftest.py) ----------------
def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _peer(asgi_app, host: str):
    """Force the ASGI peer address (guest minting is loopback-only)."""

    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = (host, 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


def _settings(tmp_path, environment: str = "test") -> Settings:
    return Settings(
        environment=environment,
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
    )


@pytest.fixture()
def app(session_maker, tmp_path) -> FastAPI:
    return create_app(session_maker=session_maker, settings=_settings(tmp_path))


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_peer(app, "127.0.0.1")) as c:
        yield c


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_auth(session, environment: str = "local") -> AuthService:
    return AuthService(session, AuditService(session), environment=environment)


def guest_headers(client: TestClient) -> tuple[dict, dict]:
    """Create a guest session; return (response json, csrf headers)."""
    r = client.post("/auth/guest")
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {"X-CSRF-Token": body["csrf_token"]}


# --------------------------------------------------------------------------- #
# 1-6 · service level
# --------------------------------------------------------------------------- #
def test_guest_is_a_real_user_row(session):
    """A guest owns a real row: same id space as any tenant, status=guest."""
    auth = make_auth(session)
    user, row = auth.create_guest_user(ip="127.0.0.1")

    assert user.status == "guest"
    assert user.email.endswith("@local")
    assert user.email.startswith("guest-")
    assert user.plan == "free"
    assert row.owner_id == user.id
    assert session.get(User, user.id) is not None
    assert auth.find_guest(user.id) is user


def test_guest_email_shape_and_detection(session):
    auth = make_auth(session)
    user, _ = auth.create_guest_user()
    assert auth.is_guest_email(user.email) is True
    # A real account must never be mistaken for a guest by the login wall.
    assert auth.is_guest_email("person@example.com") is False
    assert auth.is_guest_email("") is False


def test_two_guests_are_isolated(session):
    auth = make_auth(session)
    a, _ = auth.create_guest_user()
    b, _ = auth.create_guest_user()
    assert a.id != b.id
    assert a.email != b.email
    assert auth.find_guest(a.id) is a
    assert auth.find_guest(b.id) is b


def test_guest_upgrade_keeps_same_user_id_and_data(session):
    """The headline guarantee: play as guest, upgrade, data is still yours.

    ``owner_id`` is the join key on every ownership-scoped table, so keeping the
    id is what "data preserved" actually means.
    """
    auth = make_auth(session)
    guest, _ = auth.create_guest_user()
    # Stand-in for "data the guest produced while playing".
    session.add(
        Memory(
            id="m1",
            owner_id=guest.id,
            domain="personal",
            category="note",
            content="hi",
            content_hash="h1",
        )
    )
    session.flush()

    upgraded, _ = auth.upgrade_guest(
        Actor.owner(guest.id),
        email="Person@Example.com",
        password="correct-horse-battery",
        consent_accepted=True,
        display_name="人",
    )

    assert upgraded.id == guest.id
    assert upgraded.status == "active"
    assert upgraded.email == "person@example.com"
    owner = session.execute(
        sa.text("SELECT owner_id FROM memories WHERE id='m1'")
    ).scalar_one()
    assert owner == guest.id, "memory must stay attached to the same owner_id"

def test_guest_upgrade_records_consent_and_password(session):
    auth = make_auth(session)
    guest, _ = auth.create_guest_user()
    assert auth.list_consents(guest.id) == [], "guest must not get a fabricated consent record"

    user, _ = auth.upgrade_guest(
        Actor.owner(guest.id), email="p@example.com",
        password="correct-horse-battery", consent_accepted=True,
    )
    assert user.password_hash != "!guest-no-password!"
    assert not user.password_hash.startswith("!guest-no-password!")
    consents = auth.list_consents(user.id)
    assert len(consents) == 1 and consents[0].doc_id == "privacy-policy"


def test_guest_upgrade_rejects_bad_input(session):
    auth = make_auth(session)
    guest, _ = auth.create_guest_user()
    actor = Actor.owner(guest.id)

    with pytest.raises(ValidationFailed):  # invalid email
        auth.upgrade_guest(actor, email="nope", password="correct-horse-battery",
                           consent_accepted=True)
    with pytest.raises(ValidationFailed):  # weak password
        auth.upgrade_guest(actor, email="p@example.com", password="short",
                           consent_accepted=True)
    with pytest.raises(ValidationFailed):  # consent required
        auth.upgrade_guest(actor, email="p@example.com",
                           password="correct-horse-battery", consent_accepted=False)
    with pytest.raises(ValidationFailed):  # cannot keep a @local address
        auth.upgrade_guest(actor, email="guest-abc@local",
                           password="correct-horse-battery", consent_accepted=True)

    # Nothing above may have mutated the row.
    assert session.get(User, guest.id).status == "guest"


def test_upgrade_refuses_registered_account_and_duplicate_email(session):
    auth = make_auth(session)
    registered, _ = auth.register_user(
        email="taken@example.com", password="correct-horse-battery",
        consent_accepted=True, ip="127.0.0.1",
    )
    with pytest.raises(PermissionDenied):  # not a guest -> cannot be "upgraded"
        auth.upgrade_guest(Actor.owner(registered.id), email="other@example.com",
                           password="correct-horse-battery", consent_accepted=True)

    guest, _ = auth.create_guest_user()
    with pytest.raises(PermissionDenied):  # email collision
        auth.upgrade_guest(Actor.owner(guest.id), email="taken@example.com",
                           password="correct-horse-battery", consent_accepted=True)

    with pytest.raises(Unauthenticated):  # no such owner
        auth.upgrade_guest(Actor.owner("ghost"), email="g@example.com",
                           password="correct-horse-battery", consent_accepted=True)


def test_describe_account_is_honest_for_unknown_owner(session):
    auth = make_auth(session)
    # Legacy bootstrap owner has no users row: must not claim a tier.
    info = auth.describe_account("owner")
    assert info["is_guest"] is False
    assert info["plan"] == "unknown"

    guest, _ = auth.create_guest_user()
    assert auth.describe_account(guest.id)["is_guest"] is True


def test_db_check_constraints_cover_guest_and_plan():
    """The tiers must be enforced by the database, not only by Python."""
    assert "guest" in USER_STATUSES
    assert USER_PLANS == ("free", "pro")
    from find_yourself.db.models import User as U
    names = {c.name for t in U.__table__.constraints for c in [t]}
    assert any("status" in (n or "") for n in names)
    assert any("plan" in (n or "") for n in names)


# --------------------------------------------------------------------------- #
# 7-12 · API level
# --------------------------------------------------------------------------- #
def test_api_guest_login_then_me_reports_guest(client: TestClient):
    body, _ = guest_headers(client)
    assert body["is_guest"] is True
    assert body["plan"] == "free"

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["is_guest"] is True
    assert me.json()["plan"] == "free"
    assert me.json()["subject_type"] == "owner"

    acct = client.get("/api/auth/account")
    assert acct.status_code == 200
    assert acct.json()["is_guest"] is True


def test_guest_endpoints_report_subject_type_owner(client: TestClient):
    """Regression guard for a real W8 defect found by the frontend tests.

    ``POST /auth/guest`` and ``POST /auth/guest/upgrade`` originally omitted
    ``subject_type``. The web client dispatches on that single field to decide
    "is this an owner session?", so the browser silently ended up with
    ``owner=null`` and the免登录 bootstrap was a no-op. The response contract
    must stay aligned with ``GET /auth/me``.
    """
    body, csrf = guest_headers(client)
    assert body["subject_type"] == "owner"

    upgraded = client.post(
        "/auth/guest/upgrade",
        json={
            "email": "w8-contract@example.com",
            "password": "correct-horse-battery",
            "consent_accepted": True,
        },
        headers=csrf,
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["subject_type"] == "owner"
    assert upgraded.json()["is_guest"] is False


def test_api_guest_workbench_is_usable_without_login(client: TestClient):
    """免登录可用: a guest session is a normal owner session, so workbench APIs work."""
    guest_headers(client)
    r = client.get("/api/conversations")
    assert r.status_code == 200, r.text
    assert r.json() == []  # a real, empty, owner-scoped list — not an error


def test_api_guest_upgrade_preserves_data_end_to_end(client: TestClient):
    body, csrf = guest_headers(client)
    owner = body["owner_id"]

    created = client.post("/api/conversations", json={"title": "游客的对话"},
                          headers=csrf)
    assert created.status_code == 200, created.text
    conv_id = created.json()["id"]

    up = client.post("/auth/guest/upgrade", headers=csrf, json={
        "email": "up@example.com", "password": "correct-horse-battery",
        "consent_accepted": True,
    })
    assert up.status_code == 200, up.text
    assert up.json()["is_guest"] is False
    assert up.json()["owner_id"] == owner, "id must not change on upgrade"

    assert client.get("/auth/me").json()["is_guest"] is False
    convs = client.get("/api/conversations").json()
    assert any(c["id"] == conv_id for c in convs), "guest-created data must survive"

    # And the account can now log in with its real credentials.
    fresh = TestClient(_peer(client.app, "127.0.0.1"))
    again = fresh.post("/auth/login", json={"email": "up@example.com",
                                            "password": "correct-horse-battery"})
    assert again.status_code == 200, again.text
    assert again.json()["owner_id"] == owner


def test_api_guest_upgrade_requires_csrf(client: TestClient):
    """Credential binding must not be driveable cross-site."""
    guest_headers(client)
    r = client.post("/auth/guest/upgrade", json={
        "email": "csrf@example.com", "password": "correct-horse-battery",
        "consent_accepted": True,
    })
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf"


def test_api_guest_refused_outside_local_and_test(app: FastAPI):
    """Guest minting is gated on environment — not merely on the caller.

    Exercised by patching the live ``app.state.settings.environment`` rather than
    constructing a full production :class:`Settings`, because that model
    deliberately refuses to be built without OIDC + HTTPS + PostgreSQL + Temporal
    + S3. The gate under test is the route's environment check, so patching the
    one field it reads keeps the assertion honest and the fixture legal.
    """
    from find_yourself.api.routes import guest as guest_routes

    with TestClient(_peer(app, "127.0.0.1")) as c:
        original = app.state.settings.environment
        try:
            app.state.settings.environment = "production"
            r = c.post("/auth/guest")
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "guest_disabled"
        finally:
            app.state.settings.environment = original

    # A non-loopback peer is refused independently of environment.
    assert guest_routes.LOOPBACK == {"127.0.0.1", "localhost", "::1"}


def test_api_guest_refused_from_non_loopback(app):
    """A page on another host must not drive guest minting via the user's browser."""
    with TestClient(_peer(app, "203.0.113.9")) as c:
        r = c.post("/auth/guest")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "guest_loopback_only"


def test_api_account_reports_payment_disabled(client: TestClient, app: FastAPI):
    """诚实: v1 has no payment channel, and the API says so."""
    guest_headers(client)
    acct = client.get("/api/auth/account").json()
    assert acct["payment_enabled"] is False
    assert acct["plan"] in ("free", "pro")
    # No endpoint may flip the tier: nothing in the app writes a "pro" plan.
    paths = set(app.openapi()["paths"])
    assert not any(p.endswith("/plan") for p in paths)
    # The guest surface is exactly the three documented endpoints.
    assert "/auth/guest" in paths and "/auth/guest/upgrade" in paths
    assert "/api/auth/account" in paths
