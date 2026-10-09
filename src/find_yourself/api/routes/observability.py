"""可观测与成本聚合 HTTP 路由（A-可观测-02/03 · P2，A-成本仪表盘-02 · P3）。

范式与 ``api/routes/kanban.py`` 一致：

* 每个 owner 读路由挂 ``get_actor``，改动路由挂 ``csrf_protected``；
* 任何 body 都不接受 ``owner_id`` / ``role``——身份一律由服务端解析；
* 错误统一走 ``services/errors.py`` 的 DomainError 信封（全局处理器转成
  ``{"error": {code, message, details}}``）；
* 改动路由**必须显式 commit**（本仓约定：service 只 flush，commit 由路由做）。

🔴 **本模块不做显式注册。** ``api/routes/__init__.py:152`` 的
``discover_local_routes()`` 在模块加载时（:193）扫描本包，把任何带
``router = APIRouter(...)`` 且文件名不以 ``_`` 开头的模块自动挂上。所以本文件
只要满足那两个条件就可达，不需要碰 ``routes/__init__.py``（那是跨包锁）。

🔴 **不在 ``Services`` 上加字段。** ``api/deps.py`` 同为跨包锁，所以服务在路由里
就地构造（照 P17 的 ``hitl_vote.py`` 做法）：``LogService`` /
``ObservabilityService`` 都只吃已有的 ``svc.session`` / ``svc.audit`` /
``svc.budget``，不引入新的共享装配。

端点一览：

===================================== ====== ==================================
端点方法用途
===================================== ====== ==================================
``GET  /logs``                     读     五维筛选 + 分面计数 + 序号水位
``GET  /logs/stream``              读     SSE 实时日志流
``GET  /logs/{seq}/trace``         读     错误跳 trace
``GET  /performance``              读     整体指标
``GET  /performance/agents``       读     单 Agent 统计
``GET  /cost/progress``            读     进度条（当前步/总步/预计剩余 + 费用）
===================================== ====== ==================================
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ...services.actor import Actor
from ...services.observability import (
    MAX_STREAM_FRAMES,
    MAX_STREAM_IDLE_POLLS,
    ObservabilityService,
)
from ..deps import Services, get_actor, get_services

router = APIRouter(prefix="/api/observability", tags=["observability"])

#: SSE 轮询间隔的下界（秒）。比它更密的轮询只会把数据库打满而不让面板更实时。
MIN_POLL_INTERVAL = 0.5


def _svc(svc: Services) -> ObservabilityService:
    """按请求装配可观测服务。

    🔴 ``api/deps.py`` 是跨包锁，不加字段。``LogService`` 与
    ``ObservabilityService`` 都只需要已有的 ``session`` / ``audit`` / ``budget``，
    所以就地构造即可——三个端点共用同一实例，避免同一请求里两次装配两个 audit 视图。
    """

    return ObservabilityService(
        svc.session, audit=svc.audit, budget=svc.budget
    )


# ---------------------------------------------------------------------------
# 需求 A-可观测-02 · 日志监控面板
# ---------------------------------------------------------------------------
@router.get("/logs", summary="日志监控面板：五维筛选 + 分面 + 序号水位")
async def logs_panel(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    level: str | None = Query(default=None, description="error/warning/info/debug/change"),
    actor_filter: str | None = Query(default=None, alias="actor", max_length=200),
    surface: str | None = Query(default=None, max_length=64),
    project: str | None = Query(default=None, max_length=200),
    since_seq: int | None = Query(default=None, description="序号轴起点（含）"),
    until_seq: int | None = Query(default=None, description="序号轴终点（含）"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """日志列表。时间轴是 ``seq`` 而非墙钟——``audit_events`` 没有时间戳列。"""
    return _svc(svc).logs_panel(
        actor,
        level=level,
        actor_filter=actor_filter,
        surface=surface,
        project=project,
        since_seq=since_seq,
        until_seq=until_seq,
        limit=limit,
        offset=offset,
    )


@router.get("/logs/stream", summary="实时日志流（SSE）")
async def logs_stream(
    request: Request,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    level: str | None = Query(default=None),
    since_seq: int | None = Query(default=None, description="从该序号之后推；缺省=只推今后新增"),
    poll_interval: float = Query(default=2.0, ge=MIN_POLL_INTERVAL, le=30.0),
    max_frames: int = Query(
        default=MAX_STREAM_FRAMES, ge=1, le=MAX_STREAM_FRAMES,
        description=(
            "推够多少帧后主动收尾。0 不是「无限」而是「不发」——"
            "无限流会让一个只读不消费的客户把连接永久钉住"
        ),
    ),
max_idle_polls: int = Query(
        default=MAX_STREAM_IDLE_POLLS, ge=1, le=600,
        description="连续多少个周期没有新帧就收尾；没有它，一直无事件时流不会结束",
    ),
) -> StreamingResponse:
    """把新到的审计帧按 SSE 推给面板。

    断线续传读 ``Last-Event-ID``，与仓内其他 SSE 端点（``tasks.py:168``、
    ``bus.py:107``）同一套约定。**非法值一律退化成 0，不 500**：一个坏的头部
    不该把整条流打断。

    ``max_frames`` 让流可以**有界**：一次性补齐历史、或测试里等一个确定的终点
    时用得到。不设上界的话，客户端不消费时这个生成器会一直转下去。
    """
    raw = request.headers.get("Last-Event-ID") or request.query_params.get(
        "since_seq"
    )
    if raw is not None:
        try:
            since_seq = max(0, int(raw))
        except (TypeError, ValueError):
            since_seq = 0

    service = _svc(svc)

    async def gen():
        async for frame in service.stream_logs(
            actor,
            since_seq=since_seq,
            level=level,
            poll_interval=poll_interval,
            max_frames=max_frames,
            max_idle_polls=max_idle_polls,
        ):
            # 客户端断开时立刻收手，别把生成器晾在那儿空转。
            if await request.is_disconnected():
                return
            yield frame

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/{seq}/trace", summary="错误跳 trace")
async def log_trace(
    seq: int,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """从一条审计帧跳到它的关联事件。

    响应里的 ``basis`` 说明**凭什么**关联的：``message``（``details.message_id``）、
    ``entity``（task/canvas/team id），或 ``none``（关联不上，不补假关联）。
    """
    return _svc(svc).trace_for(actor, seq)


# ---------------------------------------------------------------------------
# 需求 A-可观测-03 · 性能监控
# ---------------------------------------------------------------------------
@router.get("/performance", summary="整体性能指标")
async def performance(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    """整体指标。只统计有真实墙钟时间戳的表；样本不足的百分位为 ``null``。"""
    return _svc(svc).performance(actor, days=days)


@router.get("/performance/agents", summary="单 Agent 统计")
async def agent_stats(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    team_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """逐个 Agent 的状态 / 步数 / 预算 / 活动窗口。owner 收敛走 team_definitions。"""
    return _svc(svc).agent_stats(actor, team_id=team_id, limit=limit)


# ---------------------------------------------------------------------------
# 需求 A-成本仪表盘-02 · 进度条
# ---------------------------------------------------------------------------
@router.get("/cost/progress", summary="进度条：当前步 / 总步 / 预计剩余 + 费用占用")
async def cost_progress(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    task_id: str | None = Query(default=None, max_length=64),
    team_id: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """进度条数据。

    ``remaining`` 是**区间 + 假设**，不是单一预测：上界余量精确可算，时间区间
    由实测步均耗时折算、下界为 0，样本不足时时间字段为 ``null``。
    """
    return _svc(svc).cost_progress(actor, task_id=task_id, team_id=team_id)


__all__ = ["router"]
