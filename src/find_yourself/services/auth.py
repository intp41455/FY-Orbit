"""Owner sessions, CSRF tokens and server-side service identities (§5.1).

This is the BUG-03 fix: identity is established here from verified sessions or
service-identity records, never from request-body ``owner_id``/``role``. The
local dev token mode is restricted to loopback and refused in production.
CSRF double-submit token is bound to the session; mutating requests must present
a matching token.
"""

import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from ..db.models import AuthSession, ServiceIdentity
from ..db.types import utcnow
from .actor import Actor
from .errors import PermissionDenied, Unauthenticated, ValidationFailed
from .audit import AuditService

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class AuthService:
    def __init__(self, session: Session, audit: AuditService, *, environment: str = "local",
                 local_token: str = ""):
        self.s = session
        self.audit = audit
        self.environment = environment
        self.local_token = local_token

    # -- owner sessions ---------------------------------------------------
    def create_owner_session(self, owner_id: str, ttl_seconds: int = 8 * 3600) -> AuthSession:
        now = utcnow()
        row = AuthSession(
            id=uuid4().hex, owner_id=owner_id, csrf_secret=secrets.token_hex(32),
            created_at=now, expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(Actor.owner(owner_id), "auth.session_created", row.id)
        return row

    def verify_session(self, session_id: str) -> AuthSession:
        row = self.s.get(AuthSession, session_id)
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            raise Unauthenticated("invalid_session", "Session is missing, expired or revoked")
        return row

    def owner_actor(self, session_id: str, csrf_token: str = "") -> Actor:
        row = self.verify_session(session_id)
        if csrf_token and not secrets.compare_digest(csrf_token, row.csrf_secret):
            raise PermissionDenied("csrf", "CSRF token mismatch", 403)
        return Actor.owner(row.owner_id, csrf_token=row.csrf_secret)

    def revoke_session(self, actor: Actor, session_id: str) -> None:
        row = self.s.get(AuthSession, session_id)
        if row is not None:
            row.revoked_at = utcnow()
            self.s.flush()

    # -- service identities ------------------------------------------------
    def register_service(self, actor: Actor, *, kind: str, name: str, domains: list[str]) -> ServiceIdentity:
        actor.require_owner()
        token = secrets.token_hex(32)
        row = ServiceIdentity(
            id=uuid4().hex, kind=kind, name=name, domains=domains, capabilities=[],
            secret_hash=hashlib.sha256(token.encode()).hexdigest(), state="active",
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(actor, "service.registered", row.id, {"kind": kind})
        # The plaintext token is returned once to the caller that provisions it.
        row.plaintext_token = token  # type: ignore[attr-defined]
        return row

    def service_actor(self, token: str) -> Actor:
        if not token:
            raise Unauthenticated("no_credentials", "Service token required")
        digest_ = hashlib.sha256(token.encode()).hexdigest()
        rows = self.s.query(ServiceIdentity).filter_by(secret_hash=digest_, state="active").all()
        if not rows:
            raise Unauthenticated("bad_service_token", "Unknown or revoked service identity")
        row = rows[0]
        if row.lease_expires_at is not None and row.lease_expires_at <= utcnow():
            raise Unauthenticated("service_expired", "Service identity lease expired")
        return Actor.service(row.id, row.kind, list(row.domains or []))

    # -- local dev token (loopback only) ----------------------------------
    def local_dev_actor(self, token: str, remote_addr: str) -> Actor:
        if self.environment == "production":
            raise PermissionDenied("local_token_disabled", "Local dev token is disabled in production", 403)
        if remote_addr not in LOOPBACK:
            raise PermissionDenied("local_token_loopback", "Local dev token only allowed from loopback", 403)
        if not self.local_token or not secrets.compare_digest(token, self.local_token):
            raise Unauthenticated("bad_dev_token", "Invalid local dev token")
        return Actor.owner("owner")

    # -- CSRF ----------------------------------------------------------------
    @staticmethod
    def require_csrf(actor: Actor, provided: str | None) -> None:
        if actor.subject_type != "owner":
            return  # service identities present their own credentials
        if not provided or not secrets.compare_digest(provided, actor.csrf_token):
            raise PermissionDenied("csrf", "Missing or invalid CSRF token", 403)
