"""个性化像素角色 HTTP surface (W11 · 个性化像素角色生成系统).

* ``POST /api/avatar/generate``    —— 画像 → 角色包（本地生成，落草稿档案）
* ``PUT  /api/avatar/confirm``     —— 草稿 → 已确认 + 「像不像自己」自评 + 设为专属小人
* ``GET  /api/avatar/me``          —— 读当前档案（含矩阵，前端直接渲染）
* ``POST /api/avatar/share-card``  —— 720×960 分享卡渲染数据（只含勾选字段）
* ``GET  /api/avatar/house``       —— 小屋消费：已确认且已设为专属的那份（小屋场景用）

Auth model: 读走 ``get_actor``，写额外过 ``csrf_protected``（CSRF + 同源 Origin），
服务层强制 owner-only —— service 身份写入 403；``owner_id`` 只从认证层取，
请求体里的同名字段**无授权效力**（FROZEN_CONTRACT §2/§3.2）。

诚实：画像缺项不会被静默补全成「你的数据」，引擎在 ``advisory`` 里如实标注
「待补画像」；徽章白名单外的字段直接 422，不静默忽略。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...services.avatar_gen import SHARE_BADGE_FIELDS
from ...services.avatar_profile import AvatarProfileService
from ...services.actor import Actor
from ..deps import csrf_protected, get_actor, get_db

router = APIRouter(prefix="/api/avatar", tags=["avatar"])


class GenerateBody(BaseModel):
    # extra="forbid": 多余字段是客户端 bug，宁可报错也别让它「看起来生效了」
    model_config = ConfigDict(extra="forbid")

    portrait: dict[str, Any] = Field(
        default_factory=dict,
        description="用户逐项同意后的画像（缺项由引擎走中性默认并在 advisory 标注）",
    )
    overrides: dict[str, Any] | None = Field(
        default=None,
        description="微调滑杆值：hair_style/hair_tone/outfit/mouth/eye/hue_shift",
    )


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, description="乐观锁：必须等于当前档案版本")
    likeness_score: int | None = Field(default=None, ge=1, le=10, description="像不像自己 1~10")
    likeness_note: str | None = Field(default=None, max_length=200, description="一句感想")
    is_house_avatar: bool = Field(default=True, description="是否替换小屋默认小人")


class ShareCardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    badges: list[str] = Field(
        default_factory=list,
        description=f"要上卡的字段（白名单：{sorted(SHARE_BADGE_FIELDS)}）；未勾选一律不上卡",
    )
    display_name: str | None = Field(default=None, max_length=40)
    overrides: dict[str, Any] | None = None


@router.post("/generate")
async def generate_avatar(
    body: GenerateBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """画像 → 像素角色包。重复调用是 upsert，不会堆出多个角色。"""
    return AvatarProfileService(db).generate(
        actor, portrait=body.portrait, overrides=body.overrides
    )


@router.put("/confirm")
async def confirm_avatar(
    body: ConfirmBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """确认角色（草稿 → 已确认），可一并记录自评与设为小屋专属小人。"""
    return AvatarProfileService(db).confirm(
        actor,
        expected_version=body.expected_version,
        likeness_score=body.likeness_score,
        likeness_note=body.likeness_note,
        is_house_avatar=body.is_house_avatar,
    )


@router.get("/me")
async def get_my_avatar(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """当前档案（含矩阵）。未生成过返回 404。"""
    return AvatarProfileService(db).me(actor)


@router.post("/share-card")
async def make_share_card(
    body: ShareCardBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """生成分享卡渲染数据。必须已确认；只含用户勾选的字段。"""
    return AvatarProfileService(db).share_card(
        actor, badges=body.badges, display_name=body.display_name, overrides=body.overrides
    )


@router.get("/house")
async def get_house_avatar(
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """小屋场景用：已确认且已设为专属的那一份；否则 404 让前端回退默认小人。"""
    return AvatarProfileService(db).house_avatar(actor)
