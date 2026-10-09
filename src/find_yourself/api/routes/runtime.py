"""A-Agent运行时-01 · 单循环内核 HTTP 面（把 LangGraph 主干接线到产品 API）。

* ``GET  /api/runtime/hooks/events`` —— 25 事件权威清单（wired/reserved 如实标注）
* ``GET  /api/runtime/hooks``        —— 当前注册的 hook（进程内，重启即清）
* ``POST /api/runtime/run``          —— 走 SingleLoopKernel 执行一个任务
  （前置 hook 可阻断；阻断/完成/失败全量审计挂帧；检查点落盘可续）
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...runtime.hooks import HOOK_EVENTS, WIRED_EVENTS, get_default_hook_bus
from ...runtime.kernel import SingleLoopKernel
from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_services

router = APIRouter(prefix="/api/runtime", tags=["runtime-kernel"])


class KernelRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000)
    route: str = Field(default="", max_length=40)
    domain: str = Field(default="personal", max_length=16)
    max_steps: int = Field(default=8, ge=1, le=64)
    task_id: str = Field(default="", max_length=200)


@router.get("/hooks/events")
def list_hook_events() -> dict:
    """生命周期事件权威清单——wired=True 才是已真实接线的事件。"""
    return {
        "count": len(HOOK_EVENTS),
        "events": [
            {"name": name, "wired": name in WIRED_EVENTS} for name in HOOK_EVENTS
        ],
    }


@router.get("/hooks")
def list_registered_hooks() -> dict:
    """当前进程内注册的 hook（诚实边界：重启即清空，持久化注册表是后续切片）。"""
    bus = get_default_hook_bus()
    registered = [
        listener
        for name in HOOK_EVENTS
        for listener in bus.listeners(name)
    ]
    return {"count": len(registered), "items": registered}


@router.post("/run")
def kernel_run(
    body: KernelRunBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    """唯一执行主干的产品入口：前置 hook 可阻断；完成后凭 thread_id 可续。"""
    task_id = body.task_id or f"api-{uuid.uuid4().hex[:12]}"
    kernel = SingleLoopKernel(
        bus=get_default_hook_bus(), audit=svc.audit, actor=actor
    )
    result = kernel.run(
        {
            "task_id": task_id,
            "attempt": 1,
            "goal": body.goal,
            "domain": body.domain,
            "route": body.route,
            "budget_balance": 1.0,
            "max_steps": body.max_steps,
            "history": [{"role": "user", "content": body.goal}],
        },
        config={"configurable": {"thread_id": f"th-{task_id}"}},
    )
    svc.session.commit()
    return {
        "task_id": task_id,
        "thread_id": f"th-{task_id}",
        "status": result.status,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "error": result.error,
        "output": result.state.get("output"),
        "step_count": result.state.get("step_count"),
        "hook_trace": result.trace[-50:],
    }
