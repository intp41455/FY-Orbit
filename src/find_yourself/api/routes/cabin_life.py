"""B 包生活模拟 HTTP surface（`/api/cabin/life/*`）。

端点::

    GET    /api/cabin/life/save     完整面板快照（存档 + HUD + 社交/经营/制作/采集）
    POST   /api/cabin/life/save     建新档 {theme, day?}
    PUT    /api/cabin/life/save     只写玩家偏好（数值字段一律 422）
    DELETE /api/cabin/life/save     删档（重开一周目）
    POST   /api/cabin/life/action   **唯一**的状态变更入口（服务端权威）
    GET    /api/cabin/life/meta     主题清单 + 动作白名单（前端单一真源）

路由前缀说明（说明书 §2.1）：本模块用 `/api/cabin/life/*`，与 W1 的
`/api/cabin/furniture`、`/api/cabin/interiors/{house_id}` 和 W2 的
`/api/cabin/save`、`/api/cabin/save/action`、`/api/cabin/gameplay/meta`
**全部无交集** —— 尤其是 `/save`：W2 已经占了裸 `/api/cabin/save`，本模块
刻意多一层 `life/`，不去碰它。

服务端权威：没有「直接改数值」的通道。金币、背包、好感、技能经验只能由
`/action` 里的白名单动作经 `cabin_life` 纯函数算出，`PUT` 提交这些字段会被 422。

鉴权：读需已验证会话；写走 `csrf_protected`；服务身份一律 403。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..deps import csrf_protected, get_actor, get_db
from ...services.actor import Actor
from ...services.cabin_life import clock as clock_mod
from ...services.cabin_life import interaction, npcs, themes
from ...services.cabin_life.service import ACTIONS, WRITABLE_SETTINGS, LifeService

router = APIRouter(prefix="/api/cabin/life", tags=["cabin-life"])


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = Field(min_length=1, max_length=32)
    day: int = Field(default=1, ge=1, le=100000)


class SettingsPutBody(BaseModel):
    # 放开 extra：客户端若提交 coins/bag 等数值字段，要让它进服务层拿到明确的
    # `life_server_authoritative`，而不是被 Pydantic 提前拦成含糊的 validation_failed。
    model_config = ConfigDict(extra="allow")

    settings: dict[str, Any] | None = None
    expected_version: int | None = Field(default=None, ge=0)


class ActionBody(BaseModel):
    # 白名单在服务层强制（ACTIONS）；这里仍放开 extra，让多余字段进服务层统一判。
    model_config = ConfigDict(extra="allow")

    action: str = Field(min_length=1, max_length=24)
    node_id: str | None = Field(default=None, max_length=64)
    recipe_id: str | None = Field(default=None, max_length=64)
    good_id: str | None = Field(default=None, max_length=64)
    npc_id: str | None = Field(default=None, max_length=64)
    gift_id: str | None = Field(default=None, max_length=64)
    quest_id: str | None = Field(default=None, max_length=64)
    giver: str | None = Field(default=None, max_length=64)
    price: int | None = None
    qty: int | None = None


@router.get("/save")
async def get_save(
    player_x: int | None = Query(default=None, description="玩家格坐标 X（可选）"),
    player_y: int | None = Query(default=None, description="玩家格坐标 Y（可选）"),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """读取完整面板快照。玩家坐标给了才会算采集距离（不给则一律 in_range=false）。"""
    tile = None
    if player_x is not None and player_y is not None:
        tile = (int(player_x), int(player_y))
    return LifeService(db).snapshot(actor, player_tile=tile)


@router.post("/save")
async def post_save(
    body: CreateBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """建新档。已存在 → 422（不覆盖现有进度）。"""
    return LifeService(db).create(actor, theme=body.theme, day=body.day)


@router.put("/save")
async def put_save(
    body: SettingsPutBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """只写玩家偏好。提交 coins/bag/affinity 等数值字段 → 422 `life_server_authoritative`。"""
    return LifeService(db).put_settings(actor, body.model_dump())


@router.delete("/save")
async def delete_save(
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """删档。显式动作，不做静默清理。"""
    return LifeService(db).delete(actor)


@router.post("/action")
async def life_action(
    body: ActionBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """唯一的状态变更入口。未知 action → 422；规则层拒绝 → 422 带具体原因。"""
    return LifeService(db).action(actor, body.model_dump())


@router.get("/meta")
async def life_meta(actor: Actor = Depends(get_actor)) -> dict:
    """主题清单 + 动作白名单 + 可写偏好字段（前端与测试的单一真源）。

    常量直接引用规则模块，不在此处拷贝数字 —— 拷贝就会漂移。
    """
    return {
        "themes": [
            {"id": tid, "label": themes.theme_label(tid)} for tid in themes.theme_ids()
        ],
        "actions": sorted(ACTIONS),
        "writable_settings": sorted(WRITABLE_SETTINGS),
        "max_hearts": npcs.MAX_HEARTS,
        "max_gifts_per_day": npcs.MAX_GIFTS_PER_DAY,
        "daily_node_limit": interaction.DAILY_NODE_LIMIT,
        "daily_reset_minute": clock_mod.DAILY_RESET_MINUTE,
    }
