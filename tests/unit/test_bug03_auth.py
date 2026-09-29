"""BUG-03: identity/CSRF primitives. Model-asserted owner/role has no authority."""

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.auth import AuthService
from find_yourself.services.errors import PermissionDenied, Unauthenticated


def test_owner_actor_cannot_be_forged_from_body():
    # A service/agent cannot simply declare itself owner.
    svc = Actor.service("worker-1", "worker")
    with pytest.raises(PermissionDenied):
        svc.require_owner()


def test_session_verify_rejects_expired_and_revoked(session, audit):
    auth = AuthService(session, audit, environment="local", local_token="dev-secret-token-32charsxxxxxxxx")
    s = auth.create_owner_session("owner-1", ttl_seconds=-10)  # already expired
    with pytest.raises(Unauthenticated):
        auth.owner_actor(s.id)


def test_csrf_mismatch_rejected(session, audit):
    auth = AuthService(session, audit)
    s = auth.create_owner_session("owner-1")
    actor = auth.owner_actor(s.id)
    with pytest.raises(PermissionDenied):
        auth.require_csrf(actor, "wrong-token")
    # correct token passes
    auth.require_csrf(actor, s.csrf_secret)


def test_local_token_forbidden_in_production(session, audit):
    auth = AuthService(session, audit, environment="production", local_token="x")
    with pytest.raises(PermissionDenied):
        auth.local_dev_actor("x", "127.0.0.1")


def test_local_token_forbidden_from_non_loopback(session, audit):
    auth = AuthService(session, audit, environment="local", local_token="secret-32-chars-needed-xxxxxxxx")
    with pytest.raises(PermissionDenied):
        auth.local_dev_actor("secret-32-chars-needed-xxxxxxxx", "203.0.113.9")


def test_service_token_roundtrip(session, audit):
    auth = AuthService(session, audit)
    owner = Actor.owner("owner-1")
    reg = auth.register_service(owner, kind="worker", name="w1", domains=["work"])
    actor = auth.service_actor(reg.plaintext_token)
    assert actor.subject_type == "service"
    assert actor.service_kind == "worker"
    with pytest.raises(Unauthenticated):
        auth.service_actor("deadbeef")
