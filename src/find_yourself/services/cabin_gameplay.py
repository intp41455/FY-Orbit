"""数码小屋玩法循环与背景探险 (W2 · cabin_gameplay).

这是「养成 + 轻探险」核心循环的**唯一结算真源**（任务书 §3 服务端权威）：
刷新时间、产出、随机事件、亲密度、任务进度、离线收益全部在服务端算完，
前端只渲染服务端返回的结算明细（+什么、为什么）。这既是防作弊，
也是诚实原则在游戏里的形态——前端**没有**任何本地结算分支。

核心循环（任务书 §1）::

    动机（宠物心情/小屋等级）→ 行动（探险采集/日常照料/制造）
      → 奖励（材料+金币+亲密度）→ 投入（家具解锁/小屋升级/亲密度成长）→ 更强动机

设计基准：`docs/handoff-tasks/11-游戏对标拆解与设计基准.md`
  * DNA-3 好感度是内容引擎 → :data:`PREFERENCES` 个性化喜好表 + 图鉴接口；
  * DNA-5 自主生命感 → :func:`_roll_companion` 离场行为状态机 + 证据；
  * DNA-7 制造闭环 → 材料 → :data:`BLUEPRINTS` 图纸 → 家具解锁；
  * DNA-8 即时反馈 → 每次结算返回 ``feedback``（≤100ms 弹出，零惩罚）；
  * DNA-9 主题一致性 → 探险点/材料按 :data:`THEMES` 白名单约束，不串戏。

时间：全部 UTC（``db.types.utcnow``）。「每日」语义按 **Asia/Shanghai 固定
偏移 +08:00** 计算（:data:`LOCAL_TZ`）——本机是 Windows，直接用 zoneinfo
会依赖 tzdata，因此这里用固定偏移并在 docstring 明确标注，避免部署环境差异
导致「每日重置」时刻漂移。

ORM 模型放在本模块而非 ``db/``：任务书 W2 所有权只授予
``services/cabin_gameplay.py`` / ``api/routes/cabin_gameplay.py`` / migration，
``api/app.py`` 与 ``db/workbench_models.py`` 均不在授权范围内。模型在
``api.routes`` 导入链上完成注册，而 ``Base.metadata.create_all`` 在应用
lifespan 中于路由导入之后才执行，因此建表时该表必然已在 metadata 内
（等价于 W1 的 ``cabin_interiors``，只是定义位置不同）。
"""

from __future__ import annotations

import copy
import hashlib
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..db.base import Base
from ..db.types import TZDateTime, utcnow
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

#: 「每日」语义使用的固定 Asia/Shanghai 偏移（UTC+8），无 tzdata 依赖。
LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

#: 合法 action 白名单。任务书 §1.6 要求的 5 个 + 11 号文档制造闭环所需的
#: ``craft``（材料→图纸→家具）。白名单之外一律 422，不做「宽容降级」。
GAMEPLAY_ACTIONS: frozenset[str] = frozenset(
    {"explore", "feed", "water", "clean", "claim", "craft"}
)

MAX_MATERIAL_QTY = 99
MAX_COINS = 1_000_000_000
MAX_INTIMACY = 100
MIN_INTIMACY = 0
MIN_HOUSE_LEVEL = 1
MAX_HOUSE_LEVEL = 5

#: 亲密度闲置衰减：每满 24h 无互动 -1，单次结算最多扣 5（克制，不制造焦虑）。
INTIMACY_DECAY_PER_DAY = 1
INTIMACY_DECAY_MAX_PER_SETTLE = 5

#: 离线收益结算上限 24h（任务书 §1.7），超出部分**如实告知**被截断。
OFFLINE_CAP_HOURS = 24
#: 低于该离线时长不弹「离线期间」提示，避免每次刷新都打扰。
OFFLINE_NOTICE_MINUTES = 30

#: 连续登录判定窗口（同一天多次 GET 只算一次登录）。
LOGIN_STREAK_GAP_HOURS = 48


# ----------------------------------------------------------------------
# 主题与材料（DNA-9 主题一致性：每主题独立物件集，不串戏）
# ----------------------------------------------------------------------

#: 5 个背景主题 id，与前端 ``CABIN_BACKGROUNDS`` 一致。
THEMES: tuple[str, ...] = ("forest", "garden", "stream", "field", "planet")

#: material id -> (label, theme, tier)
#: 每主题 3 种可采集物（11 号文档 §3.3「每主题可采集物 ≥3」），
#: 合计 15 种；每种背包上限 :data:`MAX_MATERIAL_QTY` = 99。
#:
#: ⚠️ 与任务书 §1.4「8 种材料」的冲突：本模块按 11 号文档（验收基准）取 15 种，
#: 且完整包含 §1.1 点名的 11 种。已列入交付报告请产品裁决。
MATERIALS: dict[str, tuple[str, str, int]] = {
    # forest 树林
    "pinecone": ("松果", "forest", 1),
    "mushroom": ("蘑菇", "forest", 1),
    "wood": ("木材", "forest", 2),
    # garden 花园
    "flower": ("花", "garden", 1),
    "seed": ("种子", "garden", 1),
    "honey": ("蜂蜜", "garden", 2),
    # stream 小溪
    "fish": ("鱼", "stream", 1),
    "pebble": ("鹅卵石", "stream", 1),
    "moss": ("水草", "stream", 2),
    # field 田野
    "wheat": ("麦穗", "field", 1),
    "firefly_jar": ("萤火虫罐", "field", 2),
    "pumpkin": ("南瓜", "field", 2),
    # planet 宇宙
    "stardust": ("星尘", "planet", 1),
    "crystal": ("晶体", "planet", 2),
    "moonstone": ("月石", "planet", 3),
}

#: 可喂食材料（喂宠物时优先消耗这些）。
FOOD_MATERIALS: frozenset[str] = frozenset(
    {"mushroom", "honey", "fish", "wheat", "pumpkin", "pebble", "seed"}
)

#: 可浇水的材料（花园产出）。
WATERABLE_MATERIALS: frozenset[str] = frozenset({"flower", "seed", "honey", "moss"})


# ----------------------------------------------------------------------
# 探险点（每主题 ≥4 个，任务书 §1.1）
# ----------------------------------------------------------------------

#: spot dict: id, theme, label, material, qty, coins, intimacy,
#:             cooldown_hours(2-4 真实时间), fx/fy(渲染归一化坐标), dust(是否可能积灰)
SPOTS: tuple[dict[str, Any], ...] = (
    # --- forest 树林 ---
    {"id": "forest_pine", "theme": "forest", "label": "老松树", "material": "pinecone",
     "qty": 2, "coins": 3, "intimacy": 1, "cooldown_hours": 2, "fx": 0.18, "fy": 0.62, "dust": True},
    {"id": "forest_mush", "theme": "forest", "label": "蘑菇圈", "material": "mushroom",
     "qty": 1, "coins": 2, "intimacy": 1, "cooldown_hours": 2, "fx": 0.36, "fy": 0.74, "dust": False},
    {"id": "forest_log", "theme": "forest", "label": "倒木", "material": "wood",
     "qty": 1, "coins": 5, "intimacy": 0, "cooldown_hours": 4, "fx": 0.72, "fy": 0.68, "dust": True},
    {"id": "forest_hollow", "theme": "forest", "label": "树洞", "material": "wood",
     "qty": 2, "coins": 4, "intimacy": 1, "cooldown_hours": 3, "fx": 0.55, "fy": 0.55, "dust": False},
    # --- garden 花园 ---
    {"id": "garden_rose", "theme": "garden", "label": "玫瑰丛", "material": "flower",
     "qty": 2, "coins": 3, "intimacy": 1, "cooldown_hours": 2, "fx": 0.22, "fy": 0.66, "dust": False},
    {"id": "garden_pot", "theme": "garden", "label": "花盆", "material": "seed",
     "qty": 3, "coins": 2, "intimacy": 0, "cooldown_hours": 2, "fx": 0.44, "fy": 0.72, "dust": True},
    {"id": "garden_bee", "theme": "garden", "label": "蜂箱", "material": "honey",
     "qty": 1, "coins": 6, "intimacy": 1, "cooldown_hours": 4, "fx": 0.78, "fy": 0.6, "dust": False},
    {"id": "garden_pond", "theme": "garden", "label": "小水洼", "material": "moss",
     "qty": 1, "coins": 3, "intimacy": 1, "cooldown_hours": 3, "fx": 0.6, "fy": 0.78, "dust": False},
    # --- stream 小溪 ---
    {"id": "stream_pool", "theme": "stream", "label": "深水潭", "material": "fish",
     "qty": 1, "coins": 5, "intimacy": 1, "cooldown_hours": 2, "fx": 0.3, "fy": 0.7, "dust": False},
    {"id": "stream_bank", "theme": "stream", "label": "卵石滩", "material": "pebble",
     "qty": 3, "coins": 2, "intimacy": 0, "cooldown_hours": 2, "fx": 0.52, "fy": 0.8, "dust": True},
    {"id": "stream_reed", "theme": "stream", "label": "水草丛", "material": "moss",
     "qty": 2, "coins": 3, "intimacy": 1, "cooldown_hours": 3, "fx": 0.7, "fy": 0.63, "dust": False},
    {"id": "stream_deep", "theme": "stream", "label": "溪底", "material": "fish",
     "qty": 2, "coins": 7, "intimacy": 1, "cooldown_hours": 4, "fx": 0.85, "fy": 0.75, "dust": False},
    # --- field 田野 ---
    {"id": "field_stalk", "theme": "field", "label": "麦垛", "material": "wheat",
     "qty": 3, "coins": 3, "intimacy": 0, "cooldown_hours": 2, "fx": 0.2, "fy": 0.68, "dust": False},
    {"id": "field_jar", "theme": "field", "label": "萤火虫罐", "material": "firefly_jar",
     "qty": 1, "coins": 8, "intimacy": 2, "cooldown_hours": 4, "fx": 0.48, "fy": 0.58, "dust": False},
    {"id": "field_vine", "theme": "field", "label": "南瓜藤", "material": "pumpkin",
     "qty": 1, "coins": 6, "intimacy": 1, "cooldown_hours": 3, "fx": 0.68, "fy": 0.74, "dust": True},
    {"id": "field_stone", "theme": "field", "label": "田边石堆", "material": "pebble",
     "qty": 1, "coins": 3, "intimacy": 0, "cooldown_hours": 2, "fx": 0.88, "fy": 0.66, "dust": False},
    # --- planet 宇宙 ---
    {"id": "planet_dust", "theme": "planet", "label": "星尘带", "material": "stardust",
     "qty": 2, "coins": 6, "intimacy": 1, "cooldown_hours": 2, "fx": 0.24, "fy": 0.6, "dust": False},
    {"id": "planet_crack", "theme": "planet", "label": "晶簇裂隙", "material": "crystal",
     "qty": 1, "coins": 9, "intimacy": 1, "cooldown_hours": 4, "fx": 0.56, "fy": 0.66, "dust": False},
    {"id": "planet_rock", "theme": "planet", "label": "月壤", "material": "moonstone",
     "qty": 1, "coins": 12, "intimacy": 2, "cooldown_hours": 4, "fx": 0.76, "fy": 0.72, "dust": True},
    {"id": "planet_halo", "theme": "planet", "label": "光晕", "material": "stardust",
     "qty": 3, "coins": 8, "intimacy": 2, "cooldown_hours": 3, "fx": 0.9, "fy": 0.55, "dust": False},
)

SPOT_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in SPOTS}

#: 屋等级推导阈值——与前端 W1 ``deriveCabinLevel`` 完全一致（3/7/11/16 件）。
#: 服务端用同一套阈值从 ``cabin_interiors`` 反推等级，避免两端等级口径分叉。
LEVEL_THRESHOLDS: tuple[int, ...] = (3, 7, 11, 16)


# ----------------------------------------------------------------------
# 个性化喜好表（DNA-3 / 林中小女巫转译）
# ----------------------------------------------------------------------

#: 性格 id 与前端 ``PERSONALITIES`` 一致：lively / cool / melancholy / chatty。
#: 每条 = (偏好食物材料, 偏好互动, 偏好抚摸部位, 图鉴备注)
PREFERENCES: dict[str, dict[str, tuple[str, str, str, str]]] = {
    "lively": {
        "person": ("honey", "奔跑", "头顶", "一提到跑跳就两眼放光，尾巴摇成螺旋桨。"),
        "pet": ("fish", "追球", "耳后", "对快速滚动的东西毫无抵抗力。"),
    },
        "cool": {
            "person": ("moonstone", "独处", "背", "喜欢安静，靠近时会假装没看见你。"),
            "pet": ("pebble", "晒太阳", "背", "看着高冷，其实会悄悄把鹅卵石推到你脚边。"),
        },
    "melancholy": {
        "person": ("flower", "看雨", "手", "话很少，但会把最好的花插在你桌上。"),
        "pet": ("moss", "发呆", "肚子", "常常望着窗外，像在想很久以前的事。"),
    },
    "chatty": {
        "person": ("wheat", "聊天", "脸", "一天能讲三百件事，一件也记不住。"),
        "pet": ("pumpkin", "说话", "爪子", "会盯着你念叨，念到你先开口为止。"),
    },
}

#: 无性格时的兜底（诚实：未知性格不报错，按 chatty 处理并在响应里标注）。
FALLBACK_PERSONALITY = "chatty"


# ----------------------------------------------------------------------
# 蓝图：材料 → 图纸 → 家具（DNA-7 制造闭环）
# ----------------------------------------------------------------------

#: furniture_id -> (label, {material: qty}, unlock_level)
#: furniture id 必须存在于 W1 的 ``FURNITURE_CATALOG``（后端白名单），
#: 由 ``tests/unit/test_cabin_gameplay.py`` 做前后端一致性比对。
BLUEPRINTS: dict[str, tuple[str, dict[str, int], int]] = {
    "herb_shelf": ("草药架", {"wood": 3, "flower": 2, "moss": 1}, 1),
    "snow_lamp": ("冰晶灯", {"crystal": 2, "pebble": 2, "stardust": 1}, 2),
    "crystal_tree": ("水晶树", {"crystal": 3, "stardust": 3, "moonstone": 1}, 3),
}


# ----------------------------------------------------------------------
# 随机事件（任务书 §1.3，≥6 种；文案结合性格字段）
# ----------------------------------------------------------------------

#: event id -> (weight, 标题, 文案模板)
#: 模板占位：``{person}`` 小人名 / ``{pet}`` 宠物名 / ``{material}`` / ``{label}``
EVENTS: dict[str, tuple[int, str, str]] = {
    "double_find": (22, "小发现", "在{label}旁边多捡到一份{material}，今天的运气好像偏向我。"),
    "personality_line": (18, "小人的碎碎念", "{person}忽然停下来说：「{line}」"),
    "pet_gift": (16, "宠物带回礼物", "{pet}从外面叼回来一样东西，蹭了蹭你的脚。"),
    "lost_firefly": (10, "迷路的萤火虫", "一只{theme_label}的萤火虫跟上了{pet}，它绕着你的鞋尖转了 30 秒才走。"),
    "treasure_chest": (8, "宝箱", "斑驳的木箱里是一枚旧硬币——还需要一把钥匙才能打开更大的那只。"),
    "coin_find": (14, "零钱", "草丛里翻出 {coins} 枚硬币，叮当作响。"),
    "nothing": (12, "一无所获", "这里现在什么也没有了。诚实地说：真的没有。"),
}

#: 「小人的碎碎念」按性格给不同台词（每种性格 ≥3 条，保证新鲜度）。
PERSONALITY_LINES: dict[str, tuple[str, ...]] = {
    "lively": ("跑一圈再回来！", "我数到三就出发——一、二、", "今天的风是甜的！"),
    "cool": ("……还行。", "不必跟着我。", "安静一会儿。"),
    "melancholy": ("有点想看雨。", "花开了就好。", "你在的时候屋子更暖。"),
    "chatty": ("我跟你说，我刚才数了数这棵树！", "等下等下，我还有一件事！", "对了对了——"),
}

PERSONALITY_FALLBACK_LINES: tuple[str, ...] = (
    "今天也一起加油吧。",
    "外面风有点大。",
    "我在这里等你。",
)


# ----------------------------------------------------------------------
# 任务（新手链 / 日常 / 居民愿望）
# ----------------------------------------------------------------------

#: 新手引导链 5 步（任务书 §1.5）。``event`` 为进度事件名，``target`` 为阈值。
TUTORIAL_STEPS: tuple[dict[str, Any], ...] = (
    {"id": "t1", "title": "采集一次", "desc": "点一个发光热点，收取第一份材料。",
     "event": "explore", "target": 1, "reward": {"coins": 10, "intimacy": 2, "items": {"seed": 2}},
     "reward_label": "种子 ×2 + 10 金币"},
    {"id": "t2", "title": "喂一次宠物", "desc": "喂食会提升亲密度，命中喜好加成翻倍。",
     "event": "feed", "target": 1, "reward": {"coins": 8, "intimacy": 3, "items": {"mushroom": 1}},
     "reward_label": "蘑菇 ×1 + 8 金币"},
    {"id": "t3", "title": "布置一件家具", "desc": "进屋添加一件家具，装饰也是成长。",
     "event": "decorate", "target": 1, "reward": {"coins": 15, "intimacy": 2, "items": {"flower": 1}},
     "reward_label": "花 ×1 + 15 金币"},
    {"id": "t4", "title": "完成三次探险", "desc": "不同的探险点有不同的产出与节律。",
     "event": "explore", "target": 3, "reward": {"coins": 25, "intimacy": 4, "items": {"wood": 2}},
     "reward_label": "木材 ×2 + 25 金币"},
    {"id": "t5", "title": "小屋升到 Lv2", "desc": "多摆几件家具，小屋会自己升级。",
     "event": "level", "target": 2, "reward": {"coins": 40, "intimacy": 5, "items": {"honey": 1}},
     "reward_label": "蜂蜜 ×1 + 40 金币"},
)

#: 每日任务池（每次按日期挑 3 个）。每条 = (id, title, desc, event, target, reward)
DAILY_POOL: tuple[dict[str, Any], ...] = (
    {"id": "d_gather3", "title": "采集 3 次", "desc": "任何主题都算。", "event": "explore", "target": 3,
     "reward": {"coins": 18, "intimacy": 2, "items": {"pebble": 2}}, "reward_label": "鹅卵石 ×2 + 18 金币"},
    {"id": "d_feed2", "title": "喂食 2 次", "desc": "命中喜好有双倍亲密度。", "event": "feed", "target": 2,
     "reward": {"coins": 15, "intimacy": 4, "items": {"fish": 1}}, "reward_label": "鱼 ×1 + 15 金币"},
    {"id": "d_clean2", "title": "打扫 2 次", "desc": "擦掉灰尘，屋子亮一点。", "event": "clean", "target": 2,
     "reward": {"coins": 14, "intimacy": 2, "items": {"wood": 1}}, "reward_label": "木材 ×1 + 14 金币"},
    {"id": "d_water1", "title": "浇花 1 次", "desc": "浇水后该点产出翻倍。", "event": "water", "target": 1,
     "reward": {"coins": 16, "intimacy": 3, "items": {"flower": 2}}, "reward_label": "花 ×2 + 16 金币"},
    {"id": "d_event1", "title": "触发 1 次随机事件", "desc": "每次探险都可能遇到。", "event": "event", "target": 1,
     "reward": {"coins": 20, "intimacy": 2, "items": {"stardust": 1}}, "reward_label": "星尘 ×1 + 20 金币"},
    {"id": "d_craft1", "title": "制造 1 件家具", "desc": "材料换图纸，图纸换家具。", "event": "craft", "target": 1,
     "reward": {"coins": 30, "intimacy": 4, "items": {"crystal": 1}}, "reward_label": "晶体 ×1 + 30 金币"},
    {"id": "d_coin60", "title": "累计赚 60 金币", "desc": "探险、照料、任务都算。", "event": "coins", "target": 60,
     "reward": {"coins": 25, "intimacy": 3, "items": {"moonstone": 1}}, "reward_label": "月石 ×1 + 25 金币"},
)

#: 居民愿望池（花园故事转译）。``kind`` 决定达成方式。
WISH_POOL: tuple[dict[str, Any], ...] = (
    {"id": "w_fish3", "kind": "material", "target": "fish", "need": 3,
     "text": "想吃小鱼干……桌上那三条可以吗？", "reward_label": "亲密度 +6 + 20 金币"},
    {"id": "w_flower2", "kind": "material", "target": "flower", "need": 2,
     "text": "想把两朵花摆在窗边，我可以自己插好。", "reward_label": "亲密度 +5 + 18 金币"},
    {"id": "w_rug", "kind": "craft", "target": "rug_large", "need": 1,
     "text": "想要一块大地毯，冬天趴着一定很舒服。", "reward_label": "亲密度 +8 + 40 金币"},
    {"id": "w_stove", "kind": "craft", "target": "stove", "need": 1,
     "text": "有个炉子的话，下雨天也不怕。", "reward_label": "亲密度 +8 + 40 金币"},
    {"id": "w_wood5", "kind": "material", "target": "wood", "need": 5,
     "text": "攒够木头，我想自己动手做点东西。", "reward_label": "亲密度 +6 + 25 金币"},
    {"id": "w_crystal", "kind": "craft", "target": "crystal_tree", "need": 1,
     "text": "想要一棵水晶树……我保证每天给它擦灰。", "reward_label": "亲密度 +10 + 60 金币"},
)


# ----------------------------------------------------------------------
# ORM
# ----------------------------------------------------------------------


class CabinSave(Base):
    """数码小屋玩法存档 (W2 · cabin_save) —— 每 owner 一行。

    任务书 §1.6 要求的 9 个字段全部存在：``owner_id``（主键）、``materials``、
    ``coins``、``intimacy``、``house_level``、``quest_state``、``spots_state``、
    ``daily_seed``、``version``。

    另增 4 类运行必需字段（任务书未禁止，且缺了无法诚实实现离线/制造/行为）：

    * ``settings`` —— 玩家偏好（当前主题等），``PUT /api/cabin/save`` 唯一可写项；
    * ``unlocked_furniture`` —— 制造/任务解锁的家具 id 集合；
    * ``companion_state`` / ``dust_state`` —— 自主行为证据与灰尘清洁点；
    * ``last_seen_at`` / ``last_interaction_at`` / ``chest_keys`` /
      ``login_streak`` / ``last_login_date`` —— 离线结算、闲置衰减、宝箱钥匙与
      连续登录。

    本表**不接受**客户端直接写入 ``materials``/``coins``/``intimacy``/
    ``house_level``：这些只由服务端结算函数改动（诚实 + 防作弊）。
    """

    __tablename__ = "cabin_saves"

    owner_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    materials: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    intimacy: Mapped[int] = mapped_column(Integer, default=0)
    house_level: Mapped[int] = mapped_column(Integer, default=MIN_HOUSE_LEVEL)
    quest_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    spots_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    daily_seed: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    unlocked_furniture: Mapped[list[str]] = mapped_column(JSON, default=list)
    companion_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dust_state: Mapped[list[str]] = mapped_column(JSON, default=list)
    chest_keys: Mapped[int] = mapped_column(Integer, default=0)
    login_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_login_date: Mapped[str] = mapped_column(String(10), default="")

    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_interaction_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("coins >= 0", name="cabin_save_coins_nonnegative"),
        CheckConstraint(
            f"intimacy >= {MIN_INTIMACY} AND intimacy <= {MAX_INTIMACY}",
            name="cabin_save_intimacy_range",
        ),
        CheckConstraint(
            f"house_level >= {MIN_HOUSE_LEVEL} AND house_level <= {MAX_HOUSE_LEVEL}",
            name="cabin_save_level_range",
        ),
        CheckConstraint("version >= 1", name="cabin_save_version_positive"),
        CheckConstraint("chest_keys >= 0", name="cabin_save_keys_nonnegative"),
    )


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _local_date(now: datetime) -> str:
    """Asia/Shanghai（固定 +08:00）日历日，用于「每日重置」判断。"""
    return now.astimezone(LOCAL_TZ).date().isoformat()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _daily_seed(owner_id: str, local_date: str) -> str:
    """按 (owner, 日期) 派生确定性 seed —— 每日事件序列可复现（任务书 §3）。"""
    raw = f"{owner_id}|{local_date}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _rng(seed: str) -> random.Random:
    """确定性随机源。**禁止**全局 ``random``（任务书 §3）。"""
    return random.Random(seed)


def _personality(raw: Any) -> str:
    return raw if isinstance(raw, str) and raw in PREFERENCES else FALLBACK_PERSONALITY


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


# ----------------------------------------------------------------------
# 服务
# ----------------------------------------------------------------------


class CabinGameplayService:
    """数码小屋玩法结算（owner 私有，服务端权威）。"""

    def __init__(self, db: Any, audit: Any | None = None):
        self._db = db
        self._audit = audit

    # -- 鉴权 ------------------------------------------------------------
    def _require_owner(self, actor: Actor) -> str:
        actor.require_authenticated()
        if actor.subject_type != "owner":
            raise PermissionDenied(
                "owner_only", "Cabin gameplay saves are owner-private", 403
            )
        if not actor.owner_id:
            raise PermissionDenied("owner_required", "Missing owner identity", 403)
        return actor.owner_id

    def _row(self, owner_id: str) -> CabinSave | None:
        return self._db.get(CabinSave, owner_id)

    def _new_row(self, owner_id: str, now: datetime) -> CabinSave:
        return CabinSave(
            owner_id=owner_id,
            materials={},
            coins=0,
            intimacy=0,
            house_level=MIN_HOUSE_LEVEL,
            quest_state={},
            spots_state={},
            daily_seed="",
            version=1,
            settings={"active_theme": "forest"},
            unlocked_furniture=[],
            companion_state={},
            dust_state=[],
            chest_keys=0,
            login_streak=0,
            last_login_date="",
            last_seen_at=now,
        )

    # -- 家具总数（跨服务只读，用于等级推导） -----------------------------
    def _furniture_count(self, owner_id: str) -> int:
        """统计该 owner 在所有房屋模板下已放置的家具件数。

        只读 W1 的 ``cabin_interiors`` 表，不修改它。表不存在时返回 0
        （测试库/未迁移环境不因此崩溃，等级显示为 Lv1，诚实）。
        """
        from sqlalchemy import select

        from ..db.workbench_models import CabinInterior  # 局部导入避免循环

        try:
            rows = (
                self._db.execute(
                    select(CabinInterior.layout).where(CabinInterior.owner_id == owner_id)
                )
                .scalars()
                .all()
            )
        except Exception:  # noqa: BLE001 —— 表不存在时按 0 件处理（诚实降级）
            return 0
        count = 0
        for layout in rows:
            if isinstance(layout, dict):
                items = layout.get("items")
                if isinstance(items, list):
                    count += len(items)
        return count

    @staticmethod
    def _level_for_count(count: int) -> int:
        level = MIN_HOUSE_LEVEL
        for idx, threshold in enumerate(LEVEL_THRESHOLDS, start=2):
            if count >= threshold:
                level = idx
        return level

    def _sync_level(self, row: CabinSave) -> tuple[int, int]:
        """按家具件数回填 house_level，返回 ``(old_level, new_level)``。

        同时在这里记账新手链 t3 的 ``decorate`` 进度——家具件数是**派生状态**，
        由 W1 的 ``CabinInterior`` 表决定，W2 无法拦截布置动作，只能读表回填。
        本方法是家具件数的唯一漏斗（读取存档与执行动作两条路径都经过它），
        因此 t3 的进度不会漏记。
        """
        count = self._furniture_count(row.owner_id)
        target = self._level_for_count(count)
        old = row.house_level
        if target != old:
            row.house_level = target
        self._sync_decorate_progress(row, count)
        return old, target

    def _sync_decorate_progress(self, row: CabinSave, count: int) -> None:
        """按「家具件数 − 建档基线」回填 t3 的 decorate 进度（绝对值，只增不减）。"""
        q = self._quest_dict(row)
        tutorial = q["tutorial"]
        if tutorial.get("completed"):
            return
        baseline = tutorial.get("baseline_items")
        if baseline is None:
            # 建档时未记录基线（旧档迁移）：以当前件数为准，避免把已有家具算成新增。
            tutorial["baseline_items"] = count
            row.quest_state = q
            return
        delta = max(int(count) - int(baseline), 0)
        if delta <= int(tutorial.get("progress", {}).get("decorate", 0)):
            return
        tutorial.setdefault("progress", {})["decorate"] = delta
        row.quest_state = q

    # -- 每日重置 --------------------------------------------------------
    def _ensure_daily(self, row: CabinSave, now: datetime) -> dict[str, Any]:
        """跨日则换 seed、轮换日常、生成新愿望、重置日常进度。

        返回 ``{"rotated": bool, "wish": {...}}``。
        """
        today = _local_date(now)
        rotated = False
        if row.daily_seed != _daily_seed(row.owner_id, today):
            row.daily_seed = _daily_seed(row.owner_id, today)
            rotated = True
            q = self._quest_dict(row)
            q["daily"] = {
                "date": today,
                "ids": self._pick_dailies(row.daily_seed),
                "progress": {},
                "claimed": [],
            }
            # 连续登录 → 宝箱钥匙（任务书 §1.3「钥匙来自连续登录」）。
            if row.last_login_date == today:
                pass
            else:
                yesterday = (now.astimezone(LOCAL_TZ) - timedelta(days=1)).date().isoformat()
                row.login_streak = row.login_streak + 1 if row.last_login_date == yesterday else 1
                row.last_login_date = today
                if row.login_streak >= 2:
                    row.chest_keys = _clamp(row.chest_keys + 1, 0, 99)
            q["wish"] = self._make_wish(row.daily_seed)
            row.quest_state = q
        return {"rotated": rotated, "wish": self._quest_dict(row).get("wish", {})}

    def _pick_dailies(self, seed: str) -> list[str]:
        rng = _rng(f"{seed}|daily")
        ids = [d["id"] for d in DAILY_POOL]
        rng.shuffle(ids)
        return ids[:3]

    def _make_wish(self, seed: str) -> dict[str, Any]:
        rng = _rng(f"{seed}|wish")
        w = WISH_POOL[rng.randrange(len(WISH_POOL))]
        return {
            "id": w["id"],
            "kind": w["kind"],
            "target": w["target"],
            "need": w["need"],
            "text": w["text"],
            "reward_label": w["reward_label"],
            "progress": 0,
            "done": False,
            "claimed": False,
        }

    def _quest_dict(self, row: CabinSave) -> dict[str, Any]:
        """取出 quest_state 的**可变副本**。

        SQLAlchemy 的 JSON 列不会追踪原地修改（in-place mutation）：若在旧对象上
        改完再整体赋回，早先写入的键可能被后续一次「读-改-写」用旧快照覆盖掉
        （这正是 ``care`` 首次照料标记丢失的原因）。这里统一返回
        ``dict(...)`` 浅拷贝 + 深拷贝嵌套 dict/list，调用方改完显式回写
        ``row.quest_state = q``，从根本上避免互相覆盖。
        """
        raw = row.quest_state
        if not isinstance(raw, dict):
            raw = {}
        q: dict[str, Any] = copy.deepcopy(raw)
        tutorial = q.get("tutorial")
        if not isinstance(tutorial, dict):
            tutorial = {}
            q["tutorial"] = tutorial
        tutorial.setdefault("step", 0)
        tutorial.setdefault("progress", {})
        tutorial.setdefault("claimed", [])
        tutorial.setdefault("completed", False)
        tutorial.setdefault("baseline_items", None)
        return q

    # -- 背包与产出 ------------------------------------------------------
    def _grant_material(
        self, row: CabinSave, material: str, qty: int
    ) -> tuple[int, bool]:
        """加材料并夹到上限；返回 ``(实际入包量, 是否被上限截断)``。"""
        if material not in MATERIALS:
            raise ValidationFailed("cabin_unknown_material", f"Unknown material {material!r}")
        bag = dict(row.materials or {})
        current = int(bag.get(material, 0) or 0)
        granted = max(0, min(int(qty), MAX_MATERIAL_QTY - current))
        bag[material] = current + granted
        row.materials = bag
        return granted, granted < int(qty)

    def _grant_coins(self, row: CabinSave, amount: int) -> int:
        value = _clamp(int(row.coins or 0) + int(amount), 0, MAX_COINS)
        row.coins = value
        return value

    def _grant_intimacy(self, row: CabinSave, amount: int) -> tuple[int, int]:
        """返回 ``(before, after)``（已夹到 [0, 100]）。"""
        before = int(row.intimacy or 0)
        row.intimacy = _clamp(before + int(amount), MIN_INTIMACY, MAX_INTIMACY)
        return before, int(row.intimacy)

    def _apply_idle_decay(self, row: CabinSave, now: datetime) -> int:
        """闲置缓慢降亲密度（单次最多 -5，克制造型不制造焦虑）。"""
        last = _parse_iso(
            (row.companion_state or {}).get("last_interaction")
            if isinstance(row.companion_state, dict)
            else None
        ) or row.last_interaction_at
        if last is None:
            return 0
        hours = (now - last).total_seconds() / 3600.0
        if hours < 24:
            return 0
        drop = min(INTIMACY_DECAY_MAX_PER_SETTLE, int(hours // 24) * INTIMACY_DECAY_PER_DAY)
        if drop <= 0:
            return 0
        before = int(row.intimacy or 0)
        row.intimacy = _clamp(before - drop, MIN_INTIMACY, MAX_INTIMACY)
        return before - int(row.intimacy)

    # -- 探险点 ----------------------------------------------------------
    def _spot_state(self, row: CabinSave) -> dict[str, Any]:
        s = row.spots_state if isinstance(row.spots_state, dict) else {}
        return dict(s)

    def _spot_status(self, row: CabinSave, spot: dict[str, Any], now: datetime) -> dict[str, Any]:
        state = self._spot_state(row).get(spot["id"], {})
        last_at = _parse_iso(state.get("last_collect_at")) if isinstance(state, dict) else None
        ready_at = (
            last_at + timedelta(hours=float(spot["cooldown_hours"])) if last_at else None
        )
        available = ready_at is None or ready_at <= now
        remaining = 0 if ready_at is None else max(0, int((ready_at - now).total_seconds()))
        return {
            "id": spot["id"],
            "theme": spot["theme"],
            "label": spot["label"],
            "material": spot["material"],
            "material_label": MATERIALS[spot["material"]][0],
            "qty": spot["qty"],
            "coins": spot["coins"],
            "intimacy": spot["intimacy"],
            "cooldown_hours": spot["cooldown_hours"],
            "fx": spot["fx"],
            "fy": spot["fy"],
            "available": available,
            "ready_at": _iso(ready_at),
            "remaining_seconds": remaining,
            "collect_count": int(state.get("count", 0)) if isinstance(state, dict) else 0,
            "dust": spot["id"] in (row.dust_state or []),
        }

    def spots_for(self, row: CabinSave, theme: str, now: datetime) -> list[dict[str, Any]]:
        return [
            self._spot_status(row, s, now)
            for s in SPOTS
            if s["theme"] == theme
        ]

    # -- 自主行为（雨世界转译） -----------------------------------------
    def _roll_companion(
        self, row: CabinSave, now: datetime, personality: str
    ) -> dict[str, Any]:
        """按离开时长 + 亲密度决定「刚才干了什么」，并留下可见证据。

        行为多样度随亲密度解锁（亲密度越高，可能的行为越多），
        呼应 11 号文档「行为多样度作为宠物成长维度」。
        """
        state = row.companion_state if isinstance(row.companion_state, dict) else {}
        last_seen = _parse_iso(state.get("last_away_at")) or row.last_seen_at
        if last_seen is not None:
            row.last_seen_at = last_seen
        away_hours = 0.0 if last_seen is None else max(0.0, (now - last_seen).total_seconds() / 3600.0)
        if away_hours < 0.25:
            # 刚离开不到 15 分钟：保持原行为，不重复播报。
            return {
                "behavior": state.get("behavior", "idle"),
                "label": state.get("label", "就在旁边"),
                "away_hours": round(away_hours, 2),
                "trinkets": state.get("trinkets", []),
                "unlocked_count": len(self._behavior_pool(int(row.intimacy or 0))),
            }
        rng = _rng(f"{row.daily_seed}|companion|{int(away_hours * 4)}|{row.owner_id[-6:]}")
        pool = self._behavior_pool(int(row.intimacy or 0))
        behavior = pool[rng.randrange(len(pool))]
        trinkets = list(state.get("trinkets", []))
        if behavior["trinket"] and rng.random() < 0.6:
            trinkets.append(behavior["trinket"])
        new_state = {
            "last_away_at": _iso(now),
            "behavior": behavior["id"],
            "label": behavior["label"],
            "trinkets": trinkets[-5:],
            "last_interaction": state.get("last_interaction")
            or _iso(row.last_interaction_at or now),
        }
        row.companion_state = new_state
        return {
            "behavior": behavior["id"],
            "label": behavior["label"],
            "away_hours": round(away_hours, 2),
            "trinkets": new_state["trinkets"],
            "unlocked_count": len(pool),
        }

    @staticmethod
    def _behavior_pool(intimacy: int) -> list[dict[str, Any]]:
        """亲密度 0/20/50/80 逐级解锁行为（多样度 = 成长维度）。"""
        base = [
            {"id": "sleep", "label": "在窝里睡着了", "trinket": None},
            {"id": "window", "label": "趴在窗边发呆", "trinket": None},
        ]
        if intimacy >= 20:
            base.append({"id": "yarn", "label": "和毛线球玩了一会儿", "trinket": "毛线球"})
        if intimacy >= 50:
            base.append({"id": "butterfly", "label": "追蝴蝶追到院子外面", "trinket": "掉落的鳞粉"})
        if intimacy >= 80:
            base.append({"id": "wander", "label": "自己出门溜达了一圈", "trinket": "一小块奇怪的石头"})
        return base

    # -- 离线收益 -------------------------------------------------------
    def _offline_report(
        self, row: CabinSave, now: datetime, theme: str
    ) -> dict[str, Any] | None:
        last_seen = row.last_seen_at
        if last_seen is None:
            return None
        minutes = (now - last_seen).total_seconds() / 60.0
        if minutes < OFFLINE_NOTICE_MINUTES:
            return None
        capped = minutes > OFFLINE_CAP_HOURS * 60
        effective_hours = min(minutes / 60.0, float(OFFLINE_CAP_HOURS))
        refreshed: list[str] = []
        for spot in SPOTS:
            if spot["theme"] != theme:
                continue
            status = self._spot_status(row, spot, now)
            if status["available"] and status["collect_count"] > 0:
                refreshed.append(status["label"])
        return {
            "away_hours": round(effective_hours, 2),
            "real_away_hours": round(minutes / 60.0, 2),
            "capped": capped,
            "refreshed_spots": refreshed,
            "text": (
                f"离线 {effective_hours:.1f} 小时"
                + ("（已按 24 小时上限结算）" if capped else "")
                + (f"，{len(refreshed)} 个探险点已刷新" if refreshed else "，探险点都还没刷新")
            ),
        }

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def get_save(
        self,
        actor: Actor,
        *,
        theme: str = "forest",
        person_name: str = "小人",
        personality: Any = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        owner_id = self._require_owner(actor)
        if theme not in THEMES:
            raise ValidationFailed("cabin_unknown_theme", f"Unknown theme {theme!r}")
        moment = now or utcnow()
        persona = _personality(personality)

        row = self._row(owner_id)
        created = row is None
        if row is None:
            row = self._new_row(owner_id, moment)
            self._db.add(row)

        daily = self._ensure_daily(row, moment)
        offline = self._offline_report(row, moment, theme)
        companion = self._roll_companion(row, moment, persona)
        old_level, new_level = self._sync_level(row)
        self._advance_quests(row, "level", new_level)

        if created:
            # 新档：给一份起步材料，避免「背包空空、无事可做」的死局。
            for material in ("seed", "mushroom", "pebble"):
                self._grant_material(row, material, 2)
            q = self._quest_dict(row)
            layout = self._furniture_count(owner_id)
            q["tutorial"]["baseline_items"] = layout
            row.quest_state = q

        row.last_seen_at = moment
        self._db.commit()
        self._db.refresh(row)

        return {
            "owner_scoped": True,
            "version": row.version,
            "coins": row.coins,
            "intimacy": row.intimacy,
            "house_level": row.house_level,
            "max_house_level": MAX_HOUSE_LEVEL,
            "materials": dict(row.materials or {}),
            "material_catalog": [
                {"id": mid, "label": label, "theme": theme_id, "tier": tier}
                for mid, (label, theme_id, tier) in MATERIALS.items()
            ],
            "spots": self.spots_for(row, theme, moment),
            "quests": {
                "tutorial": self._quest_dict(row)["tutorial"],
                "daily": self._quest_dict(row).get("daily", {}),
                "wish": daily["wish"],
            },
            "companion": companion,
            "unlocked_furniture": list(row.unlocked_furniture or []),
            "chest_keys": row.chest_keys,
            "login_streak": row.login_streak,
            "dust": list(row.dust_state or []),
            "offline": offline,
            "daily_rotated": daily["rotated"],
            "level_up": (
                {"from": old_level, "to": new_level} if new_level > old_level else None
            ),
            # 传**原始** personality 而非归一化后的 persona，否则 recognized 恒为 True。
            "preferences": self.preference_view(personality),
            "server_time": _iso(moment),
            "local_date": _local_date(moment),
            "settings": dict(row.settings or {}),
        }

    def preference_view(self, personality: Any) -> dict[str, Any]:
        persona = _personality(personality)
        raw = PREFERENCES[persona]
        # 只有「传了字符串且命中白名单」才算识别成功；None/未知值都诚实标 False。
        recognized = isinstance(personality, str) and personality in PREFERENCES
        return {
            "personality": persona,
            "recognized": recognized,
            "person": {
                "food": raw["person"][0],
                "food_label": MATERIALS[raw["person"][0]][0],
                "interaction": raw["person"][1],
                "touch": raw["person"][2],
                "note": raw["person"][3],
            },
            "pet": {
                "food": raw["pet"][0],
                "food_label": MATERIALS[raw["pet"][0]][0],
                "interaction": raw["pet"][1],
                "touch": raw["pet"][2],
                "note": raw["pet"][3],
            },
        }

    def meta(self, actor: Actor) -> dict[str, Any]:
        """玩法静态元数据：数值表/蓝图/事件/任务，供前端与测试对齐单一真源。"""
        self._require_owner(actor)
        return {
            "themes": list(THEMES),
            "materials": [
                {"id": mid, "label": label, "theme": tid, "tier": tier}
                for mid, (label, tid, tier) in MATERIALS.items()
            ],
            "spots": [
                {
                    "id": s["id"], "theme": s["theme"], "label": s["label"],
                    "material": s["material"], "qty": s["qty"], "coins": s["coins"],
                    "intimacy": s["intimacy"], "cooldown_hours": s["cooldown_hours"],
                    "fx": s["fx"], "fy": s["fy"],
                }
                for s in SPOTS
            ],
            "blueprints": {
                fid: {"label": lb, "cost": cost, "unlock_level": lvl}
                for fid, (lb, cost, lvl) in BLUEPRINTS.items()
            },
            "events": {
                eid: {"weight": w, "title": t, "template": tpl}
                for eid, (w, t, tpl) in EVENTS.items()
            },
            "tutorial_steps": [
                # 带出 `event`：进度字典是按事件名记账的（explore/feed/decorate/level），
                # 前端必须知道该步对应哪个事件名才能渲染进度条，否则只能硬编码在前端。
                {"id": s["id"], "title": s["title"], "desc": s["desc"],
                 "event": s["event"], "target": s["target"], "reward_label": s["reward_label"]}
                for s in TUTORIAL_STEPS
            ],
            "daily_pool": [
                {"id": d["id"], "title": d["title"], "desc": d["desc"],
                 "event": d["event"], "target": d["target"], "reward_label": d["reward_label"]}
                for d in DAILY_POOL
            ],
            "limits": {
                "max_material_qty": MAX_MATERIAL_QTY,
                "max_coins": MAX_COINS,
                "max_intimacy": MAX_INTIMACY,
                "max_house_level": MAX_HOUSE_LEVEL,
                "offline_cap_hours": OFFLINE_CAP_HOURS,
                "level_thresholds": list(LEVEL_THRESHOLDS),
            },
            "actions": sorted(GAMEPLAY_ACTIONS),
        }

    # ------------------------------------------------------------------
    # 写入：settings（唯一允许客户端写的部分）
    # ------------------------------------------------------------------
    def put_save(
        self, actor: Actor, payload: Any, expected_version: int, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """只接受玩家偏好类字段。

        **服务端权威**：试图写 ``materials`` / ``coins`` / ``intimacy`` /
        ``house_level`` / ``daily_seed`` 一律 422 —— 数值只能由结算动作产生。
        """
        owner_id = self._require_owner(actor)
        moment = now or utcnow()
        if not isinstance(payload, dict):
            raise ValidationFailed("cabin_invalid_payload", "Payload must be a JSON object")
        forbidden = {
            "materials", "coins", "intimacy", "house_level", "daily_seed",
            "quest_state", "spots_state", "unlocked_furniture", "chest_keys",
            "owner_id", "version",
        }
        bad = sorted(forbidden & set(payload.keys()))
        if bad:
            raise ValidationFailed(
                "cabin_server_authoritative",
                f"Server-authoritative fields cannot be written by the client: {', '.join(bad)}",
            )
        settings = payload.get("settings")
        if settings is not None:
            if not isinstance(settings, dict):
                raise ValidationFailed("cabin_invalid_settings", "settings must be an object")
            unknown = sorted(set(settings.keys()) - {"active_theme"})
            if unknown:
                raise ValidationFailed(
                    "cabin_invalid_settings", f"Unknown settings keys: {', '.join(unknown)}"
                )
            theme = settings.get("active_theme")
            if theme is not None and theme not in THEMES:
                raise ValidationFailed("cabin_unknown_theme", f"Unknown theme {theme!r}")

        row = self._row(owner_id)
        if row is None:
            row = self._new_row(owner_id, moment)
            self._db.add(row)
        elif row.version != expected_version:
            raise Conflict(
                "cabin_version_conflict",
                f"Stored save is at version {row.version}, client sent {expected_version}",
                409,
            )

        if settings is not None:
            merged = dict(row.settings or {})
            merged.update(settings)
            row.settings = merged
        row.version += 1
        self._db.commit()
        self._db.refresh(row)
        return {
            "version": row.version,
            "settings": dict(row.settings or {}),
            "persisted": True,
        }

    # ------------------------------------------------------------------
    # 动作结算
    # ------------------------------------------------------------------
    def perform_action(
        self,
        actor: Actor,
        *,
        action: Any,
        spot_id: Any = None,
        target: Any = None,
        quest_kind: Any = None,
        quest_id: Any = None,
        person_name: str = "小人",
        personality: Any = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        owner_id = self._require_owner(actor)
        if not isinstance(action, str) or action not in GAMEPLAY_ACTIONS:
            raise ValidationFailed(
                "cabin_unknown_action",
                f"Unknown action {action!r}; allowed: {', '.join(sorted(GAMEPLAY_ACTIONS))}",
            )
        moment = now or utcnow()
        persona = _personality(personality)

        row = self._row(owner_id)
        if row is None:
            row = self._new_row(owner_id, moment)
            self._db.add(row)
            for material in ("seed", "mushroom", "pebble"):
                self._grant_material(row, material, 2)
            q = self._quest_dict(row)
            q["tutorial"]["baseline_items"] = self._furniture_count(owner_id)
            row.quest_state = q

        self._ensure_daily(row, moment)
        # 派生状态（家具件数 → 小屋等级 / t3 decorate 进度）必须在 handler **之前**对齐：
        # claim 要读这些进度做校验，若放在 handler 之后就会读到上一轮的旧值。
        _, new_level = self._sync_level(row)
        # t5「小屋升到 Lv2」按等级绝对值记账。与 get_save 同源，
        # 保证「只发动作、不开存档页」也能完成新手链（否则 t5 永远差一步）。
        self._advance_quests(row, "level", new_level)
        handler = {
            "explore": self._act_explore,
            "feed": self._act_feed,
            "water": self._act_water,
            "clean": self._act_clean,
            "claim": self._act_claim,
            "craft": self._act_craft,
        }[action]
        result = handler(
            row, moment, persona, person_name,
            spot_id=spot_id, target=target, quest_kind=quest_kind, quest_id=quest_id,
        )

        self._apply_idle_decay(row, moment)
        row.last_interaction_at = moment
        if isinstance(row.companion_state, dict):
            state = dict(row.companion_state)
            state["last_interaction"] = _iso(moment)
            row.companion_state = state
        row.version += 1
        self._db.commit()
        self._db.refresh(row)

        return {
            "action": action,
            "ok": True,
            "version": row.version,
            "coins": row.coins,
            "intimacy": row.intimacy,
            "house_level": row.house_level,
            "materials": dict(row.materials or {}),
            "unlocked_furniture": list(row.unlocked_furniture or []),
            "chest_keys": row.chest_keys,
            "dust": list(row.dust_state or []),
            "quests": {
                "tutorial": self._quest_dict(row)["tutorial"],
                "daily": self._quest_dict(row).get("daily", {}),
                "wish": self._quest_dict(row).get("wish", {}),
            },
            "server_time": _iso(moment),
            **result,
        }

    # -- explore --------------------------------------------------------
    def _act_explore(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        if not isinstance(spot_id, str) or spot_id not in SPOT_BY_ID:
            raise ValidationFailed(
                "cabin_unknown_spot", f"Unknown exploration spot {spot_id!r}"
            )
        spot = SPOT_BY_ID[spot_id]
        status = self._spot_status(row, spot, now)
        if not status["available"]:
            raise Conflict(
                "cabin_spot_not_ready",
                f"{spot['label']} refreshes in {status['remaining_seconds']}s",
                409,
            )

        spots_state = self._spot_state(row)
        prev = spots_state.get(spot_id, {})
        spots_state[spot_id] = {
            "last_collect_at": _iso(now),
            "count": int(prev.get("count", 0)) + 1 if isinstance(prev, dict) else 1,
        }
        row.spots_state = spots_state

        # 浇过水的可浇材料产出翻倍（日常照料的意义）。
        watered = isinstance(prev, dict) and bool(prev.get("watered"))
        qty = spot["qty"] * (2 if watered and spot["material"] in WATERABLE_MATERIALS else 1)
        granted, capped = self._grant_material(row, spot["material"], qty)
        coins = self._grant_coins(row, spot["coins"])
        before_int, after_int = self._grant_intimacy(row, spot["intimacy"])

        # 消耗一次灰尘（如果有）
        dust = list(row.dust_state or [])
        dust_cleared = spot_id in dust
        if dust_cleared:
            dust.remove(spot_id)
            row.dust_state = dust

        event = self._roll_event(row, now, persona, person_name, spot, granted)
        if event.get("material") or event.get("items"):
            for mid, mqty in (event.get("items") or {}).items():
                self._grant_material(row, mid, mqty)
        if event.get("coins"):
            coins = self._grant_coins(row, int(event["coins"]))
        if event.get("intimacy"):
            before_int, after_int = self._grant_intimacy(row, int(event["intimacy"]))

        gained_items = {spot["material"]: granted}
        for mid, mqty in (event.get("items") or {}).items():
            gained_items[mid] = gained_items.get(mid, 0) + mqty

        progress = self._advance_quests(row, "explore", 1)
        if event["id"] != "nothing":
            progress = self._advance_quests(row, "event", 1) or progress
        # 金币累计类日常按**本次实际获得**推进，而不是固定值。
        coin_gain = spot["coins"] + int(event.get("coins", 0))
        if coin_gain > 0:
            progress = self._advance_quests(row, "coins", coin_gain) or progress

        return {
            "spot": status,
            "gained": {
                "items": gained_items,
                "coins": spot["coins"] + int(event.get("coins", 0)),
                "intimacy": after_int - before_int,
            },
            "item_capped": capped,
            "watered_bonus": watered,
            "dust_cleared": dust_cleared,
            "event": event,
            "quest_progress": progress,
            "feedback": event.get("feedback") or f"收获 {MATERIALS[spot['material']][0]} ×{granted}",
        }

    # -- 事件 roll ------------------------------------------------------
    def _roll_event(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        spot: dict[str, Any], granted: int,
    ) -> dict[str, Any]:
        state = self._spot_state(row).get(spot["id"], {})
        count = int(state.get("count", 1)) if isinstance(state, dict) else 1
        rng = _rng(f"{row.daily_seed}|event|{spot['id']}|{count}|{persona}")

        # 「一无所获」诚实态：真的什么都不给，不做假产出。
        if granted <= 0:
            chosen = "nothing"
        else:
            ids = list(EVENTS.keys())
            weights = [EVENTS[i][0] for i in ids]
            chosen = rng.choices(ids, weights=weights, k=1)[0]

        _, title, template = EVENTS[chosen]
        lines = PERSONALITY_LINES.get(persona, PERSONALITY_FALLBACK_LINES)
        line = lines[rng.randrange(len(lines))]
        text = template.format(
            person=person_name or "小人",
            pet="小家伙",
            material=MATERIALS[spot["material"]][0],
            label=spot["label"],
            theme_label=spot["theme"],
            line=line,
            coins=max(1, spot["coins"]),
        )

        event: dict[str, Any] = {
            "id": chosen,
            "title": title,
            "text": text,
            "items": {},
            "coins": 0,
            "intimacy": 0,
            "feedback": text,
        }
        if chosen == "double_find":
            event["items"] = {spot["material"]: 1}
            event["feedback"] = f"{MATERIALS[spot['material']][0]} ×2！"
        elif chosen == "pet_gift":
            gift = rng.choice(sorted(FOOD_MATERIALS))
            event["items"] = {gift: 1}
            event["feedback"] = f"小家伙带回了 {MATERIALS[gift][0]}"
        elif chosen == "coin_find":
            amount = rng.randint(3, 10)
            event["coins"] = amount
            event["intimacy"] = 1
        elif chosen == "treasure_chest":
            if row.chest_keys > 0:
                row.chest_keys -= 1
                gift = rng.choice(sorted(MATERIALS.keys()))
                event["items"] = {gift: 2}
                event["coins"] = 15
                event["feedback"] = f"用 1 把钥匙打开了：{MATERIALS[gift][0]} ×2 + 15 金币"
            else:
                event["feedback"] = "宝箱需要 1 把钥匙（连续登录可获得）"
        elif chosen == "lost_firefly":
            event["intimacy"] = 2
            event["feedback"] = "萤火虫绕着鞋尖转了 30 秒"
        return event

    # -- feed -----------------------------------------------------------
    def _act_feed(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        prefs = PREFERENCES[persona]["pet"]
        bag = dict(row.materials or {})
        candidates = sorted(set(FOOD_MATERIALS) & set(bag.keys()))
        if not candidates:
            raise ValidationFailed(
                "cabin_no_food",
                "背包里没有可喂的材料；先去探险采集一些食物类材料",
            )
        chosen = target if target in candidates else (
            prefs[0] if prefs[0] in candidates else candidates[0]
        )
        have = int(bag.get(chosen, 0) or 0)
        if have <= 0:
            raise ValidationFailed("cabin_no_food", f"No {MATERIALS[chosen][0]} left in the bag")
        bag[chosen] = have - 1
        if bag[chosen] <= 0:
            bag.pop(chosen, None)
        row.materials = bag

        liked = chosen == prefs[0]
        base = 4
        gain = base * 2 if liked else base
        before_int, after_int = self._grant_intimacy(row, gain)
        coins = self._grant_coins(row, 0)

        # 每日首次照料额外奖励（任务书 §1.2）
        first_today = self._first_care_today(row, now, "feed")
        bonus_items: dict[str, int] = {}
        if first_today:
            bonus_items = {"wheat": 1}
            for mid, q in bonus_items.items():
                self._grant_material(row, mid, q)
            self._grant_coins(row, 5)
            coins = row.coins

        progress = self._advance_quests(row, "feed", 1)
        return {
            "fed": MATERIALS[chosen][0],
            "liked": liked,
            "gained": {"items": bonus_items, "coins": 5 if first_today else 0,
                       "intimacy": after_int - before_int},
            "first_care_today": first_today,
            "quest_progress": progress,
            "feedback": (
                f"{MATERIALS[chosen][0]} 是它的最爱，亲密度 +{gain}"
                if liked else f"吃掉了 {MATERIALS[chosen][0]}，亲密度 +{gain}"
            ),
        }

    def _first_care_today(self, row: CabinSave, now: datetime, kind: str) -> bool:
        """今日该照料类型是否首次。**必须**把 care 写回 row，否则标记不落库。"""
        q = self._quest_dict(row)
        care = q.get("care")
        if not isinstance(care, dict):
            care = {}
            q["care"] = care
        today = _local_date(now)
        if care.get("date") != today:
            care["date"] = today
            care["kinds"] = []
        kinds = care.get("kinds")
        if not isinstance(kinds, list):
            kinds = []
            care["kinds"] = kinds
        row.quest_state = q  # 关键：q 是 row.quest_state 的引用，但显式回写更安全
        if kind in kinds:
            return False
        kinds.append(kind)
        row.quest_state = q
        return True

    # -- water ----------------------------------------------------------
    def _act_water(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        waterable = [s for s in SPOTS if s["material"] in WATERABLE_MATERIALS]
        if spot_id is not None and spot_id in SPOT_BY_ID:
            spot = SPOT_BY_ID[spot_id]
            if spot["material"] not in WATERABLE_MATERIALS:
                raise ValidationFailed(
                    "cabin_not_waterable", f"{spot['label']} does not need watering"
                )
        else:
            spot = waterable[0]
        spots_state = self._spot_state(row)
        prev = spots_state.get(spot["id"], {})
        prev = dict(prev) if isinstance(prev, dict) else {}
        prev["watered"] = True
        prev["watered_at"] = _iso(now)
        spots_state[spot["id"]] = prev
        row.spots_state = spots_state

        first_today = self._first_care_today(row, now, "water")
        before_int, after_int = self._grant_intimacy(row, 2)
        if first_today:
            self._grant_material(row, "flower", 1)
            self._grant_coins(row, 5)
        progress = self._advance_quests(row, "water", 1)
        return {
            "watered": spot["label"],
            "gained": {"items": {"flower": 1} if first_today else {}, "coins": 5 if first_today else 0,
                       "intimacy": after_int - before_int},
            "first_care_today": first_today,
            "quest_progress": progress,
            "feedback": f"浇了 {spot['label']}，下次采集产出翻倍",
        }

    # -- clean ----------------------------------------------------------
    def _act_clean(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        dust = list(row.dust_state or [])
        # 打扫有概率发现新的灰尘清洁点（任务书 §1.2）
        rng = _rng(f"{row.daily_seed}|clean|{row.version}|{row.owner_id[-6:]}")
        found: str | None = None
        if rng.random() < 0.5:
            candidates = [s["id"] for s in SPOTS if s["dust"] and s["id"] not in dust]
            if candidates:
                found = candidates[rng.randrange(len(candidates))]
                dust.append(found)

        cleared = 0
        if spot_id is not None and spot_id in dust:
            dust.remove(spot_id)
            cleared = 1
        row.dust_state = dust

        before_int, after_int = self._grant_intimacy(row, 1 + cleared)
        coins_gained = 2 + cleared * 3
        self._grant_coins(row, coins_gained)
        first_today = self._first_care_today(row, now, "clean")
        if first_today:
            self._grant_coins(row, 5)
            coins_gained += 5
        progress = self._advance_quests(row, "clean", 1)
        return {
            "cleared": cleared,
            "found_dust_spot": found,
            "gained": {"items": {}, "coins": coins_gained, "intimacy": after_int - before_int},
            "first_care_today": first_today,
            "quest_progress": progress,
            "feedback": (
                f"擦掉了 {SPOT_BY_ID[spot_id]['label']} 的灰尘" if cleared
                else ("发现了一处灰尘，待机中" if found else "屋子很干净，擦了一遍")
            ),
        }

    # -- claim ----------------------------------------------------------
    def _act_claim(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        if quest_kind not in ("tutorial", "daily", "wish"):
            raise ValidationFailed(
                "cabin_unknown_quest_kind",
                "quest_kind must be one of: tutorial, daily, wish",
            )
        if not isinstance(quest_id, str) or not quest_id:
            raise ValidationFailed("cabin_invalid_quest_id", "quest_id is required")

        q = self._quest_dict(row)
        if quest_kind == "tutorial":
            return self._claim_tutorial(row, q, quest_id)
        if quest_kind == "daily":
            return self._claim_daily(row, q, quest_id)
        return self._claim_wish(row, q, quest_id)

    def _deliver(
        self, row: CabinSave, reward: dict[str, Any]
    ) -> dict[str, Any]:
        items = dict(reward.get("items") or {})
        for mid, qty in items.items():
            self._grant_material(row, mid, int(qty))
        if reward.get("coins"):
            self._grant_coins(row, int(reward["coins"]))
        before, after = self._grant_intimacy(row, int(reward.get("intimacy", 0)))
        return {
            "reward": {
                "items": items,
                "coins": int(reward.get("coins", 0)),
                "intimacy": after - before,
            }
        }

    def _claim_tutorial(
        self, row: CabinSave, q: dict[str, Any], quest_id: str
    ) -> dict[str, Any]:
        step_index = int(q["tutorial"].get("step", 0))
        if q["tutorial"].get("completed"):
            raise Conflict("cabin_tutorial_done", "Tutorial chain already completed", 409)
        if step_index >= len(TUTORIAL_STEPS):
            raise Conflict("cabin_tutorial_done", "Tutorial chain already completed", 409)
        step = TUTORIAL_STEPS[step_index]
        if step["id"] != quest_id:
            raise ValidationFailed(
                "cabin_wrong_step",
                f"Current tutorial step is {step['id']!r}, not {quest_id!r}",
            )
        if int(q["tutorial"]["progress"].get(step["event"], 0)) < step["target"]:
            raise Conflict(
                "cabin_quest_incomplete",
                f"Step {step['id']} needs {step['target']} progress, "
                f"has {q['tutorial']['progress'].get(step['event'], 0)}",
                409,
            )
        claimed = q["tutorial"].setdefault("claimed", [])
        if step["id"] in claimed:
            raise Conflict("cabin_already_claimed", "Step reward already claimed", 409)

        delivered = self._deliver(row, step["reward"])
        claimed.append(step["id"])
        next_step = step_index + 1
        completed = next_step >= len(TUTORIAL_STEPS)
        q["tutorial"]["step"] = next_step
        if completed:
            q["tutorial"]["completed"] = True
        # 完成新手链 → 解锁一件任务家具（11 号文档制造闭环起点）
        unlocked: list[str] = []
        if completed:
            unlocked = self._grant_unlock(row, "crystal_tree" if row.house_level >= 3 else "herb_shelf")
        row.quest_state = q
        return {
            "claimed": step["id"],
            "completed": completed,
            "unlocked_furniture": unlocked,
            "feedback": f"「{step['title']}」完成：{step['reward_label']}",
            **delivered,
        }

    def _claim_daily(
        self, row: CabinSave, q: dict[str, Any], quest_id: str
    ) -> dict[str, Any]:
        daily = q.get("daily") or {}
        if quest_id not in (daily.get("ids") or []):
            raise ValidationFailed(
                "cabin_quest_not_active", f"{quest_id!r} is not one of today's dailies"
            )
        spec = next((d for d in DAILY_POOL if d["id"] == quest_id), None)
        if spec is None:
            raise ValidationFailed("cabin_unknown_quest", f"Unknown daily {quest_id!r}")
        claimed = daily.setdefault("claimed", [])
        if quest_id in claimed:
            raise Conflict("cabin_already_claimed", "Daily reward already claimed", 409)
        progress = int((daily.get("progress") or {}).get(quest_id, 0))
        if progress < spec["target"]:
            raise Conflict(
                "cabin_quest_incomplete",
                f"{quest_id} needs {spec['target']}, has {progress}",
                409,
            )
        delivered = self._deliver(row, spec["reward"])
        claimed.append(quest_id)
        row.quest_state = q
        return {
            "claimed": quest_id,
            "feedback": f"日常「{spec['title']}」完成：{spec['reward_label']}",
            **delivered,
        }

    def _claim_wish(
        self, row: CabinSave, q: dict[str, Any], quest_id: str
    ) -> dict[str, Any]:
        wish = q.get("wish") or {}
        if wish.get("id") != quest_id:
            raise ValidationFailed(
                "cabin_wish_mismatch", f"Current wish is {wish.get('id')!r}, not {quest_id!r}"
            )
        if wish.get("claimed"):
            raise Conflict("cabin_already_claimed", "Wish reward already claimed", 409)
        if not wish.get("done"):
            raise Conflict("cabin_wish_incomplete", "Wish is not fulfilled yet", 409)
        spec = next((w for w in WISH_POOL if w["id"] == quest_id), None)
        coins = 20
        intimacy = 5
        if spec is not None:
            # 奖励文案形如「亲密度 +6 + 20 金币」：从中解析出两个数值，
            # 避免文案改了而实际发奖不变（诚实：发多少写多少）。
            m = re.search(r"亲密度 \+(\d+)", spec["reward_label"])
            if m:
                intimacy = int(m.group(1))
            c = re.search(r"\+ (\d+) 金币", spec["reward_label"])
            if c:
                coins = int(c.group(1))
        delivered = self._deliver(row, {"coins": coins, "intimacy": intimacy})
        wish["claimed"] = True
        row.quest_state = q
        return {
            "claimed": quest_id,
            "feedback": f"愿望达成：{wish.get('text', '')}",
            **delivered,
        }

    # -- craft ----------------------------------------------------------
    def _act_craft(
        self, row: CabinSave, now: datetime, persona: str, person_name: str,
        *, spot_id: Any, target: Any, quest_kind: Any, quest_id: Any,
    ) -> dict[str, Any]:
        if not isinstance(target, str) or target not in BLUEPRINTS:
            raise ValidationFailed(
                "cabin_unknown_blueprint",
                f"Unknown blueprint {target!r}; known: {', '.join(sorted(BLUEPRINTS))}",
            )
        label, cost, unlock_level = BLUEPRINTS[target]
        if row.house_level < unlock_level:
            raise Conflict(
                "cabin_level_too_low",
                f"{label} requires house Lv{unlock_level}, current Lv{row.house_level}",
                409,
            )
        if target in (row.unlocked_furniture or []):
            raise Conflict("cabin_already_unlocked", f"{label} already unlocked", 409)
        bag = dict(row.materials or {})
        missing = {m: q - int(bag.get(m, 0) or 0) for m, q in cost.items()
                   if int(bag.get(m, 0) or 0) < q}
        if missing:
            raise ValidationFailed(
                "cabin_missing_materials",
                "Missing materials: "
                + ", ".join(f"{MATERIALS[m][0]} ×{n}" for m, n in sorted(missing.items())),
            )
        for m, q in cost.items():
            left = int(bag.get(m, 0) or 0) - q
            if left > 0:
                bag[m] = left
            else:
                bag.pop(m, None)
        row.materials = bag
        unlocked = self._grant_unlock(row, target)
        progress = self._advance_quests(row, "craft", 1, crafted_id=target)
        return {
            "crafted": target,
            "crafted_label": label,
            "spent": cost,
            "unlocked_furniture": unlocked,
            "quest_progress": progress,
            "feedback": f"做好了 {label}！去布置模式摆上吧",
        }

    def _grant_unlock(self, row: CabinSave, furniture_id: str) -> list[str]:
        current = list(row.unlocked_furniture or [])
        if furniture_id in current:
            return []
        current.append(furniture_id)
        row.unlocked_furniture = current
        return [furniture_id]

    # -- 任务进度 -------------------------------------------------------
    def _advance_quests(
        self, row: CabinSave, event: str, amount: int, *, crafted_id: str | None = None
    ) -> dict[str, Any]:
        q = self._quest_dict(row)
        changed: dict[str, Any] = {"tutorial": None, "daily": None, "wish": None}

        # 新手链
        t = q["tutorial"]
        if not t.get("completed"):
            step_index = int(t.get("step", 0))
            if step_index < len(TUTORIAL_STEPS):
                step = TUTORIAL_STEPS[step_index]
                if step["event"] == event:
                    prog = t.setdefault("progress", {})
                    prog[event] = int(prog.get(event, 0)) + amount
                    if prog[event] >= step["target"] and prog[event] - amount < step["target"]:
                        changed["tutorial"] = {
                            "id": step["id"], "title": step["title"], "ready": True,
                        }

        # 日常（coins 类在同一分支内按 event 过滤，无需第二段）
        daily = q.get("daily") or {}
        if daily:
            progress = daily.setdefault("progress", {})
            for qid in daily.get("ids", []):
                spec = next((d for d in DAILY_POOL if d["id"] == qid), None)
                if spec is None or spec["event"] != event:
                    continue
                if qid in (daily.get("claimed") or []):
                    continue
                before = int(progress.get(qid, 0))
                progress[qid] = before + amount
                if progress[qid] >= spec["target"] and before < spec["target"]:
                    changed["daily"] = {"id": qid, "title": spec["title"], "ready": True}

        # 居民愿望：material 类看背包是否已满足；craft 类看目标家具是否刚解锁。
        wish = q.get("wish") or {}
        if wish and not wish.get("done") and not wish.get("claimed"):
            satisfied = False
            if wish.get("kind") == "material" and event == "explore":
                have = int((row.materials or {}).get(wish["target"], 0) or 0)
                satisfied = have >= int(wish["need"])
            elif wish.get("kind") == "craft" and event == "craft" and crafted_id is not None:
                satisfied = crafted_id == wish["target"]
            if satisfied:
                wish["progress"] = int(wish["need"])
                wish["done"] = True
                changed["wish"] = {"id": wish["id"], "ready": True}

        row.quest_state = q
        return {k: v for k, v in changed.items() if v}
