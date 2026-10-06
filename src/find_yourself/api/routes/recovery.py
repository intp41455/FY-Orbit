"""T6-G 恢复中心 HTTP 面（可发现性：让用户找得到断点在哪、怎么继续）。

* ``GET  /api/recovery/interruptions``            —— 扫描 open 中断 + 可续作性
* ``POST /api/recovery/interruptions/{id}/resume`` —— 续作（S5 需显式确认）
* ``GET  /api/recovery/playbook``                 —— 六类中断处置矩阵（S1–S6）

前端按 ``resume.policy`` 渲染：auto=一键继续；confirm=弹确认框（文案必须
说明「换供应商后成本与输出分布会变」）；manual=指路专用恢复流（快照回滚）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..deps import Services, csrf_protected, get_actor, get_services, get_settings
from ...config import Settings
from ...services.actor import Actor
from ...services.recovery import RecoveryService, playbook_view

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_provider_change: bool = False


@router.get("/interruptions")
def list_interruptions(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
    settings: Settings = Depends(get_settings),
) -> dict:
    """扫描 open 中断事件（每条含策略与可续作指针）。"""
    service = RecoveryService(svc.session, svc.audit, settings=settings)
    return service.scan(actor)


@router.post("/interruptions/{event_id}/resume")
def resume_interruption(
    event_id: str,
    body: ResumeBody,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
    settings: Settings = Depends(get_settings),
) -> dict:
    """续作一条中断。confirm 策略未确认 → 409 ``resume_confirmation_required``。"""
    service = RecoveryService(svc.session, svc.audit, settings=settings)
    return service.resume(actor, event_id, confirm_provider_change=body.confirm_provider_change)


@router.get("/playbook")
def get_playbook() -> dict:
    """六类中断（S1–S6）的统一处置矩阵——恢复中心 UI 的策略真源。"""
    return playbook_view()
