"""W11 · 个性化像素角色生成系统 — 画像 → 角色参数的确定性映射引擎 + 像素合成器.

战略定位（任务书 §1）：这是产品「病毒增长引擎」与「本地隐私卖点」的技术底座。

**隐私红线（本文件的最高约束，也是营销卖点）**：本模块是**纯规则引擎，不调用任何
模型、不发任何网络请求**。用户的画像（MBTI / 八字 / 星盘 / 姓名）**全程留在本机进程内**，
只参与本地确定性计算，绝不外发。分享卡数据也由调用方按「用户逐项勾选」的字段组装。

**确定性铁律**：同一画像输入必须产出**逐字节相同**的像素矩阵——引擎内禁止使用
``random`` / ``uuid`` / ``time`` 等不确定源；一切"随机"都走由输入指纹派生的
``mulberry32`` 式确定性 PRNG。这让「同一画像跨设备可复现」，也让单测能做字节级断言。

参数空间（任务书 §2.1 防重样要求 ≥10^6）：

===========  =====  ====================================================
维度          取值数  来源
===========  =====  ====================================================
hair_style     12    性格基调（J/P 剪影族 × 6）
hair_color      8    五行主色 + 明度层级
palette        10    八字五行主导色板（5 主色 + 中性/微调档）
eye             6    眼神细节（大五 mood / MBTI 倾向）
mouth           5    表情基调（T/F）
accessory      12    星盘日（头饰）
outfit          8    服装质感（MBTI 四维）
emblem         12    星座徽记（星盘日/月/升兜底）
texture         4    服装质感倾向
===========  =====  ====================================================
12 × 8 × 10 × 6 × 5 × 12 × 8 × 12 × 4 = 132,710,400 种组合，
远超任务书「≥10⁶ 防重样」红线（同画像必同角色，跨设备可复现）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .errors import ValidationFailed

# ======================================================================
# 常量表：全部写成不可变常量，便于单测逐项断言与主控评审
# ======================================================================

ENGINE_VERSION = "1.0.0-rules"
AVATAR_WIDTH = 24
AVATAR_HEIGHT = 48
MIN_PALETTE_COLORS = 16
BODY_HEAD_RATIO = 1.4  # 头身比 20:28 = 1:1.4，严格 2.5 头身（任务书 A2 / §3.2）

# --- 八字五行 → 主色调板 ------------------------------------------------
# 任务书 §2.1：金=白金冷调、木=青绿、水=蓝黑靛、火=红橙、土=赭黄
ELEMENT_PALETTES: dict[str, dict[str, Any]] = {
    "金": {
        "label": "金 · 白金冷调",
        "base": "#e8edf4", "shadow": "#b9c6d8", "accent": "#8fa3bf",
        "trim": "#d7c48a", "bg": "#f2f6fb", "skin": "#f0d9c4",
        # 服装质感倾向（下同：satin/matte/fur/linen/silk）
        "texture": "satin",
        "texture_label": "缎光冷冽",
    },
    "木": {
        "label": "木 · 青绿",
        "base": "#a8d8b0", "shadow": "#6fb078", "accent": "#3f7d4f",
        "trim": "#e3c46a", "bg": "#eef7ec", "skin": "#f2dcc6",
        "texture": "linen",
        "texture_label": "草木棉麻",
    },
    "水": {
        "label": "水 · 蓝黑靛",
        "base": "#7fa8d8", "shadow": "#4a6fa8", "accent": "#22345c",
        "trim": "#a9c8e8", "bg": "#eaf1fa", "skin": "#ecd5c0",
        "texture": "silk",
        "texture_label": "流水丝滑",
    },
    "火": {
        "label": "火 · 红橙",
        "base": "#f09a72", "shadow": "#c96742", "accent": "#8f3520",
        "trim": "#ffd28a", "bg": "#fdf0e8", "skin": "#f5d8bd",
        "texture": "satin",
        "texture_label": "炽热缎光",
    },
    "土": {
        "label": "土 · 赭黄",
        "base": "#d8b878", "shadow": "#a8894c", "accent": "#6f5527",
        "trim": "#f2dc9a", "bg": "#faf4e4", "skin": "#f0d2b0",
        "texture": "matte",
        "texture_label": "厚土哑光",
    },
}
NEUTRAL_ELEMENT = "土"  # 缺项中性默认

#: 五行主导判定顺序（同票数时按此顺序取，保证确定性）。
ELEMENT_TIE_ORDER: tuple[str, ...] = ("金", "木", "水", "火", "土")

# --- MBTI 四维 → 脸型/发型剪影/表情/站姿 --------------------------------
# 任务书 §2.1：E/I → 动态 vs 静谧；S/N → 脸型；T/F → 表情；J/P → 发型剪影
MBTI_SHAPE: dict[str, dict[str, Any]] = {
    "E": {"stride": "动态", "posture": "open", "lean": 1},
    "I": {"stride": "静谧", "posture": "closed", "lean": 0},
    "S": {"face": "圆润", "face_shape": "round"},
    "N": {"face": "清峭", "face_shape": "angled"},
    "T": {"mood": "沉静", "mouth": "flat"},
    "F": {"mood": "温柔", "mouth": "smile"},
    "J": {"hair": "利落", "hair_cut": "neat"},
    "P": {"hair": "蓬松", "hair_cut": "fluffy"},
}

# --- 星盘日/月/升 → 头饰 / 披风围巾 / 星座徽记 ----------------------------
# 12 星座符号像素化为 8×8 徽章（任务书 §2.1）
ZODIAC_EMBLEMS: tuple[str, ...] = (
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
)
ZODIAC_CN: dict[str, str] = {
    "aries": "白羊座", "taurus": "金牛座", "gemini": "双子座", "cancer": "巨蟹座",
    "leo": "狮子座", "virgo": "处女座", "libra": "天秤座", "scorpio": "天蝎座",
    "sagittarius": "射手座", "capricorn": "摩羯座", "aquarius": "水瓶座",
    "pisces": "双鱼座",
}
#: 英文星名 → 星座 id（用于解析 charts/engine.py 的 "Aries (白羊座)" 等输出）
ZODIAC_EN_TO_ID: dict[str, str] = {
    "aries": "aries", "taurus": "taurus", "gemini": "gemini", "cancer": "cancer",
    "leo": "leo", "virgo": "virgo", "libra": "libra", "scorpio": "scorpio",
    "sagittarius": "sagittarius", "capricorn": "capricorn",
    "aquarius": "aquarius", "pisces": "pisces",
}
#: 日/月/升 → 头饰（12 种，日主导）+ 披风围巾色带
SUN_HEADWEAR: dict[str, str] = {
    "aries": "circlet", "taurus": "ribbon", "gemini": "double_pin", "cancer": "hood",
    "leo": "crown", "virgo": "laurel", "libra": "bow", "scorpio": "veil",
    "sagittarius": "feather", "capricorn": "band", "aquarius": "halo", "pisces": "tiara",
}
MOON_CAPE: dict[str, str] = {
    "aries": "short_red", "taurus": "long_green", "gemini": "striped",
    "cancer": "soft_grey", "leo": "flowing_gold", "virgo": "neat_white",
    "libra": "silk_pink", "scorpio": "deep_plum", "sagittarius": "travel_blue",
    "capricorn": "heavy_brown", "aquarius": "sheer_cyan", "pisces": "misty_violet",
}
ASC_ACCENT: dict[str, str] = {
    "aries": "star_pin", "taurus": "gem_brooch", "gemini": "twin_charm",
    "cancer": "shell", "leo": "sun_badge", "virgo": "wheat_sprig",
    "libra": "balance_charm", "scorpio": "talisman", "sagittarius": "arrow_pin",
    "capricorn": "seal", "aquarius": "circuit_badge", "pisces": "drop_charm",
}
NEUTRAL_ZODIAC = "virgo"  # 缺项中性默认（最中性、装饰最少）

# --- 性格维度 → 眼神细节 / 随身小物 ------------------------------------
# 任务书 §2.1：性格维度 → 眼神细节、随身小物
EYE_STYLES: tuple[str, ...] = ("sparkle", "calm", "sharp", "gentle", "dreamy", "focused")
EYE_LABELS: dict[str, str] = {
    "sparkle": "高光闪烁", "calm": "沉静平和", "sharp": "锐利专注",
    "gentle": "温柔含光", "dreamy": "朦胧发呆", "focused": "凝神前视",
}
# 随身小物 ← 性格 mood（决策契约见 build_avatar：mood 优先，mood 缺失才退回 _hand_for(mbti)）。
# 键必须 ∈ MOOD_EYE_BIAS 的合法值（sunny/calm/melancholy）；过去错写成 curious/warm/...
# 导致 HAND_ITEMS.get(mood) 永远 miss、恒回退 lantern，性格随身小物功能形同虚设（G1-2b 修复）。
HAND_ITEMS: dict[str, str] = {
    "sunny": "flower",        # 晴朗外向 → 小花
    "calm": "tea_cup",        # 平和 → 茶杯
    "melancholy": "book",     # 沉静内省 → 书
}
HAND_ITEMS_DEFAULT = "lantern"

HAIR_STYLES: tuple[str, ...] = (
    "short_neat", "short_fluffy", "bob", "long_straight", "long_wavy",
    "ponytail", "twin_tail", "bun", "side_swept", "curly", "braid", "undercut",
)
#: 发型 → 剪影矩阵引用（前端渲染时按 id 取矩阵；此处只给 id 与描述）
HAIR_STYLE_LABELS: dict[str, str] = {
    "short_neat": "利落短发", "short_fluffy": "蓬松短发", "bob": "齐耳波波头",
    "long_straight": "顺直长发", "long_wavy": "波波长发", "ponytail": "马尾",
    "twin_tail": "双马尾", "bun": "丸子头", "side_swept": "侧分",
    "curly": "卷发", "braid": "编发", "undercut": "内侧剃短",
}
HAIR_TONES: tuple[str, ...] = (
    "ink", "chestnut", "gold", "auburn", "ash", "rose", "mint", "frost",
)
HAIR_TONE_HEX: dict[str, str] = {
    "ink": "#2b2f3a", "chestnut": "#6b4a32", "gold": "#c9a24a",
    "auburn": "#8f4b32", "ash": "#8b8f98", "rose": "#c07f8f",
    "mint": "#7fb69a", "frost": "#cfe0ee",
}
OUTFITS: tuple[str, ...] = (
    "tshirt", "knit", "coat", "robe", "dress", "hoodie", "vest", "cape",
)
OUTFIT_LABELS: dict[str, str] = {
    "tshirt": "短衫", "knit": "针织衫", "coat": "长外套", "robe": "长袍",
    "dress": "连衣裙", "hoodie": "连帽卫衣", "vest": "马甲", "cape": "斗篷",
}
MOUTH_STYLES: tuple[str, ...] = ("smile", "flat", "open_smile", "small", "grin")
MOUTH_LABELS: dict[str, str] = {
    "smile": "微笑", "flat": "平静", "open_smile": "开朗笑",
    "small": "浅笑", "grin": "咧嘴",
}
TEXTURES: tuple[str, ...] = ("satin", "matte", "fur", "linen")

# --- 防重样：参数空间容量（单测断言 ≥10^6）------------------------------
# 必须放在**所有维度常量之后**并按其真实长度相乘。早期版本写成字面量
# ``12*8*10*6*5*12*8*12*4``，与实际维度表脱钩后把 1327 万谎报成 1.33 亿。
# 现在从常量表推导，**结构上不可能再与实现漂移**。
PARAM_SPACE_SIZE = (
    len(HAIR_STYLES) * len(HAIR_TONES) * len(EYE_STYLES) * len(MOUTH_STYLES)
    * len(OUTFITS) * len(SUN_HEADWEAR) * len(ZODIAC_EMBLEMS) * len(TEXTURES)
)  # = 13_271_040，远超任务书 ≥10^6 红线

#: 三档情绪（可选画像字段 mood）→ 眼神细节偏移
MOOD_EYE_BIAS: dict[str, int] = {"sunny": 0, "calm": 1, "melancholy": 3}

#: 四档年龄（可选画像字段 age_band）→ 中文标签。
#:
#: **刻意不参与剪影**：任务书把「可选性别/年龄档」列为画像**输入项**，
#: 同时把「头身比 1:1.2 Q 版」列为**硬要求**。若让年龄档改变头身比，
#: 幼童档就得画成大头娃娃、成人档画成 1:3.5 —— 直接违反 Q 版硬要求；
#: 若按性别分剪影则是刻板印象。两者都不做。
#:
#: 因此这里只做**归一化白名单**（非法值降级为缺项），并在 ``advisory.notes``
#: 里如实告知用户「性别/年龄档已收到但不改剪影」，绝不假装它影响了角色。
AGE_BANDS: dict[str, str] = {
    "child": "未成年",
    "teen": "青少年",
    "adult": "成年",
    "senior": "银发",
}

#: 收集到但不参与造型的可选字段（用于 advisory 如实说明）
NON_SILHOUETTE_FIELDS: dict[str, str] = {
    "gender": "性别",
    "age_band": "年龄档",
}


# ======================================================================
# 确定性 PRNG（mulberry32，与前端 cabinPixels.mulberry32 同算法）
# ======================================================================

def _deterministic_rng(seed: int):
    """返回一个 0..1 的确定性 PRNG。禁止使用 Python ``random``（不确定）。"""
    a = seed & 0xFFFFFFFF

    def _next() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        t = (t ^ (t >> 15)) * (1 | t) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t) & 0xFFFFFFFF)) & 0xFFFFFFFF
        t = t ^ (t >> 14)
        return (t & 0xFFFFFFFF) / 4294967296

    return _next


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(hex_color: str) -> float:
    """WCAG 相对亮度（sRGB→线性），用于配色对比度自检。"""
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    """两色的 WCAG 对比度（1.0..21.0）。"""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 4)


def lightness_gap(a: str, b: str) -> float:
    """两色的相对亮度绝对差（0..1）——相邻色块明度差阈值检查用。"""
    return round(abs(_relative_luminance(a) - _relative_luminance(b)), 4)


#: 相邻色块最小明度差：低于此值视为「脏色」（任务书 §4 色彩和谐红线）。
MIN_LIGHTNESS_GAP = 0.08


#: **画布上真实贴邻**的色键对：只有这些对同时可见，色差过小才会糊成一片。
#:
#: 刻意**不含**同色系的明暗对（``skin``/``skin_shadow``、``hair``/``hair_shadow`` 等）——
#: 它们本就该相近，若纳入检查会把正常的明暗层次判成冲突并强行拉开，反而更难看。
PAINT_ADJACENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("skin", "hair_shadow"),
    ("skin", "eye_ink"),
    ("skin", "blush"),
    ("skin", "outfit_base"),
    ("hair", "eye_ink"),
    ("hair", "rim_light"),
    ("outfit_base", "outfit_shadow"),
    ("outfit_base", "trim"),
    ("outfit_shadow", "legwear"),
    ("cape", "cape_shadow"),
    ("legwear", "shoe"),
    ("shoe", "skin_shadow"),
    ("emblem_bg", "trim"),
    ("trim", "rim_light"),
    ("eye_ink", "eye_highlight"),
)


def check_palette_harmony(
    colors: dict[str, str] | list[str],
    *,
    min_gap: float = MIN_LIGHTNESS_GAP,
    pairs: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, Any]:
    """配色对比度自检：检查**画布上真实相邻**的色块是否满足最小明度差。

    两种入参：
      - ``list[str]``：按顺序两两相邻（通用序列校验）；
      - ``dict[str, str]``：需同时给 ``pairs``（或用 :data:`PAINT_ADJACENT_PAIRS`），
        只检查给定键对。这才是像素画真正需要的语义。

    返回 ``{"ok": bool, "violations": [...], "adjusted": dict|list}``。
    ``violations`` 非空时 ``adjusted`` 是自动微调后的色板（对冲突色提亮/压暗），
    保证「避免像素脏色」这一硬指标可被调用方消费，而不是只报错。
    """
    as_map = isinstance(colors, dict)
    work: dict[str, str] = dict(colors) if as_map else {}
    if as_map and pairs is None:
        pairs = PAINT_ADJACENT_PAIRS

    violations: list[dict[str, Any]] = []
    if as_map:
        for key_a, key_b in pairs or ():
            if key_a not in work or key_b not in work:
                continue
            gap = lightness_gap(work[key_a], work[key_b])
            if gap < min_gap:
                violations.append(
                    {"pair": [key_a, key_b], "colors": [work[key_a], work[key_b]],
                     "gap": gap, "min_gap": min_gap}
                )
                work[key_b] = _nudge_lightness(work[key_b], min_gap, toward="lighter")
        return {"ok": not violations, "violations": violations, "adjusted": work}

    seq = list(colors)  # type: ignore[arg-type]
    adjusted = list(seq)
    for i in range(len(seq) - 1):
        gap = lightness_gap(seq[i], seq[i + 1])
        if gap < min_gap:
            violations.append(
                {"index": i, "pair": [seq[i], seq[i + 1]], "gap": gap, "min_gap": min_gap}
            )
            adjusted[i + 1] = _nudge_lightness(seq[i + 1], min_gap, toward="lighter")
    return {"ok": not violations, "violations": violations, "adjusted": adjusted}


def _nudge_lightness(hex_color: str, target_gap: float, *, toward: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    step = 0.12 if toward == "lighter" else -0.12
    for _ in range(8):
        r = max(0, min(255, round(r * (1 + step))))
        g = max(0, min(255, round(g * (1 + step))))
        b = max(0, min(255, round(b * (1 + step))))
        cand = f"#{r:02x}{g:02x}{b:02x}"
        if lightness_gap(hex_color, cand) >= target_gap:
            return cand
    return hex_color


# ======================================================================
# 输入归一化 / 指纹
# ======================================================================

@dataclass(frozen=True)
class PortraitInput:
    """画像卡（任务书 §2.1 输入）。

    所有字段都是有则用、缺则走中性默认 + 记入 ``missing`` 诚实标注。
    """

    mbti: str | None = None                     # 4 字符，如 "INFJ"
    bazi_element: str | None = None             # 五行主导，如 "木"
    bazi_day_master: str | None = None          # 日主天干，如 "甲"
    sun_sign: str | None = None                 # 太阳星座 id，如 "leo"
    moon_sign: str | None = None                # 月亮星座 id
    asc_sign: str | None = None                 # 上升星座 id
    name: str | None = None
    mood: str | None = None                     # sunny | calm | melancholy
    gender: str | None = None                   # 可选，仅影响称呼不改剪影
    age_band: str | None = None                 # 可选，见 AGE_BANDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "mbti": self.mbti,
            "bazi_element": self.bazi_element,
            "bazi_day_master": self.bazi_day_master,
            "sun_sign": self.sun_sign,
            "moon_sign": self.moon_sign,
            "asc_sign": self.asc_sign,
            "name": self.name,
            "mood": self.mood,
            "gender": self.gender,
            "age_band": self.age_band,
        }


def _norm_mbti(raw: str | None) -> str | None:
    if not raw:
        return None
    up = raw.strip().upper()
    if len(up) != 4 or up[0] not in "EI" or up[1] not in "SN" or up[2] not in "TF" or up[3] not in "JP":
        return None
    return up


def _norm_sign(raw: str | None) -> str | None:
    """接受 "leo" / "Leo" / "狮子座" / "Leo (狮子座)" 等写法。"""
    if not raw:
        return None
    s = raw.strip()
    low = s.lower()
    for key in ZODIAC_EMBLEMS:
        if key in low:
            return key
    for en, sid in ZODIAC_EN_TO_ID.items():
        if en in low:
            return sid
    for sid, cn in ZODIAC_CN.items():
        if cn in s:
            return sid
    return None


def build_portrait(raw: dict[str, Any] | None) -> PortraitInput:
    """把任意客户端字段归一化为 :class:`PortraitInput`。非法值一律降级为缺项。"""
    raw = raw or {}
    element = raw.get("bazi_element")
    if element is not None and element not in ELEMENT_PALETTES:
        element = None
    mood = raw.get("mood")
    if mood is not None and mood not in MOOD_EYE_BIAS:
        mood = None
    name = raw.get("name")
    if name is not None:
        name = str(name)[:16]
    day_master = raw.get("bazi_day_master")
    if day_master is not None and day_master not in ("甲乙丙丁戊己庚辛壬癸"):
        day_master = None
    age_band = raw.get("age_band")
    if age_band is not None and age_band not in AGE_BANDS:
        age_band = None          # 非法档位一律降级为缺项，绝不猜
    return PortraitInput(
        mbti=_norm_mbti(raw.get("mbti")),
        bazi_element=element,
        bazi_day_master=day_master,
        sun_sign=_norm_sign(raw.get("sun_sign")),
        moon_sign=_norm_sign(raw.get("moon_sign")),
        asc_sign=_norm_sign(raw.get("asc_sign")),
        name=name,
        mood=mood,
        gender=raw.get("gender"),
        age_band=age_band,
    )


def fingerprint(portrait: PortraitInput) -> str:
    """画像 → 稳定指纹（SHA-256 前 16 位）。

    指纹只覆盖**参与生成的字段**（名称不参与，避免改名换角色误导用户；
    性别不参与，避免刻板剪影），因此同画像必同角色，跨设备可复现。
    """
    payload = {
        "mbti": portrait.mbti,
        "bazi_element": portrait.bazi_element,
        "bazi_day_master": portrait.bazi_day_master,
        "sun": portrait.sun_sign,
        "moon": portrait.moon_sign,
        "asc": portrait.asc_sign,
        "mood": portrait.mood,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ======================================================================
# 映射引擎：画像 → 角色参数
# ======================================================================

@dataclass
class AvatarParams:
    """角色参数集（确定性、可序列化、可作为指纹载体）。"""

    hair_style: str
    hair_tone: str
    palette_id: str
    eye: str
    mouth: str
    accessory: str
    outfit: str
    emblem: str
    texture: str
    height_scale: float
    colors: dict[str, str]
    labels: dict[str, str]
    sources: dict[str, str]  # 每个维度来自哪个画像字段（可回溯）
    missing: list[str] = field(default_factory=list)
    fingerprint: str = ""
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "hair_style": self.hair_style,
            "hair_tone": self.hair_tone,
            "palette_id": self.palette_id,
            "eye": self.eye,
            "mouth": self.mouth,
            "accessory": self.accessory,
            "outfit": self.outfit,
            "emblem": self.emblem,
            "texture": self.texture,
            "height_scale": self.height_scale,
            "colors": self.colors,
            "labels": self.labels,
            "sources": self.sources,
            "missing": self.missing,
            "fingerprint": self.fingerprint,
            "engine_version": self.engine_version,
        }

    def signature(self) -> dict[str, Any]:
        """只含「决定外观」的字段——单测断言同一画像签名逐字节相同。"""
        return {
            "hair_style": self.hair_style,
            "hair_tone": self.hair_tone,
            "palette_id": self.palette_id,
            "eye": self.eye,
            "mouth": self.mouth,
            "accessory": self.accessory,
            "outfit": self.outfit,
            "emblem": self.emblem,
            "texture": self.texture,
        }

    def params_fingerprint(self) -> str:
        """**角色参数指纹**（任务书 §2.3）：覆盖本方法算出的全部呈现字段。

        与 :func:`fingerprint`（画像指纹）的区别：

        - ``fingerprint`` 只覆盖画像，**微调不改变**它——它回答「这是谁的底稿」；
        - ``params_fingerprint`` 覆盖**当前呈现**，微调会改变它——它回答「现在长什么样」。

        两者都随包返回：底稿指纹用于「还原 AI 底稿」判定，参数指纹用于分享卡短码，
        使「同一个人微调出两个版本」能被区分开（防重样红线）。
        """
        payload = {**self.signature(), "colors": self.colors, "height_scale": self.height_scale}
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _pick(seq: tuple[str, ...], rng_value: float) -> str:
    idx = min(len(seq) - 1, max(0, int(rng_value * len(seq))))
    return seq[idx]


def _mbti_letters(mbti: str | None) -> tuple[str, str, str, str]:
    if mbti:
        return mbti[0], mbti[1], mbti[2], mbti[3]
    return "I", "N", "F", "P"  # 中性默认：安静、清峭、温柔、蓬松


def map_portrait(portrait: PortraitInput) -> AvatarParams:
    """核心映射：画像 → 角色参数（纯函数，无副作用、无网络、无随机源）。"""
    fp = fingerprint(portrait)
    rng = _deterministic_rng(int(fp[:8], 16))
    missing: list[str] = []
    sources: dict[str, str] = {}

    # --- 主色调板 ← 八字五行主导 ---------------------------------------
    element = portrait.bazi_element
    if element is None:
        element = NEUTRAL_ELEMENT
        missing.append("bazi_element")
        sources["palette_id"] = "default(土)"
    else:
        sources["palette_id"] = f"bazi_element({element})"
    palette = ELEMENT_PALETTES[element]

    # --- MBTI 四维 -------------------------------------------------------
    e_i, s_n, t_f, j_p = _mbti_letters(portrait.mbti)
    if portrait.mbti is None:
        missing.append("mbti")
        sources["hair_style"] = "default"
        sources["eye"] = "default"
        sources["mouth"] = "default"
        sources["outfit"] = "default"
    else:
        sources["hair_style"] = f"mbti({portrait.mbti})"
        sources["eye"] = f"mbti({portrait.mbti})"
        sources["mouth"] = f"mbti({portrait.mbti})"
        sources["outfit"] = f"mbti({portrait.mbti})"

    # 发型剪影 ← J/P × E/I（12 种：neat/fluffy 两族各 6 种）
    cut = MBTI_SHAPE[j_p]["hair_cut"]
    lively = e_i == "E"
    if cut == "neat":
        hair_pool = ("short_neat", "bob", "ponytail", "bun", "side_swept", "undercut")
    else:
        hair_pool = ("short_fluffy", "long_wavy", "curly", "long_straight", "twin_tail", "braid")
    hair_style = _pick(hair_pool, rng()) if portrait.mbti else "short_neat"
    if portrait.mbti is None:
        hair_style = "short_neat"

    # 发色 ← 五行主色系 + 明度层级（8 种）
    tone_pool = _hair_tones_for_element(element)
    hair_tone = _pick(tone_pool, rng()) if portrait.mbti else "ink"

    # 眼神 ← 性格维度（大五 mood 偏移 + MBTI 倾向）
    if portrait.mood is not None:
        eye = EYE_STYLES[MOOD_EYE_BIAS[portrait.mood] % len(EYE_STYLES)]
        sources["eye"] = f"mood({portrait.mood})"
    elif portrait.mbti is not None:
        bias = 0 if lively else 2
        eye = EYE_STYLES[(bias + (0 if s_n == "N" else 1)) % len(EYE_STYLES)]
    else:
        eye = "calm"

    # 表情基调 ← T/F
    if portrait.mbti is None:
        mouth = "smile"
    else:
        mouth = MBTI_SHAPE[t_f]["mouth"]
        # F 温柔族 5 选一（用确定性 rng，但同 t_f 只在小范围内变化，保证「基调」不漂移）
        if t_f == "F":
            mouth = _pick(("smile", "small", "open_smile", "grin", "smile"), rng())
        else:
            mouth = _pick(("flat", "small", "flat", "smile", "small"), rng())

    # 服装质感倾向 ← 五行；款式 ← MBTI 四维
    texture = palette["texture"]
    if portrait.mbti is None:
        outfit = "knit"
    else:
        outfit = _outfit_for(lively, s_n, j_p)

    # 头饰 / 披风 / 徽记 ← 星盘日/月/升
    sun = portrait.sun_sign
    moon = portrait.moon_sign
    asc = portrait.asc_sign
    if sun is None:
        missing.append("sun_sign")
    if moon is None:
        missing.append("moon_sign")
    if asc is None:
        missing.append("asc_sign")
    emblem = sun or NEUTRAL_ZODIAC
    accessory = SUN_HEADWEAR[emblem]
    cape = MOON_CAPE[moon] if moon else "soft_grey"
    charm = ASC_ACCENT[asc] if asc else "seal"
    sources["accessory"] = f"sun_sign({emblem})" if sun else "default"
    sources["emblem"] = f"sun_sign({emblem})" if sun else "default"
    sources["cape"] = f"moon_sign({moon})" if moon else "default"

    # 随身小物 ← 性格：决策优先级契约（G1-2b 明确）
    #   mood 命中 HAND_ITEMS → 用所选项；
    #   mood 缺失但 mbti 存在 → 退回 MBTI 维度 _hand_for；
    #   都缺失 → HAND_ITEMS_DEFAULT。
    hand = HAND_ITEMS.get(portrait.mood or "", HAND_ITEMS_DEFAULT)
    if portrait.mbti is not None and portrait.mood is None:
        hand = _hand_for(e_i, t_f)

    colors = _build_colors(palette, hair_tone, cape, element)

    labels = {
        "palette": str(palette["label"]),
        "texture": str(palette["texture_label"]),
        "hair_style": HAIR_STYLE_LABELS[hair_style],
        "hair_tone": hair_tone,
        "eye": EYE_LABELS[eye],
        "mouth": MOUTH_LABELS[mouth],
        "outfit": OUTFIT_LABELS[outfit],
        "headwear": accessory,
        "cape": cape,
        "charm": charm,
        "hand_item": hand,
        "emblem_cn": ZODIAC_CN.get(emblem, ""),
        "stride": MBTI_SHAPE[e_i]["stride"],
        "posture": MBTI_SHAPE[e_i]["posture"],
        "face_shape": MBTI_SHAPE[s_n]["face_shape"],
    }

    return AvatarParams(
        hair_style=hair_style,
        hair_tone=hair_tone,
        palette_id=element,
        eye=eye,
        mouth=mouth,
        accessory=accessory,
        outfit=outfit,
        emblem=emblem,
        texture=texture,
        height_scale=1.0 if lively else 0.97,
        colors=colors,
        labels={**labels, "cape_id": cape, "charm_id": charm, "hand_item_id": hand},
        sources=sources,
        missing=sorted(set(missing)),
        fingerprint=fp,
    )


def _hair_tones_for_element(element: str) -> tuple[str, ...]:
    """五行 → 发色候选（8 种全局色板的确定性子集，仍含 8 项以便组合计数）。"""
    mapping = {
        "金": ("frost", "ash", "ink", "chestnut", "gold", "rose", "mint", "auburn"),
        "木": ("chestnut", "mint", "ink", "gold", "ash", "auburn", "rose", "frost"),
        "水": ("ink", "ash", "frost", "chestnut", "mint", "gold", "auburn", "rose"),
        "火": ("auburn", "gold", "rose", "chestnut", "ink", "frost", "mint", "ash"),
        "土": ("chestnut", "gold", "ink", "auburn", "ash", "rose", "mint", "frost"),
    }
    return mapping.get(element, ("ink", "chestnut", "gold", "auburn", "ash", "rose", "mint", "frost"))


def _outfit_for(lively: bool, s_n: str, j_p: str) -> str:
    if not lively and j_p == "J":
        return "coat" if s_n == "S" else "robe"
    if lively and j_p == "P":
        return "hoodie" if s_n == "N" else "tshirt"
    if j_p == "J":
        return "knit"
    return "dress" if lively else "vest"


def _hand_for(e_i: str, t_f: str) -> str:
    if e_i == "E" and t_f == "F":
        return "flower"
    if e_i == "E":
        return "compass"
    if t_f == "F":
        return "tea_cup"
    return "book"


def _build_colors(palette: dict[str, Any], hair_tone: str, cape: str, element: str) -> dict[str, str]:
    """组装最终色板（≥16 色），并用对比度自检做自动微调，避免脏色。

    键集与 :data:`CHAR_KEYS` 的语义**一一对应**（前端按同一表查色），
    另含 ``rim_light``（1px 轮廓光专用亮色）与 ``hand_item``/``shoe``。
    """
    base = str(palette["base"])
    shadow = str(palette["shadow"])
    accent = str(palette["accent"])
    trim = str(palette["trim"])
    skin = str(palette["skin"])
    cape_hex = _cape_hex(cape, element)
    hair = HAIR_TONE_HEX[hair_tone]

    keys = [
        "skin", "skin_shadow",
        "hair", "hair_shadow", "hair_highlight",
        "outfit_base", "outfit_shadow", "outfit_highlight",
        "cape", "cape_shadow",
        "trim", "legwear", "shoe",
        "eye_ink", "eye_highlight", "blush",
        "hand_item", "rim_light", "emblem_bg", "ground_shadow",
    ]
    colors_list = [
        skin,
        _shade(skin, 0.86),                     # skin_shadow
        hair,
        _shade(hair, 0.70),                      # hair_shadow
        _shade(hair, 1.22),                      # hair_highlight（受光发丝）
        base,                                    # outfit_base
        _shade(base, 0.80),                      # outfit_shadow
        _shade(base, 1.14),                      # outfit_highlight
        cape_hex,                                # cape
        _shade(cape_hex, 0.76),                  # cape_shadow
        trim,                                    # trim
        shadow,                                  # legwear
        _shade(accent, 0.55),                    # shoe
        "#2b2f3a",                               # eye_ink
        "#ffffff",                               # eye_highlight
        "#e08b8b",                               # blush
        trim,                                    # hand_item
        _shade(trim, 1.30),                      # rim_light（比 trim 更亮的轮廓光）
        str(palette["bg"]),                      # emblem_bg
        _shade(base, 0.55),                      # ground_shadow（脚下椭圆影：主色深版，替代近白 emblem_bg）
    ]
    # 画布相邻色明度差自检：只检查真实贴邻的键对，冲突色自动提亮，避免像素「脏色」
    return dict(check_palette_harmony(dict(zip(keys, colors_list, strict=True)))["adjusted"])


_CAPE_HEX: dict[str, str] = {
    "short_red": "#c9553f", "long_green": "#5f9e6a", "striped": "#7f93c9",
    "soft_grey": "#a8adb8", "flowing_gold": "#d4ad52", "neat_white": "#e6ebf2",
    "silk_pink": "#dfa2b4", "deep_plum": "#7a4a6b", "travel_blue": "#5a86b8",
    "heavy_brown": "#8a6440", "sheer_cyan": "#8fd0d8", "misty_violet": "#a98fd0",
}


def _cape_hex(cape: str, element: str) -> str:
    if cape in _CAPE_HEX:
        return _CAPE_HEX[cape]
    return str(ELEMENT_PALETTES[element]["accent"])


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    r = max(0, min(255, round(r * factor)))
    g = max(0, min(255, round(g * factor)))
    b = max(0, min(255, round(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


# ======================================================================
# 像素合成器：角色参数 → 24×32 多层矩阵（8 层）
# ======================================================================
#
# 分层顺序（任务书 §2.2）：
#   1. 地面影   2. 身体底层   3. 发型   4. 表情   5. 服装
#   6. 配饰     7. 手持物     8. 1px 轮廓光
#
# **实现方式：程序化绘制原语**（rect / ellipse / blit / shade），而非手写字符矩阵。
# 理由（对齐 11 号文档 DNA-10「程序化工艺」）：
#   - 手写 24 列矩阵极易出现行宽不一致的静默 bug（画出来才发现被裁掉），
#     而绘制原语自带边界裁剪，**结构上不可能越界**；
#   - 发型/服装/配饰的形状差异用参数（宽高/偏移/是否对称）表达，
#     便于「远看有型、近看耐看」地统一调优。
#
# 矩阵字符约定（与前端 cabinPixels.matrixToRgba 一致，'.'=透明）：
#   s 皮肤  S 皮肤暗部  h 头发  H 头发暗部  o 服装主色  O 服装暗部
#   c 披风  C 披风暗部  e 眼  E 眼高光  b 腮红  t 装饰/徽章  g 手持物
#   x 轮廓光  a 徽章底/地面影  w 鞋

LAYER_NAMES: tuple[str, ...] = (
    "shadow", "body", "hair", "face", "outfit", "accessory", "hand_item", "outline",
)

#: 色板字符 → 语义 key（前端与分享卡渲染按此表查色；与 _build_colors 的键集一致）
CHAR_KEYS: dict[str, str] = {
    "s": "skin", "S": "skin_shadow",
    "h": "hair", "H": "hair_shadow", "G": "hair_highlight",
    "o": "outfit_base", "O": "outfit_shadow", "F": "outfit_highlight",
    "c": "cape", "C": "cape_shadow",
    "e": "eye_ink", "E": "eye_highlight", "b": "blush",
    "t": "trim", "g": "hand_item", "w": "shoe", "L": "legwear",
    "x": "rim_light", "a": "emblem_bg",
    "z": "ground_shadow",
}

#: 头部几何（**单一真源**）：脸本体椭圆 = 挖脸洞用的同一个椭圆。
#:
#: 曾经这里有两个独立半径（body 画脸用 6.1/5.8，carve 挖洞用 FACE_RX/FACE_RY=5.6/5.4），
#: 导致挖出来的洞比脸本体小一圈，发型从四周内侵、脸只剩中间一条缝（渲染实测）。
#: 现在两者都从这里取，**结构上不可能再错位**。
HEAD_TOP = 1
FACE_CX = 12.0
FACE_CY = 10.5
FACE_RX = 7.2          # S 圆脸
FACE_RY = 7.2
FACE_RX_SHARP = 6.4    # N 清峭脸
FACE_RY_SHARP = 7.6
BODY_TOP = 20
SHOULDER_W = 14
HIP_W = 12


def face_radii(face_shape: str) -> tuple[float, float]:
    """按脸型取脸的半径 —— body 绘制与 carve 挖洞共用，杜绝半径不一致。"""
    if face_shape == "round":
        return FACE_RX, FACE_RY
    return FACE_RX_SHARP, FACE_RY_SHARP


def char_palette(colors: dict[str, str]) -> dict[str, str]:
    """矩阵字符 → #RRGGBB（**前端唯一需要的色板形状**）。

    为什么必须单独下发：
      ``params["colors"]`` 是**语义名 → hex**（skin/hair/rim_light…），
      而 ``matrix`` / ``layers`` 里是**单字符键**（s/h/x…）。两者键名不同，
      前端拿语义色板去查矩阵字符会一个都命中不了 —— 渲染成全透明或空洞。
      以前只下发 ``char_keys`` 让前端自己拼映射，等于把契约知识复制到每一份
      前端代码里；这里在后端一次性合成字符色板，前端拿来即用。
    """
    out: dict[str, str] = {}
    for char, semantic in CHAR_KEYS.items():
        hex_value = colors.get(semantic)
        if hex_value is None:
            raise ValidationFailed(
                "avatar_palette_incomplete",
                f"Semantic colour missing for char {char!r}: {semantic}",
            )
        out[char] = hex_value
    return out


class PixelCanvas:
    """24×32 字符画布：所有绘制原语都做边界裁剪，结构上不可能写出越界像素。"""

    __slots__ = ("w", "h", "grid")

    def __init__(self, w: int = AVATAR_WIDTH, h: int = AVATAR_HEIGHT) -> None:
        self.w = w
        self.h = h
        self.grid = [["." for _ in range(w)] for _ in range(h)]

    # -- 原语 ------------------------------------------------------------
    def px(self, x: int, y: int, ch: str) -> None:
        if 0 <= x < self.w and 0 <= y < self.h and ch != ".":
            self.grid[y][x] = ch

    def rect(self, x: int, y: int, w: int, h: int, ch: str) -> None:
        for iy in range(y, y + h):
            for ix in range(x, x + w):
                self.px(ix, iy, ch)

    def hline(self, x: int, y: int, w: int, ch: str) -> None:
        self.rect(x, y, w, 1, ch)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, ch: str) -> None:
        """实心椭圆（用于头发团块、影、徽章底）。rx/ry 为半径（可小数）。"""
        if rx <= 0 or ry <= 0:
            return
        x0 = max(0, int(cx - rx - 0.5))
        x1 = min(self.w - 1, int(cx + rx + 0.5))
        y0 = max(0, int(cy - ry - 0.5))
        y1 = min(self.h - 1, int(cy + ry + 0.5))
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                dx = (x + 0.5 - cx) / rx
                dy = (y + 0.5 - cy) / ry
                if dx * dx + dy * dy <= 1.0:
                    self.px(x, y, ch)

    def shade_edge(self, ch_edge: str, *, prefer: str = "bottom") -> None:
        """给已绘制的实心区域补暗部：优先下缘，其次右缘（模拟下方/右侧受光阴影）。

        只处理当前层中 ``ch_edge`` 之外仍为空、且与实体 4-邻接的像素。
        """
        solid = [
            [self.grid[y][x] != "." for x in range(self.w)]
            for y in range(self.h)
        ]
        order = ((0, 1), (0, -1), (1, 0), (-1, 0)) if prefer == "bottom" else (
            (1, 0), (-1, 0), (0, 1), (0, -1)
        )
        for y in range(self.h):
            for x in range(self.w):
                if solid[y][x]:
                    continue
                for dx, dy in order:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.w and 0 <= ny < self.h and solid[ny][nx]:
                        self.px(x, y, ch_edge)
                        break

    def overlay(self, other: "PixelCanvas") -> None:
        """把 ``other`` 的非透明像素盖到本层之上（后画者覆盖先画者）。

        用于「躯干先描边、再把干净的脸盖回去」——否则 ``shade_edge`` 会把脸的
        整圈轮廓也糊上暗边，Q 版脸会显得脏、变小。
        """
        for y in range(self.h):
            for x in range(self.w):
                ch = other.grid[y][x]
                if ch != ".":
                    self.grid[y][x] = ch

    def blit(self, art: tuple[str, ...], ox: int, oy: int) -> None:
        for y, row in enumerate(art):
            for x, ch in enumerate(row):
                self.px(ox + x, oy + y, ch)

    def rows(self) -> list[str]:
        return ["".join(r) for r in self.grid]

    def filled(self) -> list[list[bool]]:
        return [[ch != "." for ch in row] for row in self.grid]


# ======================================================================
# 各层绘制
# ======================================================================


def _draw_shadow() -> list[str]:
    """层 1：地面椭圆影（11 号文档 DNA-5 硬要求：地面椭圆影）。"""
    c = PixelCanvas()
    # 地面影在第 45~47 行，深色半透明角色主色深版
    c.ellipse(FACE_CX, 46.0, 8.5, 1.8, "z")
    c.ellipse(FACE_CX, 46.0, 5.0, 1.0, "z")
    return c.rows()


def _draw_body(face_shape: str) -> list[str]:
    """层 2：身体底层 = 头（脸）+ 颈 + 躯干 + 手臂 + 腿 + 鞋。

    解剖约定（严格 2.5 头身，头 20 + 身 28 = 48 行）：
      - 头部 20 行（y=0..19），脸本体干净，圆润软边缘；
      - 肩宽 14（y=20..24 软圆肩角），腰宽 12（y=25..30），胯宽 12（y=31..36）；
      - 手臂（y=21..33，手掌 s 在 y=31..33）；
      - 腿 7 行（y=37..43，L）；
      - 鞋 3 行（y=44..46，w，圆角像素鞋）。
    """
    torso = PixelCanvas()
    cx = int(FACE_CX)
    # --- 躯干：肩 → 腰 → 胯 ---
    half = SHOULDER_W // 2
    torso.rect(cx - half + 1, BODY_TOP, SHOULDER_W - 2, 2, "o")      # 肩顶圆角
    torso.rect(cx - half, BODY_TOP + 2, SHOULDER_W, 3, "o")          # 肩中
    torso.rect(cx - half + 1, BODY_TOP + 5, SHOULDER_W - 2, 6, "o")  # 腰 (y=25..30)
    torso.rect(cx - HIP_W // 2, BODY_TOP + 11, HIP_W, 6, "o")        # 胯/下摆 (y=31..36)
    # --- 手臂（两侧 2 宽，手为皮肤 s）---
    torso.rect(cx - half - 2, BODY_TOP + 1, 2, 10, "o")
    torso.rect(cx + half, BODY_TOP + 1, 2, 10, "o")
    torso.rect(cx - half - 2, BODY_TOP + 11, 2, 3, "s")
    torso.rect(cx + half, BODY_TOP + 11, 2, 3, "s")
    # --- 腿 + 鞋 ---
    torso.rect(cx - 4, BODY_TOP + 17, 3, 7, "L")
    torso.rect(cx + 1, BODY_TOP + 17, 3, 7, "L")
    torso.rect(cx - 5, BODY_TOP + 24, 4, 3, "w")
    torso.rect(cx + 1, BODY_TOP + 24, 4, 3, "w")
    torso.shade_edge("S", prefer="bottom")

    # --- 干净的脸 + 颈（overlay 在描边之上）---
    head = PixelCanvas()
    rx, ry = face_radii(face_shape)
    head.ellipse(FACE_CX, FACE_CY, rx, ry, "s")
    head.px(cx - 8, int(FACE_CY), "s")       # 耳
    head.px(cx + 7, int(FACE_CY), "s")
    neck_y = int(FACE_CY + ry) - 3
    head.rect(cx - 3, neck_y, 6, 4, "s")

    torso.overlay(head)
    return torso.rows()


#: 发型 12 种 → 形状参数（在脸的外侧堆叠；face 永远画在其上，故不会「戴头盔」）
_HAIR_SHAPES: dict[str, dict[str, Any]] = {
    "short_neat":    {"puff": 0, "fringe": 3, "side": 0, "back": 0, "tails": 0, "wave": 0},
    "short_fluffy":  {"puff": 2, "fringe": 4, "side": 1, "back": 0, "tails": 0, "wave": 1},
    "bob":           {"puff": 0, "fringe": 3, "side": 1, "back": 0, "tails": 0, "wave": 0, "bob": 3},
    "long_straight": {"puff": 0, "fringe": 3, "side": 1, "back": 7, "tails": 0, "wave": 0},
    "long_wavy":     {"puff": 1, "fringe": 3, "side": 1, "back": 7, "tails": 0, "wave": 2},
    "ponytail":      {"puff": 0, "fringe": 3, "side": 0, "back": 0, "tails": 1},
    "twin_tail":     {"puff": 0, "fringe": 3, "side": 1, "back": 0, "tails": 2},
    "bun":           {"puff": 0, "fringe": 2, "side": 0, "back": 0, "tails": 0, "bun": 1},
    "side_swept":    {"puff": 0, "fringe": 4, "side": 2, "back": 0, "tails": 0, "sweep": 1},
    "curly":         {"puff": 2, "fringe": 4, "side": 1, "back": 2, "tails": 0, "wave": 3},
    "braid":         {"puff": 0, "fringe": 3, "side": 1, "back": 5, "tails": 0, "braid": 1},
    "undercut":      {"puff": 0, "fringe": 2, "side": -1, "back": 0, "tails": 0},
}


def _draw_hair(style: str, face_shape: str) -> list[str]:
    """层 3：发型剪影（12 种）。

    关键：**先画头发团（比脸大 1~2px 的外圈），再挖出脸的可见区**，
    于是脸（body 层）从发团中透出来，而不是被头发盖住。
    """
    shape = _HAIR_SHAPES.get(style, _HAIR_SHAPES["short_neat"])
    c = PixelCanvas()
    cx = int(FACE_CX)
    frx, fry = face_radii(face_shape)
    puff = int(shape["puff"])
    fringe = int(shape["fringe"])

    # 1) 头顶发团：比脸略大（外圈）
    c.ellipse(FACE_CX, FACE_CY - 0.5, frx + 1.8 + puff * 0.5, fry + 1.4 + puff * 0.4, "h")
    if shape.get("bun"):
        c.ellipse(FACE_CX, FACE_CY - fry - 2.5, 4.2, 3.2, "h")
    if shape.get("sweep"):
        c.ellipse(cx + 6.0, FACE_CY - 3.0, 3.5, 4.5, "h")
    if shape.get("bob"):
        c.ellipse(cx - 7.5, FACE_CY + 2.0, 2.8, 5.5, "h")
        c.ellipse(cx + 7.5, FACE_CY + 2.0, 2.8, 5.5, "h")
    if shape.get("back"):
        back = int(shape["back"]) * 2
        c.rect(cx - 9, int(FACE_CY) - 1, 3, back, "h")
        c.rect(cx + 7, int(FACE_CY) - 1, 3, back, "h")
        if shape.get("wave"):
            for k in range(back // 3 + 1):
                c.ellipse(cx - 9, FACE_CY + 2 + k * 3, 2.2, 1.8, "h")
                c.ellipse(cx + 8, FACE_CY + 2 + k * 3, 2.2, 1.8, "h")
        if shape.get("braid"):
            c.ellipse(cx - 9, FACE_CY + back, 2.0, 1.8, "h")
            c.ellipse(cx + 8, FACE_CY + back, 2.0, 1.8, "h")
    if shape.get("tails") == 1:
        c.ellipse(cx + 10, FACE_CY + 3.0, 3.0, 6.5, "h")
    if shape.get("tails") == 2:
        c.ellipse(cx - 10, FACE_CY + 2.5, 3.0, 5.5, "h")
        c.ellipse(cx + 10, FACE_CY + 2.5, 3.0, 5.5, "h")

    # 2) 挖出脸的可见区
    scaled_fringe = fringe + 1
    hole = _face_hole_mask(frx, fry, scaled_fringe)
    for y in range(AVATAR_HEIGHT):
        for x in range(AVATAR_WIDTH):
            if hole[y][x]:
                c.grid[y][x] = "."

    # 3) 刘海：叠在洞顶
    top = int(FACE_CY - fry) - 1
    c.rect(cx - 7, top, 15, scaled_fringe, "h")
    if shape.get("wave") and int(shape["wave"]) >= 2:
        for k in range(4):
            c.px(cx - 6 + k * 4, top + scaled_fringe, ".")

    # 4) 只给外轮廓描暗边
    _shade_outer_edge(c, hole=hole, edge="H", prefer="bottom")
    return c.rows()


def _face_hole_mask(rx: float, ry: float, keep_fringe: int) -> list[list[bool]]:
    """脸的可见区掩膜。"""
    hole = [[False] * AVATAR_WIDTH for _ in range(AVATAR_HEIGHT)]
    y0 = int(FACE_CY - ry) + keep_fringe
    y1 = int(FACE_CY + ry)
    for y in range(y0, y1 + 1):
        for x in range(AVATAR_WIDTH):
            dx = (x + 0.5 - FACE_CX) / rx
            dy = (y + 0.5 - FACE_CY) / ry
            if dx * dx + dy * dy <= 1.0:
                hole[y][x] = True
    return hole


def _shade_outer_edge(
    c: PixelCanvas,
    *,
    hole: list[list[bool]],
    edge: str,
    prefer: str = "bottom",
) -> None:
    """给「实心且不在洞内」的像素的外邻补暗边——脸洞边界不补。"""
    solid = [[ch != "." for ch in row] for row in c.grid]
    order = ((0, 1), (0, -1), (1, 0), (-1, 0)) if prefer == "bottom" else (
        (1, 0), (-1, 0), (0, 1), (0, -1)
    )
    for y in range(AVATAR_HEIGHT):
        for x in range(AVATAR_WIDTH):
            if not solid[y][x] or hole[y][x]:
                continue
            for dx, dy in order:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < AVATAR_WIDTH and 0 <= ny < AVATAR_HEIGHT):
                    continue
                if solid[ny][nx] or hole[ny][nx]:
                    continue
                c.px(nx, ny, edge)
                break


def _draw_face(eye: str, mouth: str, face_shape: str) -> list[str]:
    """层 4：表情（眼 + 高光 + 腮红 + 嘴）。大眼(>=2px) + 腮红明显 + 柔和表情。"""
    c = PixelCanvas()
    cx = int(FACE_CX)
    eye_y = int(FACE_CY) - 1      # y=9..11
    spec = {
        "sparkle":  [(0, 0), (1, 1)],
        "calm":     [(0, 0)],
        "sharp":    [(1, 0)],
        "gentle":   [(0, 0), (1, 2)],
        "dreamy":   [(0, 1), (1, 0)],
        "focused":  [(0, 0), (1, 0)],
    }.get(eye, [(0, 0)])

    # 大眼睛：2×3 尺寸，大眼可爱萌动
    for side in (-1, 1):
        ex = cx + (side * 4 if side < 0 else side * 2)
        c.rect(ex, eye_y, 2, 3, "e")
        for dx, dy in spec:
            c.px(ex + dx, eye_y + dy, "E")

    # 腮红明显：宽 2 高 1，落在眼睛下方外侧
    c.rect(cx - 6, eye_y + 4, 2, 1, "b")
    c.rect(cx + 4, eye_y + 4, 2, 1, "b")

    # 嘴巴：柔和表情
    my = eye_y + 5
    if mouth == "flat":
        c.rect(cx - 2, my, 4, 1, "O")
    elif mouth == "smile":
        c.px(cx - 2, my, "O")
        c.px(cx + 1, my, "O")
        c.px(cx - 1, my, "b")
        c.px(cx, my, "b")
    elif mouth == "small":
        c.rect(cx - 1, my, 3, 1, "O")
    elif mouth == "open_smile":
        c.rect(cx - 2, my, 5, 1, "O")
        c.rect(cx - 1, my + 1, 3, 1, "b")
    elif mouth == "grin":
        c.rect(cx - 3, my, 6, 1, "O")
        c.rect(cx - 2, my + 1, 4, 1, "O")
    else:
        c.rect(cx - 1, my, 3, 1, "O")

    frx, fry = face_radii(face_shape)
    hole = _face_hole_mask(frx, fry, 0)
    for y in range(AVATAR_HEIGHT):
        for x in range(AVATAR_WIDTH):
            if c.grid[y][x] != "." and not hole[y][x]:
                c.grid[y][x] = "."
    return c.rows()


#: 服装 8 种 → 剪影参数
_OUTFIT_SHAPES: dict[str, dict[str, Any]] = {
    "tshirt": {"hem": 0, "skirt": 0, "hood": 0, "slim": 0, "sleeve": 2},
    "knit":  {"hem": 0, "skirt": 0, "hood": 0, "slim": 0, "sleeve": 3, "stripe": 1},
    "coat":  {"hem": 2, "skirt": 0, "hood": 0, "slim": 0, "sleeve": 4, "lapel": 1},
    "robe":  {"hem": 3, "skirt": 3, "hood": 0, "slim": 0, "sleeve": 4, "lapel": 1},
    "dress": {"hem": 1, "skirt": 3, "hood": 0, "slim": 0, "sleeve": 1},
    "hoodie": {"hem": 0, "skirt": 0, "hood": 1, "slim": 0, "sleeve": 3, "pocket": 1},
    "vest":  {"hem": 0, "skirt": 0, "hood": 0, "slim": 1, "sleeve": 0},
    "cape":  {"hem": 2, "skirt": 0, "hood": 1, "slim": 0, "sleeve": 2, "cape": 1},
}


def _draw_outfit(outfit: str) -> list[str]:
    """层 5：服装（在身体之上着色，保留脸/手/腿的肤色与鞋子）。"""
    shape = _OUTFIT_SHAPES.get(outfit, _OUTFIT_SHAPES["knit"])
    c = PixelCanvas()
    cx = int(FACE_CX)
    half = SHOULDER_W // 2 - int(shape.get("slim", 0))
    sl = int(shape.get("sleeve", 2))

    # 衣身（肩 → 腰 → 下摆）
    c.rect(cx - half + 1, BODY_TOP, (half - 1) * 2, 2, "o")
    c.rect(cx - half, BODY_TOP + 2, half * 2, 3, "o")
    c.rect(cx - half + 1, BODY_TOP + 5, (half - 1) * 2, 6, "o")
    hem_rows = 6 + int(shape.get("hem", 0)) * 2
    c.rect(cx - HIP_W // 2, BODY_TOP + 11, HIP_W, hem_rows, "o")
    if shape.get("skirt"):
        sk = int(shape["skirt"])
        c.rect(cx - HIP_W // 2 - sk, BODY_TOP + 14, HIP_W + sk * 2, sk + 2, "o")
    # 袖（长度 = sleeve；0 = 背心无袖）
    if sl > 0:
        sleeve_len = 4 if sl == 1 else (7 if sl == 2 else 10)
        c.rect(cx - half - 2, BODY_TOP + 1, 2, sleeve_len, "o")
        c.rect(cx + half, BODY_TOP + 1, 2, sleeve_len, "o")
    if shape.get("cape"):
        c.rect(cx - half - 3, BODY_TOP - 1, (half + 3) * 2, 16, "c")
    if shape.get("hood"):
        # 帽堆在颈后
        c.rect(cx - half - 1, BODY_TOP - 3, (half + 1) * 2, 3, "o")

    # --- 细节：全部画在「衣身之内」，且不与描边抢位置 ---
    if shape.get("stripe"):
        for k in range(3):
            c.px(cx - half + 2 + k * 2, BODY_TOP + 6, "F")
            c.px(cx - half + 2 + k * 2, BODY_TOP + 7, "F")
            c.px(cx - half + 2 + k * 2, BODY_TOP + 8, "F")
    if shape.get("lapel"):
        # 翻领：只在肩部做出 V 字领口线
        c.px(cx - 2, BODY_TOP, "t")
        c.px(cx + 1, BODY_TOP, "t")
        c.px(cx - 1, BODY_TOP + 1, "t")
        c.px(cx, BODY_TOP + 1, "t")
        c.px(cx - 1, BODY_TOP + 2, "t")
        c.px(cx, BODY_TOP + 2, "t")
    if shape.get("pocket"):
        c.rect(cx - 4, BODY_TOP + 9, 3, 2, "O")
        c.rect(cx + 1, BODY_TOP + 9, 3, 2, "O")
    # 领口：1px 点缀在肩线上
    c.px(cx - 2, BODY_TOP, "t")
    c.px(cx + 1, BODY_TOP, "t")
    c.shade_edge("O", prefer="bottom")
    return c.rows()


#: 头饰 12 种（星盘日）→ 绘制类型
_HEADWEAR_KINDS: dict[str, str] = {
    "circlet": "band", "ribbon": "bow", "double_pin": "pin", "hood": "hood",
    "crown": "crown", "laurel": "wreath", "bow": "bow", "veil": "veil",
    "feather": "feather", "band": "band", "halo": "halo", "tiara": "crown",
}


def _hair_anchor(hair: list[str]) -> tuple[int, int, int]:
    """头饰锚点：返回 ``(锚行, 跨度左, 跨度右)``。"""
    top = AVATAR_HEIGHT
    for y, row in enumerate(hair):
        if any(ch != "." for ch in row):
            top = y
            break
    if top >= AVATAR_HEIGHT:
        return HEAD_TOP, int(FACE_CX) - 5, int(FACE_CX) + 5
    y = min(top + 1, AVATAR_HEIGHT - 1)
    xs = [x for x in range(AVATAR_WIDTH) if hair[y][x] != "."]
    return y, xs[0], xs[-1]


def _draw_accessory(
    accessory: str, emblem: str, face_shape: str, hair: list[str]
) -> list[str]:
    """层 6：头饰（压在发际线上）+ 星座徽记（胸前，像素化星座符号）。"""
    c = PixelCanvas()
    cx = int(FACE_CX)
    kind = _HEADWEAR_KINDS.get(accessory, "band")
    frx, fry = face_radii(face_shape)   # noqa: F841
    hairline, span_lo, span_hi = _hair_anchor(hair)

    if kind == "band":
        c.rect(cx - 6, hairline, 13, 2, "t")
    elif kind == "bow":
        c.rect(cx - 7, hairline - 1, 4, 3, "t")
        c.rect(cx + 3, hairline - 1, 4, 3, "t")
        c.rect(cx - 1, hairline - 1, 3, 3, "t")
    elif kind == "pin":
        c.rect(cx - 5, hairline, 3, 2, "t")
        c.rect(cx + 3, hairline, 3, 2, "t")
    elif kind == "hood":
        c.rect(cx - 7, hairline, 15, 3, "t")
        c.rect(cx - 8, hairline + 3, 2, 6, "t")
        c.rect(cx + 7, hairline + 3, 2, 6, "t")
    elif kind == "crown":
        c.rect(cx - 5, hairline + 1, 11, 2, "t")
        c.px(cx - 5, hairline, "t")
        c.px(cx - 2, hairline - 1, "t")
        c.px(cx + 1, hairline - 1, "t")
        c.px(cx + 4, hairline, "t")
    elif kind == "wreath":
        for k in range(5):
            c.px(cx - 6 + k * 3, hairline + (k % 2), "t")
            c.px(cx - 5 + k * 3, hairline + 1 - (k % 2), "t")
    elif kind == "veil":
        c.rect(cx - 7, hairline, 15, 2, "t")
        c.rect(cx - 7, hairline + 2, 2, 14, "t")
        c.rect(cx + 6, hairline + 2, 2, 14, "t")
    elif kind == "feather":
        c.rect(cx - 4, hairline, 9, 2, "t")
        for k in range(6):
            c.px(cx + 2 + k, hairline - (k // 2), "t")
    elif kind == "halo":
        c.ellipse(cx + 0.5, hairline - 1.5, 6.0, 2.0, "t")

    lo, hi = span_lo - 1, span_hi + 1
    for y in range(AVATAR_HEIGHT):
        if y >= BODY_TOP:
            break
        row = c.grid[y]
        if "." in row:
            c.grid[y] = "".join(
                row[x] if lo <= x <= hi else "." for x in range(AVATAR_WIDTH)
            )

    # 星座徽记：居中贴在胸前
    art = _emblem_art(emblem)
    aw = max(len(r) for r in art)
    c.blit(_emblem_plate(art, plated=False), cx - aw // 2, BODY_TOP + 5)
    return c.rows()


def _emblem_plate(art: tuple[str, ...], *, plated: bool = False) -> tuple[str, ...]:
    """裁掉 ``art`` 的空白包围盒，使徽记紧贴中心。"""
    h = len(art)
    w = max(len(r) for r in art)
    solid = [
        [(x < len(art[y]) and art[y][x] not in (".", " ")) for x in range(w)]
        for y in range(h)
    ]
    ys = [y for y in range(h) if any(solid[y])]
    xs = [x for x in range(w) if any(solid[y][x] for y in range(h))]
    if not ys or not xs:
        return tuple("..." for _ in range(3))
    y0, y1, x0, x1 = ys[0], ys[-1], xs[0], xs[-1]
    if plated:
        y0, x0 = max(0, y0 - 1), max(0, x0 - 1)
        y1, x1 = min(h - 1, y1 + 1), min(w - 1, x1 + 1)
    out: list[str] = []
    for y in range(y0, y1 + 1):
        row = ""
        for x in range(x0, x1 + 1):
            if solid[y][x]:
                row += "t"
            elif plated and any(
                0 <= ny < h and 0 <= nx < w and solid[ny][nx]
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
            ):
                row += "a"
            else:
                row += "."
        out.append(row)
    return tuple(out)


def _emblem_art(emblem: str) -> tuple[str, ...]:
    """12 星座符号 → 3 行 × 最多 5 列极简徽记。"""
    table: dict[str, tuple[str, ...]] = {
        "aries":       (".t.t.", "..t..", ".ttt."),
        "taurus":      ("ttt..", "..t..", "..t.."),
        "gemini":      ("t.t.", "ttt.", "t.t."),
        "cancer":      (".t.t", "t.t.", ".t.t"),
        "leo":         ("ttt.", "t.t.", "..t."),
        "virgo":       ("t.t.", "t.t.", ".t.."),
        "libra":       (".....", "ttttt", "..t.."),
        "scorpio":     ("t...t", "t.t.t", "..t.."),
        "sagittarius": ("...t.", "..tt.", ".t..."),
        "capricorn":   ("t...t", "t.t.t", ".ttt."),
        "aquarius":    (".....", "t.t.t", ".t.t."),
        "pisces":      ("t...t", ".t.t.", "t...t"),
    }
    return table.get(emblem, table["virgo"])


def _draw_hand_item(hand: str) -> list[str]:
    """层 7：随身小物（握在右手，5px 宽，不喧宾夺主）。"""
    c = PixelCanvas()
    hx = 18
    hy = BODY_TOP + 9
    if hand == "book":
        c.rect(hx, hy, 5, 7, "g")
        c.rect(hx + 1, hy + 1, 3, 5, "t")
    elif hand == "tea_cup":
        c.rect(hx, hy + 1, 5, 1, "t")        # 杯口
        c.rect(hx, hy + 2, 5, 4, "g")        # 杯身
        c.rect(hx + 1, hy + 6, 3, 1, "t")    # 杯托
    elif hand == "lantern":
        c.rect(hx + 1, hy, 3, 1, "t")        # 提环
        c.rect(hx, hy + 1, 5, 6, "g")        # 灯身
        c.rect(hx + 1, hy + 2, 3, 4, "t")    # 灯窗
        c.rect(hx + 1, hy + 7, 3, 1, "t")    # 底座
    elif hand == "flower":
        c.rect(hx + 1, hy, 3, 2, "g")        # 上瓣
        c.rect(hx, hy + 2, 5, 3, "g")        # 侧瓣
        c.rect(hx + 1, hy + 5, 3, 2, "g")    # 下瓣
        c.rect(hx + 2, hy + 7, 1, 2, "g")    # 花茎
    elif hand == "note_book":
        c.rect(hx, hy, 5, 7, "g")
        c.rect(hx + 1, hy + 1, 3, 1, "t")
        c.rect(hx + 1, hy + 3, 3, 1, "t")
        c.rect(hx + 1, hy + 5, 3, 1, "t")
    elif hand == "compass":
        c.rect(hx, hy + 1, 5, 6, "g")
        c.rect(hx + 1, hy + 2, 3, 4, "t")
        c.px(hx + 1, hy + 2, "g")            # 指针一端
        c.px(hx + 3, hy + 5, "g")            # 指针另一端
    else:  # seal
        c.rect(hx + 1, hy, 3, 2, "t")        # 提钮
        c.rect(hx, hy + 2, 5, 5, "g")        # 印身
        c.rect(hx + 1, hy + 3, 3, 3, "t")    # 印文
    return c.rows()


def _draw_outline(layers: list[list[str]], from_left: bool) -> list[str]:
    """层 8：1px 轮廓光——沿实体**整圈外沿**补 1px 光边（左右对称）。

    G5-2 重构：旧实现按 ``from_left`` 只在**单侧**描边（受光侧），渲染出来角色
    "只有一边有轮廓"；更早的版本又在受光侧整列填满成亮条。现改为**全边界 1px 描边**
    —— 任意空像素只要 4-邻域挨着实体，就在该像素描边。得到左右对称的轮廓，
    且厚度恒为 1px（不会连成亮条）。``from_left`` 保留签名兼容，不再决定描边在哪一侧。
    """
    solid = [[False] * AVATAR_WIDTH for _ in range(AVATAR_HEIGHT)]
    for rows in layers:
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    solid[y][x] = True

    c = PixelCanvas()
    for y in range(AVATAR_HEIGHT):
        for x in range(AVATAR_WIDTH):
            if solid[y][x]:
                continue
            adjacent = (
                (x > 0 and solid[y][x - 1])
                or (x < AVATAR_WIDTH - 1 and solid[y][x + 1])
                or (y > 0 and solid[y - 1][x])
                or (y < AVATAR_HEIGHT - 1 and solid[y + 1][x])
            )
            if adjacent:
                c.px(x, y, "x")
    return c.rows()



def compose_layers(params: AvatarParams) -> dict[str, list[str]]:
    """把角色参数合成 8 层矩阵，返回 ``{layer_name: [32 行 × 24 列]}``。

    确定性：同一 ``params``（同一画像）→ 逐字节相同的 8 层矩阵。层间互不覆盖，
    前端可按层做独立动效（呼吸/眨眼/轮廓光呼吸）。
    """
    rng = _deterministic_rng(int(params.fingerprint[:8], 16) ^ 0x5A5A)
    face_shape = str(params.labels.get("face_shape", "round"))
    hand_key = str(params.labels.get("hand_item_id", "lantern"))

    shadow = _draw_shadow()
    body = _draw_body(face_shape)
    hair = _draw_hair(params.hair_style, face_shape)
    face = _draw_face(params.eye, params.mouth, face_shape)
    outfit = _draw_outfit(params.outfit)
    accessory = _draw_accessory(params.accessory, params.emblem, face_shape, hair)
    hand_item = _draw_hand_item(hand_key)
    # 受光方向由画像指纹决定：同画像稳定，跨画像有变化（避免所有角色同侧受光）
    from_left = rng() < 0.5
    outline = _draw_outline([body, hair, outfit, accessory], from_left)

    return {
        "shadow": shadow,
        "body": body,
        "hair": hair,
        "face": face,
        "outfit": outfit,
        "accessory": accessory,
        "hand_item": hand_item,
        "outline": outline,
    }



def build_avatar(
    raw_portrait: dict[str, Any] | None,
    *,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """入口：画像（+可选微调）→ 完整可渲染角色包。

    ``overrides`` 支持微调滑杆字段：``hair_style`` / ``hair_tone`` / ``hue_shift``
    （色相偏移，暂以明度偏移近似）/ ``outfit`` / ``mouth`` / ``eye``。
    **AI 底稿永远可一键还原**：调用方只要丢弃 overrides 重建即可（``base_signature``
    始终随包返回，供前端「还原」按钮比对）。
    """
    portrait = build_portrait(raw_portrait)
    base = map_portrait(portrait)
    base_signature = dict(base.signature())
    base_fingerprint = base.fingerprint

    tuned = _apply_overrides(base, overrides or {})
    layers = compose_layers(tuned)
    matrix = _flatten(layers)

    return {
        # 底稿指纹：谁的画像（微调不改变）
        "fingerprint": base_fingerprint,
        # 呈现指纹：现在长什么样（微调会改变）——分享卡短码用这个
        "params_fingerprint": tuned.params_fingerprint(),
        "engine_version": ENGINE_VERSION,
        "params": tuned.to_dict(),
        "base_signature": base_signature,
        "tuned": bool(overrides),
        "layers": layers,
        "matrix": matrix,
        "width": AVATAR_WIDTH,
        "height": AVATAR_HEIGHT,
        "palette": tuned.colors,
        "char_palette": char_palette(tuned.colors),
        "param_space_size": PARAM_SPACE_SIZE,
        "advisory": _advisory(tuned, portrait),
    }


def _advisory(params: AvatarParams, portrait: PortraitInput | None = None) -> dict[str, Any]:
    """诚实标注：哪些画像缺项走了中性默认。绝不把默认值伪装成真实画像结论。

    ``pending`` **恒定存在**（完整时为空列表），前端可以无分支地读
    ``advisory.pending.length``——曾经完整态缺这个键，页面会拿到 ``undefined``。

    ``notes`` 恒定存在，专门说明**收到但刻意不影响造型的字段**（性别/年龄档）。
    这两个字段在任务书里是可选输入项，若默默吞掉，用户会以为它们影响了角色；
    因此这里显式说明「已收到但不参与剪影」。

    ``age_band_label`` 恒定存在（无值时为 ``None``），供分享卡与预览展示真实档位。
    """
    labels = {
        "mbti": "MBTI 性格",
        "bazi_element": "八字五行",
        "sun_sign": "太阳星座",
        "moon_sign": "月亮星座",
        "asc_sign": "上升星座",
    }
    received = [
        NON_SILHOUETTE_FIELDS[key]
        for key in ("gender", "age_band")
        if portrait is not None and getattr(portrait, key, None)
    ]
    if received:
        notes = "、".join(received) + "已收到，但刻意不改变角色剪影"\
                 "（角色统一为 1:1.2 Q 版头身比，不按性别/年龄分化）。"
    else:
        notes = "性别与年龄档为可选信息，未填写也不影响角色生成。"

    if not params.missing:
        return {
            "complete": True,
            "pending": [],
            "note": "画像完整，全部维度由你的数据决定。",
            "notes": notes,
            "age_band_label": (
                AGE_BANDS.get(portrait.age_band) if portrait is not None else None
            ),
        }
    names = [labels.get(m, m) for m in params.missing]
    return {
        "complete": False,
        "pending": params.missing,
        "note": "以下画像数据尚未生成，相关维度使用中性默认（非你的真实数据）："
                + "、".join(names),
        "notes": notes,
        "age_band_label": (
            AGE_BANDS.get(portrait.age_band) if portrait is not None else None
        ),
    }


def _apply_overrides(params: AvatarParams, overrides: dict[str, Any]) -> AvatarParams:
    """应用用户微调（不改 base 指纹，只改呈现）。非法值明确报错。"""
    if not overrides:
        return params
    allowed = {"hair_style", "hair_tone", "outfit", "mouth", "eye", "hue_shift"}
    unknown = set(overrides) - allowed
    if unknown:
        raise ValidationFailed(
            "avatar_unknown_override",
            f"Unknown tuning fields: {sorted(unknown)}",
        )
    data = params.to_dict()
    labels = dict(data["labels"])
    colors = dict(data["colors"])

    if (v := overrides.get("hair_style")) is not None:
        if v not in HAIR_STYLES:
            raise ValidationFailed("avatar_bad_hair_style", f"Unknown hair style: {v!r}")
        data["hair_style"] = v
        labels["hair_style"] = HAIR_STYLE_LABELS[v]
    if (v := overrides.get("hair_tone")) is not None:
        if v not in HAIR_TONE_HEX:
            raise ValidationFailed("avatar_bad_hair_tone", f"Unknown hair tone: {v!r}")
        data["hair_tone"] = v
        colors["hair"] = HAIR_TONE_HEX[v]
        colors["hair_shadow"] = _shade(HAIR_TONE_HEX[v], 0.72)
    if (v := overrides.get("outfit")) is not None:
        if v not in OUTFITS:
            raise ValidationFailed("avatar_bad_outfit", f"Unknown outfit: {v!r}")
        data["outfit"] = v
        labels["outfit"] = OUTFIT_LABELS[v]
    if (v := overrides.get("mouth")) is not None:
        if v not in MOUTH_STYLES:
            raise ValidationFailed("avatar_bad_mouth", f"Unknown mouth: {v!r}")
        data["mouth"] = v
        labels["mouth"] = MOUTH_LABELS[v]
    if (v := overrides.get("eye")) is not None:
        if v not in EYE_STYLES:
            raise ValidationFailed("avatar_bad_eye", f"Unknown eye: {v!r}")
        data["eye"] = v
        labels["eye"] = EYE_LABELS[v]
    if (v := overrides.get("hue_shift")) is not None:
        if not isinstance(v, int) or isinstance(v, bool) or not (-2 <= v <= 2):
            raise ValidationFailed("avatar_bad_hue_shift", "hue_shift must be an int in [-2, 2]")
        factor = 1.0 + v * 0.08
        for key in ("outfit_base", "outfit_shadow", "outfit_highlight", "cape"):
            colors[key] = _shade(colors[key], factor)

    return AvatarParams(
        hair_style=data["hair_style"],
        hair_tone=data["hair_tone"],
        palette_id=data["palette_id"],
        eye=data["eye"],
        mouth=data["mouth"],
        accessory=data["accessory"],
        outfit=data["outfit"],
        emblem=data["emblem"],
        texture=data["texture"],
        height_scale=data["height_scale"],
        colors=colors,
        labels=labels,
        sources=data["sources"],
        missing=data["missing"],
        fingerprint=data["fingerprint"],
    )


def _flatten(layers: dict[str, list[str]]) -> list[str]:
    """把 8 层压平为 32 行 24 列单矩阵（上→下按 LAYER_NAMES 顺序覆盖）。

    前端可直接把 ``matrix`` 交给 ``matrixToRgba`` 渲染，也可按 ``layers`` 分层做
    独立动效（呼吸/眨眼）。
    """
    grid = PixelCanvas()
    for name in LAYER_NAMES:
        for y, row in enumerate(layers.get(name, [])):
            for x, ch in enumerate(row):
                if ch != ".":
                    grid.px(x, y, ch)
    return grid.rows()


# ======================================================================
# 分享卡渲染数据（PNG 由前端 canvas 合成；此处只出「数据」）
# ======================================================================

#: 卡面文案模板（按 MBTI 语气取向；任务书 §2.4）
SHARE_CAPTIONS: dict[str, str] = {
    "EI": "全世界只有一个由我的八字和 MBTI 生成的小人。",
    "SN": "它是我把性格拆成像素之后，长出来的样子。",
    "TF": "出生时间和性格，都是它身上的一块像素。",
    "JP": "十六型之中只有这一型，被我生成出来了。",
    "default": "这是我的专属像素小人 —— 世界上不会再有第二个。",
}

#: 卡面可勾选的画像徽章（白名单；未勾选的一律不出现，隐私红线）
SHARE_BADGE_FIELDS: tuple[str, ...] = (
    "mbti", "element", "day_master", "sun_sign", "moon_sign", "asc_sign", "name",
)


def build_share_card(
    raw_portrait: dict[str, Any] | None,
    selected_badges: list[str],
    *,
    display_name: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """分享卡渲染数据。**默认不含任何私人数据**：只有 ``selected_badges`` 命中的字段上卡。

    ``selected_badges`` 里出现白名单外的字段 → 明确报错（不静默忽略，
    否则用户以为没上卡其实上了，或反之）。
    """
    unknown = [b for b in selected_badges if b not in SHARE_BADGE_FIELDS]
    if unknown:
        raise ValidationFailed(
            "avatar_unknown_badge",
            f"Badge fields not allowed on a share card: {sorted(unknown)}",
        )
    portrait = build_portrait(raw_portrait)
    avatar = build_avatar(raw_portrait, overrides=overrides)

    badges: list[dict[str, str]] = []
    if "mbti" in selected_badges and portrait.mbti:
        badges.append({"field": "mbti", "label": "性格类型", "value": portrait.mbti})
    if "element" in selected_badges and portrait.bazi_element:
        badges.append({"field": "element", "label": "五行主色", "value": portrait.bazi_element})
    if "day_master" in selected_badges and portrait.bazi_day_master:
        badges.append({"field": "day_master", "label": "日主", "value": portrait.bazi_day_master})
    if "sun_sign" in selected_badges and portrait.sun_sign:
        badges.append({"field": "sun_sign", "label": "太阳星座", "value": ZODIAC_CN[portrait.sun_sign]})
    if "moon_sign" in selected_badges and portrait.moon_sign:
        badges.append({"field": "moon_sign", "label": "月亮星座", "value": ZODIAC_CN[portrait.moon_sign]})
    if "asc_sign" in selected_badges and portrait.asc_sign:
        badges.append({"field": "asc_sign", "label": "上升星座", "value": ZODIAC_CN[portrait.asc_sign]})
    if "name" in selected_badges:
        nm = (display_name if display_name is not None else portrait.name) or ""
        if nm:
            badges.append({"field": "name", "label": "昵称", "value": nm})

    mbti = portrait.mbti or ""
    caption_key = mbti[0] + mbti[3] if len(mbti) == 4 else "default"
    caption = SHARE_CAPTIONS.get(caption_key) or SHARE_CAPTIONS["default"]

    return {
        "width": 720,
        "height": 960,
        "avatar": {
            "matrix": avatar["matrix"],
            "layers": avatar["layers"],
            "palette": avatar["palette"],
            "char_palette": avatar["char_palette"],
            "width": AVATAR_WIDTH,
            "height": AVATAR_HEIGHT,
        },
        "badges": badges,
        "caption": caption,
        # 短码用**呈现指纹**（params_fingerprint）：同一人微调出的不同版本能被区分
        "fingerprint_short": avatar["params_fingerprint"][:8].upper(),
        "base_fingerprint_short": avatar["fingerprint"][:8].upper(),
        "tuned": avatar["tuned"],
        "brand": {"product": "Find Yourself", "tagline": "本地优先 · 你的像素小人"},
        # 隐私声明随卡下发，前端必须展示（本地优先卖点）
        "privacy_note": "分享卡只包含你勾选的项目；画像数据全程留在你的电脑，未上传。",
        "excluded_fields": [f for f in SHARE_BADGE_FIELDS if f not in selected_badges],
    }
