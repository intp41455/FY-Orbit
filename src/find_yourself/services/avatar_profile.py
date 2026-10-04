"""个性化像素角色档案持久化 (W11 · avatar_profiles).

``services/avatar_gen.py`` 是**纯函数**的映射 + 合成引擎（无 IO、无 random），
本模块只负责「把引擎产物存下来 / 读出来 / 管状态流转」，两者职责严格分开。

四条路由的服务层（任务书 §2.4）：

* ``generate``   —— upsert 草稿。**每次调用都重算**，因为引擎是确定性的：
  同画像必然同结果，重算不会丢用户的后续微调（微调在 ``overrides`` 里）。
* ``confirm``    —— 草稿 → 已确认，带乐观锁；可同时记录「像不像自己」自评。
* ``me``        —— 读当前档案（含重算出的矩阵，供前端直接渲染）。
* ``share_card``—— 只吃**已勾选**的徽章字段，未勾选一律不上卡。

安全（FROZEN_CONTRACT §1/§3.2）：所有读写按 ``owner_id`` 隔离；``owner_id``
**只**来自 :class:`Actor`，请求体里的同名字段无任何授权效力。跨 owner 读他人
档案返回 404（不泄露存在性），service 身份写入返回 403。

诚实：本模块**不静默降级**。画像缺项由引擎走中性默认并回 ``advisory``，
调用方必须把它透给前端；``is_house_avatar=True`` 但 ``state='draft'`` 这类
自相矛盾的请求直接 409，不「顺手修正」。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.types import utcnow
from ..db.workbench_models import AvatarProfile
from . import avatar_gen as ag
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

#: 画像输入白名单：只有这些键会进引擎，多余键明确报错（防「以为传了其实没生效」）。
PORTRAIT_FIELDS: frozenset[str] = frozenset(
    {
        "mbti", "bazi_element", "bazi_day_master",
        "sun_sign", "moon_sign", "asc_sign",
        "name", "nickname", "mood", "gender", "age_band",
    }
)

#: 自评上限：防止把整本书塞进「一句感想」。
MAX_LIKENESS_NOTE_CHARS = 200


def _clean_portrait(raw: Any) -> dict[str, Any]:
    """校验并清洗画像输入。未知键 → 明确报错，绝不静默丢弃。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationFailed("avatar_invalid_portrait", "portrait must be a JSON object")
    unknown = sorted(set(raw) - PORTRAIT_FIELDS)
    if unknown:
        raise ValidationFailed(
            "avatar_unknown_portrait_field",
            f"Unknown portrait fields: {unknown}. Allowed: {sorted(PORTRAIT_FIELDS)}",
        )
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValidationFailed(
                "avatar_invalid_portrait", f"portrait.{key} must be a string, got {type(value).__name__}"
            )
        text = value.strip()
        if text:
            clean[key] = text
    return clean


class AvatarProfileService:
    """``avatar_profiles`` 的读写门面。所有方法都要求 owner 身份。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # -- 内部 ------------------------------------------------------------
    def _require_owner(self, actor: Actor) -> str:
        actor.require_authenticated()
        if actor.subject_type != "owner":
            raise PermissionDenied(
                "owner_only", "Only the authenticated owner session can manage a pixel avatar", 403
            )
        return actor.owner_id

    def _row(self, owner_id: str) -> AvatarProfile | None:
        return self.db.execute(
            select(AvatarProfile).where(AvatarProfile.owner_id == owner_id)
        ).scalar_one_or_none()

    def _render(self, row: AvatarProfile) -> dict[str, Any]:
        """把库里那一行 + 引擎重算 → 前端要的完整角色包。"""
        avatar = ag.build_avatar(row.portrait, overrides=row.overrides or None)
        return {
            "id": row.id,
            "state": row.state,
            "owner_id": row.owner_id,
            "portrait": row.portrait,
            "params": avatar["params"],
            "base_signature": row.base_signature,
            "overrides": row.overrides,
            "fingerprint": row.fingerprint,
            "params_fingerprint": row.params_fingerprint,
            "engine_version": row.engine_version,
            "likeness_score": row.likeness_score,
            "likeness_note": row.likeness_note,
            "is_house_avatar": row.is_house_avatar,
            "version": row.version,
            "avatar": _pack(avatar),
            "advisory": avatar["advisory"],
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    # -- 公开 API --------------------------------------------------------
    def generate(
        self,
        actor: Actor,
        *,
        portrait: Any,
        overrides: Any = None,
    ) -> dict[str, Any]:
        """生成/更新草稿档案（upsert）。返回完整角色包。"""
        owner_id = self._require_owner(actor)
        clean = _clean_portrait(portrait)
        clean_overrides = _clean_overrides(overrides)

        avatar = ag.build_avatar(clean, overrides=clean_overrides or None)

        row = self._row(owner_id)
        if row is None:
            # ID 由服务端生成（FROZEN_CONTRACT §5）：客户端永不自选 id
            row = AvatarProfile(id=f"av_{uuid.uuid4().hex}", owner_id=owner_id)
            self.db.add(row)
        row.state = "draft"
        row.portrait = clean
        row.params = avatar["params"]
        row.base_signature = avatar["base_signature"]
        row.overrides = clean_overrides
        row.fingerprint = avatar["fingerprint"]
        row.params_fingerprint = avatar["params_fingerprint"]
        row.engine_version = avatar["engine_version"]
        # 重新生成 ⇒ 之前的自评与「专属小人」标记失效（评的是旧角色）
        row.likeness_score = None
        row.likeness_note = ""
        row.is_house_avatar = False
        row.version = (row.version or 0) + 1
        row.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._render(row)

    def confirm(
        self,
        actor: Actor,
        *,
        expected_version: int,
        likeness_score: int | None = None,
        likeness_note: str | None = None,
        is_house_avatar: bool = True,
    ) -> dict[str, Any]:
        """草稿 → 已确认。带乐观锁；可一并记录自评与设为小屋专属小人。"""
        owner_id = self._require_owner(actor)
        row = self._row(owner_id)
        if row is None:
            raise NotFound("avatar_not_found", "No pixel avatar generated yet")
        if row.version != expected_version:
            raise Conflict(
                "avatar_version_conflict",
                f"Avatar is at version {row.version}, client sent {expected_version}",
            )
        if likeness_score is not None and not (1 <= likeness_score <= 10):
            raise ValidationFailed(
                "avatar_bad_likeness", f"likeness_score must be 1..10, got {likeness_score}"
            )
        note = (likeness_note or "").strip()
        if len(note) > MAX_LIKENESS_NOTE_CHARS:
            raise ValidationFailed(
                "avatar_note_too_long",
                f"likeness_note exceeds {MAX_LIKENESS_NOTE_CHARS} characters",
            )

        row.state = "confirmed"
        if likeness_score is not None:
            row.likeness_score = likeness_score
        if likeness_note is not None:
            row.likeness_note = note
        row.is_house_avatar = bool(is_house_avatar)
        row.version += 1
        row.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(row)
        return self._render(row)

    def me(self, actor: Actor) -> dict[str, Any]:
        """读当前档案。未生成过 → 404（不返回空壳，前端据此显示「还没生成」）。"""
        owner_id = self._require_owner(actor)
        row = self._row(owner_id)
        if row is None:
            raise NotFound("avatar_not_found", "No pixel avatar generated yet")
        return self._render(row)

    def share_card(
        self,
        actor: Actor,
        *,
        badges: list[str],
        display_name: str | None = None,
        overrides: Any = None,
    ) -> dict[str, Any]:
        """生成分享卡渲染数据。**必须已有档案**（不允许拿任意画像直接出卡）。"""
        owner_id = self._require_owner(actor)
        row = self._row(owner_id)
        if row is None:
            raise NotFound("avatar_not_found", "No pixel avatar generated yet")
        if row.state != "confirmed":
            raise Conflict(
                "avatar_not_confirmed",
                "Confirm the pixel avatar before producing a share card",
            )
        clean_overrides = _clean_overrides(overrides) or dict(row.overrides or {})
        card = ag.build_share_card(
            row.portrait, badges, display_name=display_name, overrides=clean_overrides or None
        )
        card["state"] = row.state
        card["params_fingerprint"] = row.params_fingerprint
        return card

    def house_avatar(self, actor: Actor) -> dict[str, Any]:
        """小屋消费：只返回**已确认且已设为专属**的那一份，否则 404。

        小屋场景据此回退到自己的默认小人，绝不把草稿渲染进场景。
        """
        owner_id = self._require_owner(actor)
        row = self._row(owner_id)
        if row is None or row.state != "confirmed" or not row.is_house_avatar:
            raise NotFound("avatar_no_house_avatar", "No confirmed house avatar set")
        avatar = ag.build_avatar(row.portrait, overrides=row.overrides or None)
        return {
            "fingerprint": row.params_fingerprint,
            "matrix": avatar["matrix"],
            "layers": avatar["layers"],
            "palette": avatar["palette"],
            "char_keys": ag.CHAR_KEYS,
            "char_palette": avatar["char_palette"],
            "width": avatar["width"],
            "height": avatar["height"],
            "labels": avatar["params"]["labels"],
        }


def _clean_overrides(overrides: Any) -> dict[str, Any]:
    """校验微调覆盖。字段与取值合法性交给引擎（它有真正的白名单）。"""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValidationFailed("avatar_invalid_overrides", "overrides must be a JSON object")
    if len(overrides) > 8:
        raise ValidationFailed("avatar_too_many_overrides", "overrides holds more than 8 fields")
    return {str(k): v for k, v in overrides.items() if v is not None}


def _pack(avatar: dict[str, Any]) -> dict[str, Any]:
    """角色包里前端真正需要的部分（矩阵 / 分层 / 调色板 / 语义表 / 参数）。

    `params` 必须随包下发：前端微调面板按 `profile.avatar.params[key]`
    读取每个维度的当前取值（hair_style / hair_tone / outfit / mouth / eye）。
    缺它时 `avatar.params` 为 undefined，一进微调渲染段即抛
    `Cannot read properties of undefined (reading 'hair_style')` 白屏。
    TS 类型 `AvatarPackage.params` 与前端测试 fixture 都声明它恒存在，
    因此这里是**契约字段**，不是可选装饰。
    """
    return {
        "width": avatar["width"],
        "height": avatar["height"],
        "matrix": avatar["matrix"],
        "layers": avatar["layers"],
        "palette": avatar["palette"],
        "char_keys": ag.CHAR_KEYS,
        "char_palette": avatar["char_palette"],
        "param_space_size": avatar["param_space_size"],
        "tuned": avatar["tuned"],
        "params": avatar["params"],
    }
