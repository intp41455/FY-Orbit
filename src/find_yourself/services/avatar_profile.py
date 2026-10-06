"""个性化像素角色档案持久化 (W11 · avatar_profiles).

``services/avatar_gen.py`` 是**纯函数**的映射 + 合成引擎（无 IO、无 random），
本模块负责「把引擎产物存下来 / 读出来 / 管状态流转」，两者职责严格分开。
包6 · A-命理画像-02 起额外承担**语义层映射**（纯函数、无 IO）：
charts 画像 → portrait（:func:`chart_to_portrait`）→ 五维性格 + 五行占比 +
游戏角色映射（:func:`personality_dimensions`），随档案包一并下发（`personality`
键，加性字段，不影响既有契约）。

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
            # 包6 · A-命理画像-02：画像→性格维度→游戏角色映射（加性字段）
            "personality": personality_dimensions(row.portrait),
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


# --------------------------------------------------------------------------- #
# 包6 · A-命理画像-02 · 画像 → 性格维度 → 游戏角色映射
#
# 定位：charts 画像（DeterministicChartEngine 的 ChartResult，见
# :func:`chart_to_portrait`）→ portrait 字段 → 五维性格分 + 五行占比 →
# 游戏角色原型（archetype/party_role/playstyle）。全部**纯函数、确定性、
# 离线**：同画像必然同映射（与 avatar_gen 引擎同一纪律）；缺项走中性默认 50
# 并在 `advisory` 里如实列出，绝不编造「画像里没有的个性」。
# 视觉参数仍归 `avatar_gen`（引擎独占视觉映射）；本层只出**语义**映射，
# 前端/游戏层据此渲染 3D 星图角色与玩法提示。
# --------------------------------------------------------------------------- #

PERSONALITY_MAPPING_VERSION = "1"

PERSONALITY_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("extraversion", "外向性"),
    ("agreeableness", "宜人性"),
    ("conscientiousness", "尽责性"),
    ("emotional_stability", "情绪稳定性"),
    ("openness", "开放性"),
)

ELEMENT_LABELS: dict[str, str] = {
    "wood": "木", "fire": "火", "earth": "土", "metal": "金", "water": "水",
}
_BAZI_ELEMENT_TO_KEY = {v: k for k, v in ELEMENT_LABELS.items()}

#: 星座元素分组（中英文名都能匹配——engine 的 ZODIAC_SIGNS 是「白羊座 (Aries)」式）
_ZODIAC_ELEMENT_NAMES: dict[str, tuple[str, ...]] = {
    "fire": ("白羊", "狮子", "射手", "aries", "leo", "sagittarius"),
    "earth": ("金牛", "处女", "摩羯", "taurus", "virgo", "capricorn"),
    "air": ("双子", "天秤", "水瓶", "gemini", "libra", "aquarius"),
    "water": ("巨蟹", "天蝎", "双鱼", "cancer", "scorpio", "pisces"),
}
#: 西方四元素 → 五行模型（风/气类归木，取中医「风入木」的对应；火土水同名直映）
_WESTERN_ELEMENT_TO_KEY: dict[str, str] = {
    "fire": "fire", "earth": "earth", "air": "wood", "water": "water",
}

#: 游戏角色原型表（key → 中文名 / 队伍定位 / 成长提示）
ARCHETYPES: dict[str, dict[str, str]] = {
    "mage": {"name": "法师", "party_role": "远程输出", "growth_hint": "保持好奇心，把灵感变成可复用的东西"},
    "healer": {"name": "治疗者", "party_role": "辅助支援", "growth_hint": "照顾他人时也给自己留一条回复线"},
    "scholar": {"name": "学者", "party_role": "知识/制造", "growth_hint": "把研究切成可交付的小块，避免无限打磨"},
    "warrior": {"name": "战士", "party_role": "前排突击", "growth_hint": "先倾听再冲锋，火力会更准"},
    "guardian": {"name": "守护者", "party_role": "坦克/坚守", "growth_hint": "稳定是资产，偶尔也留一点即兴空间"},
    "ranger": {"name": "游侠", "party_role": "侦察/游走", "growth_hint": "自由与承诺可以并存，试着选定一个据点"},
    "wanderer": {"name": "旅人", "party_role": "自由人", "growth_hint": "画像信息越多，角色定位越准——先补全命理画像吧"},
}


def _zodiac_element(sign: str) -> str | None:
    text = (sign or "").strip().lower()
    if not text:
        return None
    for element, names in _ZODIAC_ELEMENT_NAMES.items():
        if any(name in text for name in names):
            return element
    return None


def _mbti_letters(mbti: str) -> list[str] | None:
    text = (mbti or "").strip().upper()
    if len(text) != 4:
        return None
    pairs = (("E", "I"), ("S", "N"), ("T", "F"), ("J", "P"))
    letters: list[str] = []
    for i, (a, b) in enumerate(pairs):
        if text[i] not in (a, b):
            return None
        letters.append(text[i])
    return letters


def _clamp(value: float, lo: float = 5.0, hi: float = 95.0) -> float:
    return round(max(lo, min(hi, value)), 1)


def chart_to_portrait(chart: Any) -> dict[str, str]:
    """charts 画像（ChartResult 鸭子类型）→ avatar portrait 字段子集。

    只取 PORTRAIT_FIELDS 里与命理相关的键；缺项**省略**（不写空串），
    由 :func:`personality_dimensions` 的 advisory 如实上报。
    """
    system = getattr(chart, "system", "")
    data = getattr(chart, "computed_data", None) or {}
    portrait: dict[str, str] = {}
    if system == "bazi":
        day_master = str(data.get("day_master") or "").strip()
        day_element = str(data.get("day_master_element") or "").strip()
        if day_master:
            portrait["bazi_day_master"] = day_master
        if day_element:
            portrait["bazi_element"] = day_element
    elif system == "western":
        planets = data.get("planets") or {}
        for key, planet in (("sun_sign", "Sun"), ("moon_sign", "Moon")):
            sign = str((planets.get(planet) or {}).get("sign") or "").strip()
            if sign:
                portrait[key] = sign
        asc = str((data.get("ascendant") or {}).get("sign") or "").strip()
        if asc:
            portrait["asc_sign"] = asc
    return portrait


def personality_dimensions(portrait: dict[str, str] | None) -> dict[str, Any]:
    """画像 → 五维性格（0-100）+ 五行占比 + 游戏角色映射（确定性纯函数）。

    缺项走中性默认 50 并记入 ``advisory``；同画像恒同输出。
    """
    dims: dict[str, float] = {key: 50.0 for key, _label in PERSONALITY_DIMENSIONS}
    balance: dict[str, float] = {key: 20.0 for key in ELEMENT_LABELS}
    advisory: list[str] = []
    portrait = portrait or {}

    def bump(dim: str, delta: float) -> None:
        dims[dim] = dims.get(dim, 50.0) + delta

    mbti_letters = _mbti_letters(str(portrait.get("mbti") or ""))
    if mbti_letters:
        letter_deltas = {
            "E": ("extraversion", +22.0), "I": ("extraversion", -16.0),
            "N": ("openness", +18.0), "S": ("conscientiousness", +8.0),
            "T": ("agreeableness", -14.0), "F": ("agreeableness", +16.0),
            "J": ("conscientiousness", +18.0), "P": ("openness", +14.0),
        }
        for letter in mbti_letters:
            dim, delta = letter_deltas[letter]
            bump(dim, delta)
        if "J" in mbti_letters:
            bump("emotional_stability", +6.0)
        if "P" in mbti_letters:
            bump("conscientiousness", -8.0)
    elif portrait.get("mbti"):
        advisory.append("mbti_format_invalid")

    bazi_element = _BAZI_ELEMENT_TO_KEY.get(str(portrait.get("bazi_element") or "").strip())
    if bazi_element:
        # bazi_element 即日主五行（chart_to_portrait 从 day_master_element 取值）
        balance[bazi_element] += 40.0
    elif portrait.get("bazi_element"):
        advisory.append("bazi_element_unknown")
    if not portrait.get("bazi_element") and not portrait.get("bazi_day_master"):
        advisory.append("missing:bazi_element")

    element_dim_nudges = {
        "wood": [("openness", +10.0)],
        "fire": [("extraversion", +12.0)],
        "earth": [("conscientiousness", +10.0), ("emotional_stability", +8.0)],
        "metal": [("conscientiousness", +12.0), ("agreeableness", -4.0)],
        "water": [("openness", +10.0), ("extraversion", -4.0)],
    }
    if bazi_element is not None:
        for dim, delta in element_dim_nudges[bazi_element]:
            bump(dim, delta)

    sun_element = _zodiac_element(str(portrait.get("sun_sign") or ""))
    moon_element = _zodiac_element(str(portrait.get("moon_sign") or ""))
    asc_element = _zodiac_element(str(portrait.get("asc_sign") or ""))
    if sun_element is None and portrait.get("sun_sign"):
        advisory.append("sun_sign_unknown")
    if not sun_element and not moon_element and not asc_element:
        advisory.append("missing:western_signs")

    zodiac_nudges = {
        "fire": [("extraversion", +8.0)],
        "earth": [("conscientiousness", +6.0)],
        "air": [("openness", +8.0)],
        "water": [("agreeableness", +8.0)],
    }
    for element, weight in ((sun_element, 1.0), (moon_element, 0.6), (asc_element, 0.4)):
        if element is None:
            continue
        balance[_WESTERN_ELEMENT_TO_KEY[element]] += 15.0 * weight
        for dim, delta in zodiac_nudges[element]:
            bump(dim, delta * weight)
    if moon_element == "earth":
        bump("emotional_stability", +10.0 * 0.6)
    elif moon_element == "water":
        bump("emotional_stability", -8.0 * 0.6)

    for key in dims:
        dims[key] = _clamp(dims[key])
    for key in balance:
        balance[key] = round(max(0.0, min(100.0, balance[key])), 1)
    dominant_element = max(balance, key=lambda k: balance[k]) if any(balance.values()) else None

    archetype_key = _archetype_key(dims, portrait, bazi_element)
    archetype = dict(ARCHETYPES[archetype_key])
    archetype["key"] = archetype_key
    return {
        "mapping_version": PERSONALITY_MAPPING_VERSION,
        "dimensions": dims,
        "dimension_labels": {key: label for key, label in PERSONALITY_DIMENSIONS},
        "element_balance": balance,
        "element_labels": dict(ELEMENT_LABELS),
        "dominant_element": dominant_element,
        "dominant_element_label": ELEMENT_LABELS.get(dominant_element or "", ""),
        "role_mapping": {
            "archetype": archetype["name"],
            "archetype_key": archetype["key"],
            "party_role": archetype["party_role"],
            "growth_hint": archetype["growth_hint"],
            "playstyle": {
                "combat": dims["extraversion"],
                "support": dims["agreeableness"],
                "crafting": dims["conscientiousness"],
                "survival": dims["emotional_stability"],
                "exploration": dims["openness"],
            },
        },
        "advisory": advisory,
    }


def _archetype_key(dims: dict[str, float], portrait: dict[str, str], bazi_element: str | None) -> str:
    """角色原型规则（按优先级，首个命中即用；全缺项 → wanderer）。"""
    if not portrait:
        return "wanderer"
    if dims["openness"] >= 65:
        return "mage"
    if dims["agreeableness"] >= 65:
        return "healer"
    if dims["conscientiousness"] >= 65:
        return "scholar"
    if dims["extraversion"] >= 65:
        return "warrior"
    if dims["emotional_stability"] >= 65:
        return "guardian"
    if bazi_element is not None:
        return {"wood": "scholar", "fire": "warrior", "earth": "guardian",
                "metal": "scholar", "water": "mage"}.get(bazi_element, "ranger")
    return "ranger"
