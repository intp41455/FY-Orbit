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
from ..services.capability import CapabilityBroker, build_capability_broker
from ..services.deletion import DeletionService
from ..services.errors import PermissionDenied, Unauthenticated, ValidationFailed
from ..services.grant import GrantService
from ..services.memory import MemoryService
from ..services.outbox import OutboxService
from ..services.proposal import ProposalService
from ..services.skill import SkillService
from ..services.profile import ProfileService
from ..services.canvas import CanvasService
from ..services.sync import SyncService
from ..services.workspace import WorkspaceService
from ..services.terminal import TerminalService
from ..services.git_service import GitService
from ..services.preview import PreviewService
from ..services.preview_sources import PreviewSourceService
from ..services.orchestrator_lease import OrchestratorLeaseService
from ..services.agent_teams import AgentTeamService
from ..services.hitl import HitlInterruptService
from ..services.kanban import KanbanService
from ..services.model_catalog import ModelCatalog

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
    profiles: ProfileService
    canvas: CanvasService
    sync: SyncService
    # 18 工程代码工作台
    workspaces: WorkspaceService
    terminals: TerminalService
    git: GitService
    previews: PreviewService
    preview_sources: PreviewSourceService
    orchestrator_leases: OrchestratorLeaseService
    # 19 单Agent内部团队与逐节点模型配置
    teams: AgentTeamService
    # 需求12 Human-in-the-loop 执行中断/恢复（跨 Task/Agent Team/lease 三个执行面）
    hitl: HitlInterruptService
    kanban: KanbanService
    # 统一能力网关（补齐包1）：能力裁决唯一入口；默认 None，
    # 仅 get_services 装配（直接构造 Services 的旧测试不受影响）。
    capability: CapabilityBroker | None = None


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    session = request.app.state.session_maker()
    try:
        yield session
    finally:
        session.close()


get_session = get_db


def get_services(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Services:
    audit = AuditService(db)
    grants = GrantService(db, audit)
    budget_svc = BudgetService(db, audit)
    # A single WorkspaceService instance is shared by the terminal, Git and
    # preview services so they all consult the same authorization boundary.
    workspaces = WorkspaceService(db, audit)
    previews = PreviewService(db, workspaces)
    return Services(
        session=db,
        audit=audit,
        auth=AuthService(db, audit, environment=settings.environment, local_token=settings.local_token),
        proposals=ProposalService(db, audit),
        grants=grants,
        memory=MemoryService(db, grants, audit),
        deletion=DeletionService(db, audit),
        budget=budget_svc,
        outbox=OutboxService(db, audit),
        skills=SkillService(db, audit),
        agents=AgentService(db, audit),
        profiles=ProfileService(db, audit),
        canvas=CanvasService(db, audit, budget=budget_svc, grants=grants),
        sync=SyncService(db),
        workspaces=workspaces,
        terminals=TerminalService(db, workspaces),
        git=GitService(db, workspaces),
        previews=previews,
        preview_sources=PreviewSourceService(db, workspaces, previews, audit=audit),
        orchestrator_leases=OrchestratorLeaseService(db, audit),
        teams=AgentTeamService(
            db, audit, budget=budget_svc, settings=settings,
            catalog=ModelCatalog(settings=settings),
        ),
        # HITL 复用同一个 audit 哈希链：暂停与决策都要留痕。
        hitl=HitlInterruptService(db, audit),
        # 任务看板：红带的预算阈值取自同一个 budget_svc 实例，避免两处各算一份
        kanban=KanbanService(db, audit, budget=budget_svc),
        # 统一能力网关（补齐包1）：本机能力与跨 agent 能力的唯一裁决入口，
        # RBAC/automation 四档/五级/四元组授予/档位在此多路取最严。
        capability=build_capability_broker(db, audit, settings),
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

    After the identity check the (read) transaction opened by these dependency
    queries is committed immediately. SQLAlchemy keeps that transaction open
    for the rest of the request, and upgrading a SQLite/WAL *snapshot* to a
    write fails right away with ``database is locked`` (the busy handler is not
    invoked for a stale-snapshot upgrade) whenever another request committed in
    between — which surfaced as sporadic 500s on /tree and /git/status. Ending
    the auth transaction here means the handler starts a fresh transaction
    after all dependency reads.
    """
    token = _bearer_token(request)
    if token:
        actor = svc.auth.service_actor(token)
        svc.session.commit()
        return actor

    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        row = svc.auth.verify_session(session_id)
        svc.session.commit()
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
