"""数码小屋玩法 HTTP surface (W2 · 玩法循环 / 背景探险 / 日常系统).

端点（任务书 §1.6）::

    GET  /api/cabin/save            读存档 + 结算离线收益 + 同步探险点状态
    PUT  /api/cabin/save            仅写玩家偏好（服务端权威：数值字段一律 422）
    POST /api/cabin/save/action     唯一的状态变更入口（服务端结算权威）
    GET  /api/cabin/gameplay/meta   数值表/蓝图/事件/任务（前端与测试的单一真源）

**没有** ``POST /api/cabin/save`` 式的「直接改数值」通道：材料、金币、亲密度、
小屋等级只能由 ``/action`` 的结算产生。前端只渲染 ``/action`` 返回的结算明细
（``gained`` / ``feedback`` / ``quest_progress``），本地不做任何结算——
这是任务书 §3「服务端权威」与总纲第 3 节诚实原则的共同形态。

鉴权：读需已验证会话；写走 ``csrf_protected``；服务身份一律 403
（``CabinGameplayService._require_owner``）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import csrf_protected, get_actor, get_db
from ...services.actor import Actor
from ...services.cabin_gameplay import CabinGameplayService

router = APIRouter(prefix="/api/cabin", tags=["cabin"])


class SavePutBody(BaseModel):
    # 这里**不**用 extra="forbid"：客户端若提交 coins/materials 等结算字段，
    # 我们要让它进入服务层拿到明确的 `cabin_server_authoritative`（而不是被
    # Pydantic 提前拦成含糊的 validation_failed）。因此这里放开 extra，
    # 由服务层的 forbidden 白名单做权威判定并给出可读报错。
    model_config = ConfigDict(extra="allow")

    settings: dict[str, Any] | None = Field(
        default=None, description="玩家偏好（当前仅 active_theme）"
    )
    expected_version: int = Field(ge=0, description="乐观锁：0 = 尚无存档")


class ActionBody(BaseModel):
    # 白名单在服务层强制（GAMEPLAY_ACTIONS），非法 action → 422 cabin_unknown_action。
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=16)
    spot_id: str | None = Field(default=None, max_length=48)
    target: str | None = Field(default=None, max_length=48)
    quest_kind: str | None = Field(default=None, max_length=16)
    quest_id: str | None = Field(default=None, max_length=48)
    theme: str | None = Field(default=None, max_length=16)
    person_name: str = Field(default="小人", max_length=16)
    personality: str | None = Field(default=None, max_length=16)


@router.get("/save")
async def get_save(
    theme: str = "forest",
    person_name: str = "小人",
    personality: str | None = None,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """读取存档。首次访问会创建新档并发放起步材料。"""
    return CabinGameplayService(db).get_save(
        actor, theme=theme, person_name=person_name, personality=personality
    )


@router.put("/save")
async def put_save(
    body: SavePutBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """仅写玩家偏好。写材料/金币/亲密度等结算字段会被 422 拒绝。"""
    # model_dump() 含 extra 字段，一并交给服务层的 forbidden 判定。
    payload = body.model_dump()
    return CabinGameplayService(db).put_save(actor, payload, body.expected_version)


@router.post("/save/action")
async def save_action(
    body: ActionBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """执行一次玩法动作并返回**完整结算明细**（+什么、为什么）。"""
    return CabinGameplayService(db).perform_action(
        actor,
        action=body.action,
        spot_id=body.spot_id,
        target=body.target,
        quest_kind=body.quest_kind,
        quest_id=body.quest_id,
        person_name=body.person_name,
        personality=body.personality,
    )


@router.get("/gameplay/meta")
async def gameplay_meta(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """玩法静态元数据（数值表 / 蓝图 / 事件 / 任务 / 上限）。"""
    return CabinGameplayService(db).meta(actor)
