"""W10-B · GUI 自动化权限门 HTTP surface.

* ``GET /api/automation/permissions`` — 当前档位 + full 档 TTL 剩余（owner 可读，游客会话亦可）。
* ``PUT /api/automation/permissions`` — 切换档位（owner + CSRF；full 必须带 TTL）。

安全语义与 :mod:`find_yourself.services.automation` 一致：
默认 off；full 档短 TTL、到期自动回落；每次变更写审计链。本路由**不执行**任何
截图/点击——它只是用户在设置面板里开关权限的入口；真正的系统调用仍由
harness 工具在权限门放行后发生。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import Services, csrf_protected, get_actor, get_db, get_services
from ...services.actor import Actor
from ...services.automation import (
    MAX_FULL_TTL_SECONDS,
    MODE_FULL,
    MODE_ORDER,
    get_permissions,
)
from ...services.errors import ValidationFailed

router = APIRouter(prefix="/api/automation", tags=["automation"])


class PermissionPutBody(BaseModel):
    mode: str = Field(pattern="^(off|readonly|safe|full)$")
    #: full 档必填（秒）；其它档忽略。
    ttl_seconds: int | None = Field(default=None, ge=60, le=MAX_FULL_TTL_SECONDS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/permissions")
def read_permissions(
    actor: Actor = Depends(get_actor),
) -> dict:
    pm = get_permissions()
    cur = pm.current()
    remaining = None
    if cur.mode == MODE_FULL and cur.expires_at is not None:
        remaining = max(0, int((cur.expires_at - _now()).total_seconds()))
    return {
        "mode": cur.mode,
        "expires_at": cur.expires_at.isoformat() if cur.expires_at else None,
        "ttl_remaining_seconds": remaining,
        "max_ttl_seconds": MAX_FULL_TTL_SECONDS,
        "modes": sorted(MODE_ORDER, key=lambda m: MODE_ORDER[m]),
        "honesty_note": (
            "GUI automation 默认关闭；完全控制（点击/打字）为短时效注入权，"
            "到期自动回落为关闭，重启服务后回到默认关闭状态。"
        ),
    }


@router.put("/permissions")
def set_permissions(
    body: PermissionPutBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
    svc: Services = Depends(get_services),
) -> dict:
    actor.require_owner()
    pm = get_permissions()
    # set_mode 内部会校验 full 必须带 TTL，并写本地审计事件。
    new_mode = pm.set_mode(body.mode, ttl_seconds=body.ttl_seconds)
    # 追加到主审计链（与本地 automation_audit.jsonl 互为印证）。
    svc.audit.append(
        actor,
        action="automation.permission_change",
        target=f"mode:{body.mode}",
        details={
            "mode": body.mode,
            "ttl_seconds": body.ttl_seconds,
            "expires_at": new_mode.expires_at.isoformat() if new_mode.expires_at else None,
        },
    )
    db.commit()
    # 回读（含 TTL 剩余）。
    cur = pm.current()
    remaining = None
    if cur.mode == MODE_FULL and cur.expires_at is not None:
        remaining = max(0, int((cur.expires_at - _now()).total_seconds()))
    return {
        "mode": cur.mode,
        "expires_at": cur.expires_at.isoformat() if cur.expires_at else None,
        "ttl_remaining_seconds": remaining,
    }
