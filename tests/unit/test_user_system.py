"""Route B multi-tenant user system: registration, login throttling, session
token hashing, and account deletion.

Service-level tests (this file) cover the AuthService contract; API-level
behavior lives in tests/api/test_user_auth_api.py.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from find_yourself.db.models import AuthSession, User, UserConsent
from find_yourself.services.audit import AuditService
from find_yourself.services.auth import AuthService
from find_yourself.services.deletion import DeletionService
from find_yourself.services.errors import PermissionDenied, Unauthenticated, ValidationFailed
from find_yourself.services.auth import LOGIN_MAX_FAILURES


def _auth(session) -> AuthService:
    return AuthService(session, AuditService(session), environment="local", local_token="dev-token-secret")


def test_register_creates_user_consent_and_session(session):
    auth = _auth(session)
    user, row = auth.register_user(
        email="Alice@Example.COM ", password="s3cretpass!", consent_accepted=True,
        display_name="Alice", ip="127.0.0.1",
    )
    session.flush()

    assert user.email == "alice@example.com"  # normalized lowercase
    assert user.status == "active"
    assert user.password_hash.startswith("$argon2")

    consents = session.execute(
        select(UserConsent).where(UserConsent.user_id == user.id)
    ).scalars().all()
    assert len(consents) == 1
    assert consents[0].doc_id == "privacy-policy"
    assert consents[0].ip == "127.0.0.1"

    # session issues an opaque hashed token
    token = row.plaintext_token
    assert token != row.id
    assert row.token_hash is not None and len(row.token_hash) == 64
    assert auth.resolve_session(token).id == row.id
    assert token not in (row.token_hash,)


def test_register_refuses_missing_consent_weak_password_bad_email_duplicate(session):
    auth = _auth(session)

    with pytest.raises(ValidationFailed) as exc:
        auth.register_user(email="a@b.co", password="longenough1", consent_accepted=False)
    assert exc.value.code == "consent_required"

    with pytest.raises(ValidationFailed) as exc:
        auth.register_user(email="a@b.co", password="short", consent_accepted=True)
    assert exc.value.code == "weak_password"

    with pytest.raises(ValidationFailed) as exc:
        auth.register_user(email="not-an-email", password="longenough1", consent_accepted=True)
    assert exc.value.code == "invalid_email"

    auth.register_user(email="dupe@x.io", password="longenough1", consent_accepted=True)
    session.flush()
    with pytest.raises(PermissionDenied) as exc:
        auth.register_user(email="dupe@x.io", password="otherpassword1", consent_accepted=True)
    assert exc.value.code == "email_taken"


def test_login_success_failure_and_lockout(session):
    auth = _auth(session)
    auth.register_user(email="lock@x.io", password="correct-horse", consent_accepted=True)
    session.flush()

    user, row = auth.login_user(email="lock@x.io", password="correct-horse", ip="10.0.0.1")
    assert user.email == "lock@x.io"

    # a successful login resets the failure counter (standard throttling semantics)
    auth.login_user(email="lock@x.io", password="correct-horse", ip="10.0.0.1")

    # 5 consecutive failures lock the (email, ip) pair for 15 minutes
    for _ in range(LOGIN_MAX_FAILURES):
        with pytest.raises(Unauthenticated):
            auth.login_user(email="lock@x.io", password="wrong-password", ip="10.0.0.1")
    with pytest.raises(PermissionDenied) as exc:
        auth.login_user(email="lock@x.io", password="correct-horse", ip="10.0.0.1")
    assert exc.value.code == "login_locked"

    # a different ip is not locked
    auth.login_user(email="lock@x.io", password="correct-horse", ip="10.0.0.2")


def test_session_token_hashed_and_legacy_id_fallback(session):
    auth = _auth(session)
    row = auth.create_owner_session("owner-legacy")
    session.flush()

    token = row.plaintext_token
    resolved = auth.resolve_session(token)
    assert resolved.id == row.id
    # legacy plaintext-id resolution still works (dev-token / old cookies)
    assert auth.resolve_session(row.id).id == row.id
    # random values do not resolve
    with pytest.raises(Unauthenticated):
        auth.resolve_session("not-a-session")

    auth.revoke_session(Actor_owner(), token)
    session.flush()
    with pytest.raises(Unauthenticated):
        auth.resolve_session(token)


def Actor_owner():
    from find_yourself.services.actor import Actor
    return Actor.owner("owner-legacy")


def test_delete_account_cascades_and_retains_consents(session):
    auth = _auth(session)
    user, row = auth.register_user(email="bye@x.io", password="longenough1", consent_accepted=True)
    session.flush()

    from find_yourself.db.models import Memory
    from find_yourself.db.types import utcnow
    session.add(Memory(
        id=uuid4().hex, owner_id=user.id, domain="personal", category="preference",
        content="private note", content_hash="x" * 64, active=True, created_at=utcnow(),
    ))
    session.flush()

    actor = Actor_owner().__class__(subject_type="owner", owner_id=user.id)
    deletion = DeletionService(session, AuditService(session))
    result = deletion.delete_account(actor, ip="127.0.0.1")
    session.flush()

    assert result["memories_deleted"] >= 1
    assert result["consents_retained"] == 1

    # user anonymized and locked, consent record retained
    u = session.get(User, user.id)
    assert u.status == "deleted"
    assert u.password_hash == "$locked$"
    assert u.email.startswith("deleted-")
    assert u.email.endswith("@invalid")
    assert session.execute(
        select(UserConsent).where(UserConsent.user_id == user.id)
    ).scalars().all(), "consent records must be retained for compliance"

    # all sessions revoked
    sessions = session.execute(
        select(AuthSession).where(AuthSession.owner_id == user.id)
    ).scalars().all()
    assert all(s.revoked_at is not None for s in sessions)

    # memories soft-deleted
    mems = session.execute(select(Memory).where(Memory.owner_id == user.id)).scalars().all()
    assert all(m.deleted_at is not None and m.content == "" for m in mems)
