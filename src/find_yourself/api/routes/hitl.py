"""Human-in-the-loop 中断/ 恢复 HTTP 接口（需求 12）。

* ``POST /api/hitl/interrupts``                       —— 执行暂停，登记待决中断
* ``GET  /api/hitl/interrupts``                       —— 列出待决中断（人的工作台）
* ``GET  /api/hitl/interrupts/{id}``                  —— 取单个中断（含已决策）
* ``GET  /api/hitl/executions/{execution_id}/interrupt`` —— 该执行此刻是否卡住
* ``POST /api/hitl/interrupts/{id}/decision``         —— 提交决策，恢复执行

为什么单独一个 router 而不是挂到 ``tasks`` / ``agent-teams`` 下面：HITL 挂在
**执行面**上，而执行面不止一个——Task、Agent Team、orchestrator lease 都会暂停。
塞进任何一个业务 router 都会让另外两个面找不到它，也会诱使后续代码把「等人的
中断」误当成「某个业务实体的附属状态」。这里只依赖 :class:`Actor` 与 session，
不依赖任何业务实体，因此对所有执行面一视同仁。

写操作一律 ``csrf_protected``；决策额外在服务层 ``require_owner()``，
执行体不能给自己放行。服务从统一容器 ``Services.hitl`` 取，与其余 20 个服务一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import Services, csrf_protected, get_actor, get_services
from ...services.actor import Actor
from ...services.errors import DomainError

router = APIRouter(prefix="/api/hitl", tags=["hitl"])


class InterruptBody(BaseModel):
    execution_id: str = Field(min_length=1, max_length=200)
    checkpoint: str = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)
    #: 接受 ``["approve", "cancel"]`` 或 ``[{"value":..,"label":..}]``。
    options: list[Any] = Field(min_length=1)
    reason: str = ""
    timeout_seconds: int | None = Field(default=None, gt=0)


class DecisionBody(BaseModel):
    decision: str = Field(min_length=1, max_length=64)
    resolution: dict[str, Any] | None = None
    expected_version: int | None = Field(default=None, ge=1)


def _translate(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/interrupts", status_code=201)
async def create_interrupt(
    body: InterruptBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """登记一次暂停。执行体（service 身份）与owner 都可调用。"""
    try:
        view = svc.hitl.interrupt(
            actor,
            body.execution_id,
            body.checkpoint,
            context=body.context,
            options=body.options,
            reason=body.reason,
            timeout_seconds=body.timeout_seconds,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view


@router.get("/interrupts")
async def list_interrupts(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    execution_id: str | None = None,
) -> dict:
    """待决中断列表——「等我拍板」的工作台视图。"""
    items = svc.hitl.list_pending(actor, execution_id=execution_id)
    svc.session.commit()
    return {"count": len(items), "items": items}


@router.get("/interrupts/{interrupt_id}")
async def get_interrupt(
    interrupt_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    try:
        return svc.hitl.get(actor, interrupt_id)
    except DomainError as exc:
        raise _translate(exc) from exc


@router.get("/executions/{execution_id}/interrupt")
async def execution_interrupt(
    execution_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict:
    """该执行此刻是否卡在某个检查点。没卡则 ``pending=False``。"""
    try:
        row = svc.hitl.pending(actor, execution_id)
    except DomainError as exc:
        raise _translate(exc) from exc
    svc.session.commit()
    return {
        "execution_id": execution_id,
        "pending": row is not None,
        "interrupt": row,
    }


@router.post("/interrupts/{interrupt_id}/decision")
async def decide_interrupt(
    interrupt_id: str,
    body: DecisionBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict:
    """提交决策，恢复（或取消）执行。仅 owner 可拍板。"""
    try:
        view = svc.hitl.decide(
            actor,
            interrupt_id,
            body.decision,
            resolution=body.resolution,
            expected_version=body.expected_version,
        )
        svc.session.commit()
    except DomainError as exc:
        svc.session.rollback()
        raise _translate(exc) from exc
    return view
