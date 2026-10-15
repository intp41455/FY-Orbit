"""Owner sessions, CSRF tokens and server-side service identities (§5.1).

This is the BUG-03 fix: identity is established here from verified sessions or
service-identity records, never from request-body ``owner_id``/``role``. The
local dev token mode is restricted to loopback and refused in production.
CSRF double-submit token is bound to the session; mutating requests must present
a matching token.
"""

import hashlib
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db.models import (
    USER_PLANS,
    AuthSession,
    Conversation,
    Memory,
    ServiceIdentity,
    Task,
    User,
    UserConsent,
)
from ..db.types import utcnow
from ..net import is_loopback
from .actor import Actor
from .audit import AuditService
from .errors import PermissionDenied, Unauthenticated, ValidationFailed
from .passwords import hash_password, verify_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN_LENGTH = 8
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60
CONSENT_DOC_ID = "privacy-policy"
CONSENT_DOC_VERSION = "v1"


class AuthService:
    def __init__(self, session: Session, audit: AuditService, *, environment: str = "local",
                 local_token: str = ""):
        self.s = session
        self.audit = audit
        self.environment = environment
        self.local_token = local_token
        # in-process login throttling: (email, ip) -> [failures, lock_until]
        self._login_failures: dict[tuple[str, str], list] = {}

    # -- owner sessions ---------------------------------------------------
    def create_owner_session(self, owner_id: str, ttl_seconds: int = 8 * 3600) -> AuthSession:
        """Create a session and return the row; the opaque cookie token is on
        ``row.plaintext_token`` (sha256 stored in ``token_hash``). Legacy
        callers that keep using ``row.id`` as the cookie value still work via
        the id fallback in :meth:`resolve_session`."""
        now = utcnow()
        token = secrets.token_urlsafe(32)
        row = AuthSession(
            id=uuid4().hex, owner_id=owner_id, csrf_secret=secrets.token_hex(32),
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            created_at=now, expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(Actor.owner(owner_id), "auth.session_created", row.id)
        row.plaintext_token = token  # type: ignore[attr-defined]
        return row

    def resolve_session(self, presented: str) -> AuthSession:
        """Resolve a cookie/bearer value to a live session.

        Hashed tokens are checked first; plaintext session ids (legacy
        dev-token and OIDC flows) still resolve by primary key.
        """
        digest_ = hashlib.sha256(presented.encode()).hexdigest()
        row = self.s.query(AuthSession).filter_by(token_hash=digest_).first()
        if row is None:
            row = self.s.get(AuthSession, presented)
        if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
            raise Unauthenticated("invalid_session", "Session is missing, expired or revoked")
        return row

    def verify_session(self, session_id: str) -> AuthSession:
        return self.resolve_session(session_id)

    def owner_actor(self, session_id: str, csrf_token: str = "") -> Actor:
        row = self.verify_session(session_id)
        if csrf_token and not secrets.compare_digest(csrf_token, row.csrf_secret):
            raise PermissionDenied("csrf", "CSRF token mismatch", 403)
        return Actor.owner(row.owner_id, csrf_token=row.csrf_secret)

    def revoke_session(self, actor: Actor, session_id: str) -> None:
        try:
            row = self.resolve_session(session_id)
        except Unauthenticated:
            return
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

    def issue_scoped_service_credential(
        self,
        actor: Actor,
        *,
        name: str,
        kind: str = "agent",
        task_id: str | None = None,
        domains: list[str] | None = None,
        tools: list[str] | None = None,
        budget_cents: int | None = None,
        ttl_seconds: int = 3600,
    ) -> tuple[ServiceIdentity, str]:
        """Issue a scoped service credential bounded by task, domains, tools, budget, and deadline."""
        actor.require_owner()
        token = secrets.token_hex(32)
        capabilities = [f"tool:{t}" for t in (tools or [])]
        if budget_cents is not None:
            capabilities.append(f"budget:{budget_cents}")
        now = utcnow()
        expires = now + timedelta(seconds=ttl_seconds)
        row = ServiceIdentity(
            id=uuid4().hex,
            kind=kind,
            name=name,
            task_binding=task_id,
            domains=list(domains or []),
            capabilities=capabilities,
            secret_hash=hashlib.sha256(token.encode()).hexdigest(),
            lease_expires_at=expires,
            state="active",
        )
        self.s.add(row)
        self.s.flush()
        self.audit.append(
            actor, "service.scoped_credential_issued", row.id,
            {"kind": kind, "task_id": task_id, "domains": domains, "tools": tools, "ttl": ttl_seconds, "budget": budget_cents}
        )
        row.plaintext_token = token  # type: ignore[attr-defined]
        return row, token

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

        tools = tuple(c.removeprefix("tool:") for c in (row.capabilities or []) if c.startswith("tool:"))
        budget_caps = [c.removeprefix("budget:") for c in (row.capabilities or []) if c.startswith("budget:")]
        budget_cents = int(budget_caps[0]) if budget_caps and budget_caps[0].isdigit() else None

        return Actor.service(
            row.id,
            row.kind,
            list(row.domains or []),
            task_id=row.task_binding,
            allowed_tools=tools,
            expires_at=row.lease_expires_at,
            max_budget_cents=budget_cents,
        )

    # -- local dev token (loopback only) ----------------------------------
    def local_dev_actor(self, token: str, remote_addr: str) -> Actor:
        """Dev-only bootstrap identity. This is NOT the multi-tenant login path:
        real users authenticate via :meth:`register_user` / :meth:`login_user`.
        The bootstrap maps to the legacy single-owner id (settings.owner_id)."""
        if self.environment == "production":
            raise PermissionDenied("local_token_disabled", "Local dev token is disabled in production", 403)
        if not is_loopback(remote_addr):
            raise PermissionDenied("local_token_loopback", "Local dev token only allowed from loopback", 403)
        if not self.local_token or not secrets.compare_digest(token, self.local_token):
            raise Unauthenticated("bad_dev_token", "Invalid local dev token")
        return Actor.owner("owner")

    # -- local users (Route B multi-tenant) ---------------------------------
    def _login_lock(self, email: str, ip: str) -> None:
        key = (email.lower(), ip)
        entry = self._login_failures.get(key)
        if entry and entry[1] is not None:
            if entry[1] > utcnow():
                raise PermissionDenied(
                    "login_locked",
                    f"Too many failed logins; try again after {int((entry[1] - utcnow()).total_seconds())}s",
                    429,
                )
            # lock expired -> reset
            self._login_failures.pop(key, None)

    def _record_login_failure(self, email: str, ip: str) -> None:
        key = (email.lower(), ip)
        entry = self._login_failures.setdefault(key, [0, None])
        entry[0] += 1
        if entry[0] >= LOGIN_MAX_FAILURES:
            entry[1] = utcnow() + timedelta(seconds=LOGIN_LOCK_SECONDS)

    def _reset_login_failures(self, email: str, ip: str) -> None:
        self._login_failures.pop((email.lower(), ip), None)

    def register_user(
        self,
        *,
        email: str,
        password: str,
        consent_accepted: bool,
        display_name: str = "",
        ip: str = "",
    ) -> tuple[User, AuthSession]:
        """Create a user + privacy consent record, then open a session.

        ``consent_accepted`` must be explicitly true — registration without a
        recorded consent is refused (GDPR lawful basis must be captured).
        """
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValidationFailed("invalid_email", "A valid email address is required")
        if len(password or "") < PASSWORD_MIN_LENGTH:
            raise ValidationFailed("weak_password", f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        if not consent_accepted:
            raise ValidationFailed("consent_required", "Privacy policy consent must be accepted to register")

        existing = self.s.query(User).filter_by(email=email).first()
        if existing is not None:
            raise PermissionDenied("email_taken", "An account with this email already exists", 409)

        user = User(
            id=uuid4().hex,
            email=email,
            password_hash=hash_password(password),
            display_name=(display_name or "").strip()[:120],
            status="active",
        )
        self.s.add(user)
        self.s.flush()
        self.s.add(UserConsent(
            id=uuid4().hex, user_id=user.id,
            doc_id=CONSENT_DOC_ID, doc_version=CONSENT_DOC_VERSION,
            agreed_at=utcnow(), ip=(ip or "")[:64],
        ))
        self.s.flush()
        self.audit.append(Actor.owner(user.id), "user.registered", user.id, {"email_domain": email.split("@")[-1]})
        session_row = self.create_owner_session(user.id)
        return user, session_row

    def login_user(self, *, email: str, password: str, ip: str = "") -> tuple[User, AuthSession]:
        """Password login with per-(email, ip) failure throttling."""
        email = (email or "").strip().lower()
        self._login_lock(email, ip)
        user = self.s.query(User).filter_by(email=email).first()
        if user is None or user.status != "active" or not verify_password(user.password_hash, password or ""):
            self._record_login_failure(email, ip)
            raise Unauthenticated("bad_credentials", "Invalid email or password")
        self._reset_login_failures(email, ip)
        session_row = self.create_owner_session(user.id)
        return user, session_row

    def list_consents(self, owner_id: str) -> list[UserConsent]:
        return list(self.s.query(UserConsent).filter_by(user_id=owner_id).order_by(UserConsent.agreed_at).all())

    def revoke_owner_sessions(self, owner_id: str) -> int:
        rows = self.s.query(AuthSession).filter_by(owner_id=owner_id, revoked_at=None).all()
        now = utcnow()
        for r in rows:
            r.revoked_at = now
        if rows:
            self.s.flush()
        return len(rows)

    # -- CSRF ----------------------------------------------------------------
    @staticmethod
    def require_csrf(actor: Actor, provided: str | None) -> None:
        if actor.subject_type != "owner":
            return  # service identities present their own credentials
        if not provided or not secrets.compare_digest(provided, actor.csrf_token):
            raise PermissionDenied("csrf", "Missing or invalid CSRF token", 403)

    # -- W8 account tiers: guest / registered / member -----------------------
    #
    # Product rule (W8 task book §2.2): the workbench is usable with **no login
    # at all**. A guest is therefore a *real* ``users`` row, not a synthetic
    # bypass: it owns data exactly like any other tenant, which is what makes
    # "play as guest, then upgrade, keep everything" a same-row UPDATE instead
    # of a data migration.

    #: Email domain that marks a row as a local guest account.
    GUEST_EMAIL_DOMAIN = "local"

    @classmethod
    def is_guest_email(cls, email: str) -> bool:
        """True for the synthetic ``guest-<uuid>@local`` addresses we mint.

        Used by the API to compute ``is_guest`` for ``/auth/me`` and by the
        frontend login-wall to decide which entries require a real account. Kept
        as a classmethod so the route, the service and the tests agree on one
        definition instead of three string literals.
        """
        return (email or "").strip().lower().endswith("@" + cls.GUEST_EMAIL_DOMAIN)

    def find_guest(self, owner_id: str) -> User | None:
        """Return the guest row for ``owner_id``, or None if not a guest."""
        user = self.s.get(User, owner_id)
        if user is not None and user.status == "guest":
            return user
        return None

    def create_guest_user(self, *, ip: str = "") -> tuple[User, AuthSession]:
        """Create a local guest account and open a session for it.

        No consent record is written: a guest never had a chance to accept the
        privacy policy, and inventing one would be a fabricated compliance
        record. Consent is captured for real at upgrade time (:meth:`upgrade_guest`).

        The account is local-only — callers must gate on environment (see
        ``routes/guest.py``) because "no login" must never mean "no account" on a
        shared or remote deployment.
        """
        user = User(
            id=uuid4().hex,
            email=f"guest-{uuid4().hex}@{self.GUEST_EMAIL_DOMAIN}",
            # Unusable placeholder: a guest has no password and must not be able
            # to log in through the password form.
            password_hash="!guest-no-password!",
            display_name="游客",
            status="guest",
            plan="free",
        )
        self.s.add(user)
        self.s.flush()
        self.audit.append(
            Actor.owner(user.id), "user.guest_created", user.id, {"ip": (ip or "")[:64]}
        )
        # R1.2：访客会话与 cookie 同为 365 天（8 小时是 P0-1 根因之一）。
        session_row = self.create_owner_session(user.id, ttl_seconds=365 * 24 * 3600)
        # R6 惰性清理：顺手清掉过期且无业务数据的空访客行（best-effort，不阻断创建）。
        try:
            self.purge_expired_guests()
        except Exception:  # noqa: BLE001 — 清理失败不该让建访客失败
            pass
        return user, session_row

    def reclaim_guest_session(self, guest_id: str, *, ip: str = "") -> tuple[User, AuthSession]:
        """凭客户端留存的访客标记找回原访客（I-13 R1.4）。

        只认仍存在的 guest 行：已升级/已删除/不存在一律诚实失败，
        不造新行（否则“找回”语义被稀释成“新建”）。
        新开会话同样 365 天。
        """
        user = self.s.get(User, (guest_id or "").strip())
        if user is None or user.status != "guest":
            raise Unauthenticated("guest_not_found", "访客不存在或已升级/删除，无法找回")
        session_row = self.create_owner_session(user.id, ttl_seconds=365 * 24 * 3600)
        self.audit.append(
            Actor.owner(user.id), "user.guest_reclaimed", user.id, {"ip": (ip or "")[:64]}
        )
        return user, session_row

    def purge_expired_guests(self, *, ttl_days: int = 90, limit: int = 200) -> int:
        """清理过期且无业务数据的空访客行（I-13 R6）。

        只删同时满足的行：status=guest、创建时间超 TTL、有数据的（会话/任务/
        记忆任一存在）**永不自动删**。连带删其会话行。返回删除数。
        定时轨挂在 app lifespan 启动扫描（同文件调用处注释）。
        """
        from datetime import timedelta

        cutoff = utcnow() - timedelta(days=ttl_days)
        candidates = self.s.execute(
            select(User).where(User.status == "guest", User.created_at < cutoff).limit(limit)
        ).scalars().all()
        removed = 0
        for user in candidates:
            has_data = (
                self.s.execute(
                    select(func.count()).select_from(Conversation).where(
                        Conversation.owner_id == user.id)
                ).scalar_one() > 0
                or self.s.execute(
                    select(func.count()).select_from(Task).where(
                        Task.owner_id == user.id)
                ).scalar_one() > 0
                or self.s.execute(
                    select(func.count()).select_from(Memory).where(
                        Memory.owner_id == user.id)
                ).scalar_one() > 0
            )
            if has_data:
                continue
            self.s.execute(
                delete(AuthSession).where(AuthSession.owner_id == user.id))
            self.s.delete(user)
            removed += 1
        if removed:
            self.s.flush()
        return removed

    def upgrade_guest(
        self,
        actor: Actor,
        *,
        email: str,
        password: str,
        consent_accepted: bool,
        display_name: str = "",
        ip: str = "",
    ) -> tuple[User, AuthSession]:
        """Turn the current guest session into a registered account, in place.

        The row keeps its ``id`` — which is the ``owner_id`` on every
        ownership-scoped table — so knowledge base, cabin saves, canvas and
        memories all stay attached with no migration. Only ``email``,
        ``password_hash``, ``status`` and optionally ``display_name`` change.

        Refuses to touch an already-registered account, so this can never be
        used to silently take over somebody else's session.
        """
        actor.require_owner()
        user = self.s.get(User, actor.owner_id)
        if user is None:
            raise Unauthenticated("unknown_account", "Account for this session no longer exists")
        if user.status != "guest":
            raise PermissionDenied(
                "already_registered",
                "This account is already registered; use the login form instead",
                409,
            )

        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise ValidationFailed("invalid_email", "A valid email address is required")
        if len(password or "") < PASSWORD_MIN_LENGTH:
            raise ValidationFailed(
                "weak_password", f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
            )
        if not consent_accepted:
            raise ValidationFailed("consent_required", "Privacy policy consent must be accepted to register")
        if self.is_guest_email(email):
            raise ValidationFailed(
                "reserved_email", "A real email address is required to complete registration"
            )

        existing = self.s.query(User).filter_by(email=email).first()
        if existing is not None:
            raise PermissionDenied("email_taken", "An account with this email already exists", 409)

        # R12：先记下原 guest 邮箱，升级后记入审计（不做认领机制，但留痕可查）。
        old_guest_email = user.email
        user.email = email
        user.password_hash = hash_password(password)
        user.status = "active"
        if (display_name or "").strip():
            user.display_name = display_name.strip()[:120]
        user.version += 1
        self.s.add(
            UserConsent(
                id=uuid4().hex,
                user_id=user.id,
                doc_id=CONSENT_DOC_ID,
                doc_version=CONSENT_DOC_VERSION,
                agreed_at=utcnow(),
                ip=(ip or "")[:64],
            )
        )
        self.s.flush()
        self.audit.append(
            Actor.owner(user.id),
            "user.guest_upgraded",
            user.id,
            {"email_domain": email.split("@")[-1], "old_guest_email": old_guest_email},
        )
        # The existing session stays valid: same owner_id, so nothing is lost.
        return user, self.create_owner_session(user.id)

    def describe_account(self, owner_id: str) -> dict:
        """Account tier summary for ``/auth/me`` and the settings account card.

        Returns a neutral, honest payload for owners that are not ``users`` rows
        (the legacy bootstrap owner, service identities): ``plan`` stays
        ``unknown`` instead of claiming a tier that does not exist.
        """
        user = self.s.get(User, owner_id)
        if user is None:
            return {
                "email": "",
                "is_guest": False,
                "plan": "unknown",
                "display_name": "",
                "status": "unknown",
            }
        return {
            "email": user.email,
            "is_guest": user.status == "guest",
            "plan": user.plan if user.plan in USER_PLANS else "free",
            "display_name": user.display_name,
            "status": user.status,
        }
