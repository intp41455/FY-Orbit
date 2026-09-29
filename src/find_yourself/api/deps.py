"""FastAPI dependency injection: identity, CSRF/Origin and service wiring.

Security model (FROZEN_CONTRACT §5, BUG-03/BUG-04):

* The :class:`Actor` is built ONLY here, from a verified owner session cookie
  or a server-side service-identity bearer token. Request-body
  ``owner_id``/``role``/``domain`` never promote identity.
* Mutating owner requests must present a session-bound CSRF token and a
  same-site ``Origin`` when the browser sends one.
* Service identities present their own bearer credential; they cannot call
  owner-only decision endpoints (enforced by services / ``require_owner``).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..config import Settings
from ..services.actor import Actor
from ..services.agent import AgentService
from ..services.audit import AuditService
from ..services.auth import AuthService
from ..services.budget import BudgetService
from ..services.deletion import DeletionService
from ..services.errors import PermissionDenied, Unauthenticated, ValidationFailed
from ..services.grant import GrantService
from ..services.memory import MemoryService
from ..services.outbox import OutboxService
from ..services.proposal import ProposalService
from ..services.skill import SkillService

SESSION_COOKIE = "fy_session"
CSRF_HEADER = "x-csrf-token"
AUTH_HEADER = "authorization"


@dataclass
class Services:
    session: Session
    audit: AuditService
    auth: AuthService
    proposals: ProposalService
    grants: GrantService
    memory: MemoryService
    deletion: DeletionService
    budget: BudgetService
    outbox: OutboxService
    skills: SkillService
    agents: AgentService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    session = request.app.state.session_maker()
    try:
        yield session
    finally:
        session.close()


def get_services(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Services:
    audit = AuditService(db)
    grants = GrantService(db, audit)
    return Services(
        session=db,
        audit=audit,
        auth=AuthService(db, audit, environment=settings.environment, local_token=settings.local_token),
        proposals=ProposalService(db, audit),
        grants=grants,
        memory=MemoryService(db, grants, audit),
        deletion=DeletionService(db, audit),
        budget=BudgetService(db, audit),
        outbox=OutboxService(db, audit),
        skills=SkillService(db, audit),
        agents=AgentService(db, audit),
    )


def _bearer_token(request: Request) -> str | None:
    h = request.headers.get(AUTH_HEADER, "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


def get_actor(request: Request, svc: Services = Depends(get_services)) -> Actor:
    """Resolve the caller identity from bearer token or session cookie.

    A present-but-invalid bearer token is an authentication failure; we do not
    silently fall back to the session cookie.
    """
    token = _bearer_token(request)
    if token:
        return svc.auth.service_actor(token)

    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        row = svc.auth.verify_session(session_id)
        return Actor.owner(row.owner_id, csrf_token=row.csrf_secret)

    raise Unauthenticated("unauthenticated", "Authentication required")


def require_owner(actor: Actor = Depends(get_actor)) -> Actor:
    actor.require_owner()
    return actor


def _trusted_origin_host(settings: Settings) -> str:
    # public_url like http://127.0.0.1:8000 -> host 127.0.0.1:8000
    from urllib.parse import urlparse
    p = urlparse(settings.public_url)
    return p.netloc.lower()


def enforce_csrf(request: Request, actor: Actor, settings: Settings) -> None:
    """Double-submit CSRF + same-site Origin for owner mutating requests.

    Service identities (bearer) are exempt: their credential is the CSRF.
    """
    if actor.subject_type != "owner":
        return
    provided = request.headers.get(CSRF_HEADER)
    from ..services.auth import AuthService as _AS
    _AS.require_csrf(actor, provided)

    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse
        op = urlparse(origin)
        if op.netloc.lower() != _trusted_origin_host(settings):
            raise PermissionDenied("origin", "Cross-site request rejected", 403)


class CsrfProtected:
    """Dependency marker: mutating owner routes must pass CSRF + Origin."""

    def __init__(self):
        pass

    def __call__(self, request: Request, actor: Actor = Depends(get_actor),
                 settings: Settings = Depends(get_settings)) -> Actor:
        enforce_csrf(request, actor, settings)
        return actor


csrf_protected = CsrfProtected()
