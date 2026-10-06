"""A2A HTTP surface: Agent Card discovery and JSON-RPC endpoint (G7/A01).

入站派发（§八 C 打通 · 补齐包3）：``message/send`` 经**统一调度中心**
（``services.scheduler``）的 ``a2a.inbound`` worker 真实派发：

* ``a2a_upstream_url`` 防御式读取（``getattr``，字段可能由包1 后合并到
  ``config.py``），**未配置时保持既有诚实边界**：``-32001 upstream_not_configured``；
* 已配置时：入站任务同池调度（统一路由/优先级/并发上限/回收/状态回传），
  执行体为 ``runtime.local_agents.LocalAgentsHarness``（服务身份 Actor、隔离
  工件沙箱、深度约束、上下文隔离），``tasks/get`` 可轮询调度任务状态。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...adapters.a2a import (
    AGENT_CARD_PATH,
    A2AInboundError,
    JSONRPC_PATH,
    A2ADispatcher,
    build_agent_card,
    message_text,
)
from ...db.models import Task
from ..deps import Services, csrf_protected, get_services, get_settings
from ...config import Settings
from ...services.actor import Actor

router = APIRouter(tags=["a2a"])

#: 入站 worker 在统一调度中心里的标识（与内部 agent、外部成品 agent 同池）。
A2A_INBOUND_WORKER_ID = "a2a.inbound"

#: 入站 worker 的能力标签（DispatchRequest.requested_capability 精确钉住）。
A2A_INBOUND_CAPABILITY = "a2a-inbound"

#: 入站任务经调度中心执行的超时（秒）。
A2A_INBOUND_TIMEOUT_SECONDS = 60.0


def _get_harness():
    """入站执行体（进程内缓存）：隔离工件沙箱 + 深度/预算约束。"""
    from ...runtime.local_agents import LocalAgentsConfig, LocalAgentsHarness

    global _HARNESS
    try:
        return _HARNESS
    except NameError:
        _HARNESS = LocalAgentsHarness(LocalAgentsConfig())
        return _HARNESS


def _service_actor() -> Actor:
    """入站任务的执行身份：受限服务身份（agent 类，绑定 work 域）。"""
    return Actor.service("a2a-inbound", "agent", domains=["work"])


def _inbound_worker_executor(req) -> dict:
    """调度中心 worker 执行体：LocalAgentsHarness 真实执行入站 goal。"""
    from ...db.types import utcnow

    goal = str(req.payload.get("goal") or "").strip()
    if req.report is not None:
        req.report("progress", {"stage": "accepted", "goal_chars": len(goal)})
    trace = _get_harness().run_subagent_task(
        _service_actor(),
        parent_task_id=f"a2a-inbound:{req.task_id}",
        goal=goal,
        depth=1,
    )
    return {
        "success": bool(trace.success),
        "error": trace.error,
        "artifact_ids": list(trace.artifact_ids),
        "citations": list(trace.citations),
        "finished_at": utcnow().isoformat(),
    }


def ensure_inbound_worker(scheduler=None) -> None:
    """把 A2A 入站 worker 登记进统一调度中心（幂等）。"""
    from ...services.scheduler import CHANNEL_A2A, scheduler as default_scheduler

    sch = scheduler or default_scheduler
    if sch.get_worker(A2A_INBOUND_WORKER_ID) is not None:
        return
    sch.register_simple_worker(
        A2A_INBOUND_WORKER_ID,
        CHANNEL_A2A,
        _inbound_worker_executor,
        max_parallel=2,
        tags=(A2A_INBOUND_CAPABILITY,),
        description="A2A 入站 message/send 执行器（LocalAgentsHarness 隔离沙箱）",
    )


def build_inbound_handler(scheduler=None):
    """构造入站派发 handler：统一调度中心 submit_and_wait -> A2A Task。"""
    from ...adapters.a2a import A2A_TASK_STATES as STATE_MAP
    from ...services.scheduler import (
        CHANNEL_A2A,
        DispatchRequest,
        NoRouteError,
        scheduler as default_scheduler,
    )

    sch = scheduler or default_scheduler

    def handler(params: dict) -> dict:
        # worker 注册由路由层 ensure_inbound_worker 负责；本 handler 只提交。
        # 无 worker / worker 被注销时诚实抛 A2AInboundError（-32001）。
        message = params.get("message") or {}
        goal = message_text(message).strip()
        if not goal:
            raise A2AInboundError("message.parts 里没有可执行的文本内容")
        req = DispatchRequest(
            channel=CHANNEL_A2A,
            action="a2a.message/send",
            requested_capability=A2A_INBOUND_CAPABILITY,
            payload={"goal": goal},
            timeout_seconds=A2A_INBOUND_TIMEOUT_SECONDS,
            meta={"source": "a2a", "role": str(message.get("role") or "")},
        )
        try:
            record = sch.submit_and_wait(req, timeout=A2A_INBOUND_TIMEOUT_SECONDS)
        except NoRouteError as exc:
            raise A2AInboundError(f"A2A 入站 worker 未注册或不可达：{exc}") from exc
        task: dict = {
            "id": record.task_id,
            "contextId": f"fy-scheduler:{record.task_id}",
            "status": {"state": STATE_MAP.get(record.status, "unknown")},
        }
        if record.status == "succeeded" and isinstance(record.result, dict):
            artifacts = record.result.get("artifact_ids") or []
            if artifacts:
                task["artifacts"] = [
                    {"artifactId": a, "name": "local-agent-trace"} for a in artifacts
                ]
        if record.error:
            task["status"]["message"] = {
                "role": "agent",
                "parts": [{"kind": "text", "text": record.error[:500]}],
            }
        return task

    return handler


def _scheduler_task_view(task_id: str):
    """把调度中心任务映射成 tasks/get 的返回形状（DB 任务查不到时兜底）。"""
    from ...services.scheduler import scheduler as default_scheduler

    record = default_scheduler.get_task(task_id)
    if record is None:
        return None
    view = {"id": record.task_id, "status": {"state": record.status},
            "title": f"a2a:{record.action}"}
    if record.error:
        view["status"]["message"] = {
            "role": "agent", "parts": [{"kind": "text", "text": record.error[:500]}],
        }
    return view


@router.get(AGENT_CARD_PATH)
async def agent_card(settings: Settings = Depends(get_settings)) -> dict:
    # Public card advertises capabilities only; no secrets.
    return build_agent_card(
        public_url=settings.public_url,
        agent_name="Find Yourself",
        version="0.1.0",
        description="Owner-personal agent surface; A2A-compatible discovery.",
        skills=[],
    )


@router.post(JSONRPC_PATH)
async def a2a_jsonrpc(request: Request,
                       actor: Actor = Depends(csrf_protected),
                       svc: Services = Depends(get_services),
                       settings: Settings = Depends(get_settings)) -> dict:
    body = await request.json()
    # Task lookup/cancel against local tasks, owner-scoped by the resolved actor.
    def lookup(task_id: str):
        t = svc.session.get(Task, task_id)
        if t is None:
            # §八 C：入站派发产生的调度中心任务也可轮询。
            return _scheduler_task_view(task_id)
        if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
            return None
        return {"id": t.id, "status": {"state": t.status}, "title": t.goal}

    def cancel(task_id: str) -> bool:
        t = svc.session.get(Task, task_id)
        if t is None or t.status in ("completed", "cancelled", "failed"):
            return False
        if actor.subject_type == "owner" and t.owner_id != actor.owner_id:
            return False
        t.status = "cancelled"
        svc.session.commit()
        return True

    # §八 C：a2a_upstream_url 防御式读取（字段可能尚未由包1 合并）；
    # 配置了上游才打通入站派发（经统一调度中心），未配置保持诚实 -32001。
    upstream = getattr(settings, "a2a_upstream_url", None)
    if upstream:
        # 入站派发打通：worker 预先登记进统一调度中心（幂等）。
        ensure_inbound_worker()
    dispatcher = A2ADispatcher(
        upstream_configured=bool(upstream), draining=False,
        task_lookup=lookup, cancel_task=cancel,
        dispatch_handler=build_inbound_handler() if upstream else None,
    )
    return dispatcher.dispatch(body)


@router.get("/a2a/health")
async def a2a_health() -> dict:
    return {"status": "ok", "protocol": "a2a", "version": "0.3.0"}
