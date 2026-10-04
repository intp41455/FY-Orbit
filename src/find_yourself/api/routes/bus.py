"""W7 · Agent 通信总线 HTTP surface.

* ``POST   /api/bus/{room}/messages``         发消息（CSRF + Origin 门禁）
* ``GET    /api/bus/{room}/messages?after_id`` 拉消息（断线续传）
* ``GET    /api/bus/{room}/stream``            SSE 实时流（``after_id`` / ``Last-Event-ID`` 续传）
* ``GET    /api/bus/{room}/handoffs``          连线徽标计数（handoff 消息按边聚合）
* ``POST   /api/bus/{room}/context``          登记共享上下文（文件引用 / 文本片段）
* ``GET    /api/bus/{room}/context``          读共享上下文

房间：``global``（广播，仅 owner）| ``<task_id>``（任务 owner）|
``<team_id>``（团队 owner）| ``dm:<a>:<b>``（双方）。

``from_identity`` **不是请求字段** —— 它由服务端从 Actor 推导（owner 会话或
服务身份），请求体里带也不生效（本路由的 pydantic 模型里根本没有该字段）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...runtime.agent_bus import DM_PREFIX, GLOBAL_ROOM
from ...services.actor import Actor
from ...services.agent_teams import AgentTeamService
from ...services.audit import AuditService
from ...services.budget import BudgetService
from ...services.bus_service import MAX_REFS, AgentBusService
from ..deps import Services, csrf_protected, get_actor, get_services

router = APIRouter(prefix="/api/bus", tags=["agent-bus"])


class SendMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    kind: str = Field(default="text", max_length=16)
    refs: list[str] = Field(default_factory=list, max_length=MAX_REFS)
    #: 形如 ``agent:implementer``、``implementer`` 或成员职务名「编码专家」。
    mention: str | None = Field(default=None, max_length=200)
    max_tokens: int = Field(default=512, ge=1, le=8192)


class ContextEntryBody(BaseModel):
    kind: str = Field(default="text", max_length=16)
    title: str = Field(default="", max_length=200)
    ref: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=8000)


class AddContextBody(BaseModel):
    entries: list[ContextEntryBody] = Field(default_factory=list, max_length=20)


def _service(request: Request, svc: Services) -> AgentBusService:
    """构造总线服务。

    后台 Agent 回复要跨请求存活，所以它拿的是 ``app.state.session_maker``
    （自己开独立 session），而不是这个请求结束就会被关闭的会话。配套服务也
    必须在新 session 上重建 —— 复用请求里的 audit/budget 会指向一个已经随
    响应结束的会话。
    """
    session_maker = getattr(request.app.state, "session_maker", None)

    def teams_factory(new_session: Any) -> AgentTeamService:
        audit = AuditService(new_session)
        return AgentTeamService(new_session, audit, budget=BudgetService(new_session, audit))

    return AgentBusService(
        svc.session,
        session_maker=session_maker,
        teams_factory=teams_factory,
    )


@router.post("/{room}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    room: str,
    body: SendMessageBody,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    out = _service(request, svc).send(
        actor, room, content=body.content, kind=body.kind,
        refs=list(body.refs), mention=body.mention, max_tokens=body.max_tokens,
    )
    svc.session.commit()
    return out


@router.get("/{room}/messages")
async def list_messages(
    room: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, Any]:
    out = _service(request, svc).list_messages(actor, room, after_id=after_id, limit=limit)
    svc.session.commit()
    return out


@router.get("/{room}/stream")
async def stream_messages(
    room: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    after_id: int = Query(default=0, ge=0),
    max_events: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """SSE：只推送客户端还没看到的消息（``after_id`` 或 ``Last-Event-ID``）。

    ``max_events`` 留空（0）= 常连流，事件源断开后由 ``Last-Event-ID`` 续传；
    给定正整数则只推送这批事件就关闭连接，供一次性补拉与测试使用。
    """
    service = _service(request, svc)
    # 先做一次可见性校验：越权房间不会开流。
    service.resolve_room(actor, room)
    svc.session.commit()

    last_id = after_id or int(request.headers.get("last-event-id") or 0)

    async def gen():
        sent = 0
        async for msg in service.bus.subscribe(room, after_id=last_id):
            yield msg.to_sse()
            sent += 1
            if max_events and sent >= max_events:
                return

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{room}/handoffs")
async def handoff_counts(
    room: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    out = _service(request, svc).handoffs(actor, room)
    svc.session.commit()
    return out


@router.post("/{room}/context", status_code=status.HTTP_201_CREATED)
async def add_context(
    room: str,
    body: AddContextBody,
    request: Request,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    out = _service(request, svc).add_context(
        actor, room, [e.model_dump() for e in body.entries]
    )
    svc.session.commit()
    return out


@router.get("/{room}/context")
async def list_context(
    room: str,
    request: Request,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    out = _service(request, svc).list_context(actor, room)
    svc.session.commit()
    return out


@router.get("/rooms/schema")
async def room_schema(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """房间模型说明（前端据此构造房间键，不再自行约定第二套协议）。"""
    actor.require_authenticated()
    return {
        "global": GLOBAL_ROOM,
        "dm_prefix": DM_PREFIX,
        "kinds": ["global", "task", "team", "dm"],
        "message_kinds": ["text", "file_ref", "handoff", "system"],
        "from_identity_is_server_derived": True,
    }
