"""B7 · NPC 名册 / 作息表 / 好感 0-10 心（纯逻辑层）。

说明书 §5-B7 要求：
    * ≥10 常驻 NPC / 图，各有作息表（早出晚归）；
    * 好感 0-10 心，**每升 1 心解锁新对话/剧情**；
    * 不同时间去对话内容不同；角色身份/性格影响对话。

诚实原则：
    - 好感是纯函数累加（points → hearts 由 `hearts_for_points` 显式公式给出），
      玩家可以自己复算「为什么现在是 4 心」；
    - 送礼不命中喜好也**如实**给基础好感，绝不每送必加满；
    - NPC 没起床/已回家时对话会明确说明（`DialogueLine.scene`），不假装随时可聊。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .clock import DAY_PARTS, GameClock
from .rng import clamp, pick, rng_for
from .themes import LEGACY_THEME_IDS, SPEC_THEME_IDS, theme_label

#: 好感心数上限（规格定死 0-10）
MAX_HEARTS = 10
#: 每心所需点数（线性、可复算）
POINTS_PER_HEART = 100
#: 单日送礼次数上限（防刷）
MAX_GIFTS_PER_DAY = 3


@dataclass(frozen=True)
class ScheduleSlot:
    """作息段：`(start_minute, place, activity)`，按 start 升序，最后一段兜底。"""

    start: int
    place: str
    activity: str


@dataclass(frozen=True)
class NpcDef:
    id: str
    name: str
    role: str
    theme: str
    home: str
    #: 喜好/厌恶/中立礼物（material 或 goods id）
    likes: tuple[str, ...]
    dislikes: tuple[str, ...]
    schedule: tuple[ScheduleSlot, ...]
    #: 每心解锁的一句剧情（长度 = MAX_HEARTS，index 0 = 0 心时的初见语）
    heart_lines: tuple[str, ...]
    #: 委托（commissions）：id -> 需要的材料
    commissions: tuple[tuple[str, str, int], ...] = ()


def _slots(*rows: tuple[int, str, str]) -> tuple[ScheduleSlot, ...]:
    return tuple(ScheduleSlot(a, b, c) for a, b, c in sorted(rows, key=lambda r: r[0]))


def _persona(
    npc_id: str,
    name: str,
    role: str,
    theme: str,
    likes: tuple[str, ...],
    dislikes: tuple[str, ...],
    schedule: tuple[ScheduleSlot, ...],
    lines: tuple[str, ...],
    commissions: tuple[tuple[str, str, int], ...] = (),
) -> NpcDef:
    if len(lines) != MAX_HEARTS + 1:
        raise ValueError(f"{npc_id}: 需要 {MAX_HEARTS + 1} 句心数台词，收到 {len(lines)}")
    return NpcDef(npc_id, name, role, theme, "home", likes, dislikes, schedule, lines, commissions)


_DEFAULT_LINES = (
    "刚搬来那阵子，我在这条路上走了很多遍。",
    "你说话不多，但听着挺舒服。",
    "昨天顺手帮你留了门。",
    "今天路过，看见你窗台上的灯亮着。",
    "有件事一直想跟你说，不着急。",
    "你在这儿，我就觉得这地方有人气。",
    "朋友做久了，偶尔也会想谢谢。",
    "我把最舍不得的那件东西给你看过。",
    "这里已经算是我的家了——也是你的。",
    "别人问我为什么留下，我说有个熟人。",
    "要是我哪天不在了，你替我浇浇花。",
)


def _npc(
    npc_id: str,
    name: str,
    role: str,
    theme: str,
    likes: tuple[str, ...],
    dislikes: tuple[str, ...],
    schedule: tuple[ScheduleSlot, ...],
    commissions: tuple[tuple[str, str, int], ...] = (),
    lines: tuple[str, ...] = _DEFAULT_LINES,
) -> NpcDef:
    return _persona(npc_id, name, role, theme, likes, dislikes, schedule, lines, commissions)


# ----------------------------------------------------------------------
# 名册：每主题 ≥10 人（旧五背景 + 四大主题）
# ----------------------------------------------------------------------

_R = _npc

_NPCS: tuple[NpcDef, ...] = (
    # --- forest 老林子 ---
    _R("forest_woodsman", "老周", "樵夫", "forest", ("wood", "honey"), ("slime_jelly",),
       _slots((0, "home", "睡觉"), (360, "home", "生火"), (420, "grove", "砍柴"),
              (1080, "market", "卖柴"), (1140, "grove", "砍柴"), (1200, "home", "做饭"))),
    _R("forest_herbalist", "阿蕨", "药师", "forest", ("herb", "moss"), ("crystal",),
       _slots((0, "home", "睡觉"), (360, "home", "熬药"), (420, "glade", "采药"),
              (1020, "home", "晒药"), (1140, "glade", "采药"))),
    _R("forest_beekeeper", "蜜姐", "养蜂人", "forest", ("flower", "honey"), (),
       _slots((0, "home", "睡觉"), (360, "orchard", "取蜜"), (480, "market", "摆摊"),
              (1020, "orchard", "采蜜"))),
    _R("forest_ranger", "小林", "巡林员", "forest", ("pebble", "wood"), (),
       _slots((0, "home", "睡觉"), (360, "gate", "换岗"), (420, "grove", "巡查"),
              (1140, "gate", "交班"))),
    _R("forest_kid", "豆子", "孩子", "forest", ("pinecone", "jam"), (),
       _slots((0, "home", "睡觉"), (420, "grove", "玩耍"), (1020, "market", "买糖"))),
    _R("forest_carver", "刻木", "木匠", "forest", ("wood", "cotton"), (),
       _slots((0, "home", "睡觉"), (360, "home", "开工"), (420, "workshop", "做活"),
              (1080, "home", "收工"))),
    _R("forst_shrine", "守灯人", "林守", "forest", ("stardust", "moonstone"), (),
       _slots((0, "shrine", "守夜"), (360, "shrine", "点灯"), (1020, "grove", "巡灯"),
              (1200, "shrine", "守夜"))),
    _R("forest_angler", "阿舟", "钓客", "forest", ("fish", "reed"), (),
       _slots((0, "home", "睡觉"), (360, "stream", "下竿"), (720, "stream", "钓鱼"),
              (1080, "market", "卖鱼"))),
    _R("forest_mushroom", "菌菇婆", "采集者", "forest", ("mushroom", "seed"), (),
       _slots((0, "home", "睡觉"), (420, "glade", "找菇"), (1020, "market", "卖菇"))),
    _R("forest_scribe", "文先生", "记录员", "forest", ("pebble", "magic_scroll"), (),
       _slots((0, "home", "睡觉"), (360, "home", "抄写"), (420, "library", "整理"),
              (1140, "library", "挑灯"))),
    # --- garden 后花园 ---
    _R("garden_botanist", "小薇", "园丁", "garden", ("flower", "seed"), (),
       _slots((0, "home", "睡觉"), (330, "garden", "浇花"), (420, "garden", "除草"),
              (1080, "greenhouse", "育苗"))),
    _R("garden_bee", "阿蜂", "蜂友", "garden", ("honey", "flower"), (),
       _slots((0, "home", "睡觉"), (360, "apiary", "看蜂"), (480, "garden", "采蜜"),
              (1020, "market", "卖蜜"))),
    _R("garden_tea", "婆婆", "茶婆婆", "garden", ("tea_leaf", "honey"), ("cotton",),
       _slots((0, "home", "睡觉"), (360, "kitchen", "煮茶"), (420, "yard", "晒茶"),
              (1140, "yard", "纳凉"))),
    _R("garden_seed", "种哥", "种子商", "garden", ("seed", "vegetable"), (),
       _slots((0, "home", "睡觉"), (360, "shop", "开门"), (420, "shop", "卖种子"),
              (1080, "shop", "盘账"))),
    _R("garden_cat", "三花", "猫", "garden", ("fish", "flower"), (),
       _slots((0, "home", "睡觉"), (360, "porch", "晒太阳"), (720, "garden", "抓虫"),
              (1200, "home", "睡觉"))),
    _R("garden_painter", "画皮", "画师", "garden", ("peach_blossom", "flower"), (),
       _slots((0, "home", "睡觉"), (420, "pond", "写生"), (1020, "studio", "作画"))),
    _R("garden_cook", "锅子", "厨子", "garden", ("vegetable", "jam"), (),
       _slots((0, "home", "睡觉"), (330, "kitchen", "备料"), (420, "kitchen", "做菜"),
              (1020, "market", "送餐"))),
    _R("garden_child", "苗苗", "孩童", "garden", ("flower", "bread"), (),
       _slots((0, "home", "睡觉"), (420, "garden", "捉虫"), (1020, "kitchen", "讨食"))),
    _R("garden_greenhouse", "青娘", "苗圃主", "garden", ("seed", "herb"), (),
       _slots((0, "home", "睡觉"), (360, "greenhouse", "育苗"), (420, "field", "移栽"))),
    _R("garden_well", "打水叔", "井边人", "garden", ("pebble", "reed"), (),
       _slots((0, "home", "睡觉"), (360, "well", "打水"), (420, "yard", "浇园"),
              (1140, "well", "闲坐"))),
    # --- stream 溪水边 ---
    _R("stream_fisher", "阿钓", "渔夫", "stream", ("fish", "reed"), (),
       _slots((0, "home", "睡觉"), (360, "bank", "放笼"), (420, "bank", "钓鱼"),
              (1020, "market", "卖鱼"))),
    _R("stream_moss", "苔生", "苔藓学者", "stream", ("moss", "pebble"), (),
       _slots((0, "home", "睡觉"), (360, "bank", "观察"), (420, "cave", "取样"),
              (1140, "home", "记录"))),
    _R("stream_washer", "浣衣娘", "浣衣人", "stream", ("cotton", "soap"), (),
       _slots((0, "home", "睡觉"), (360, "bank", "洗衣"), (480, "bank", "晾衣"))),
    _R("stream_keeper", "桥头汉", "守桥人", "stream", ("wood", "pebble"), (),
       _slots((0, "home", "睡觉"), (360, "bridge", "扫桥"), (420, "bridge", "值守"),
              (1200, "bridge", "巡夜"))),
    _R("stream_heron", "鹭姐", "观鸟人", "stream", ("reed", "fish"), (),
       _slots((0, "home", "睡觉"), (360, "hide", "蹲守"), (420, "marsh", "观鸟"))),
    _R("stream_snail", "蜗壳", "采集童", "stream", ("pebble", "moss"), (),
       _slots((0, "home", "睡觉"), (420, "bank", "翻石头"), (1020, "market", "卖石"))),
    _R("stream_boat", "船家", "摆渡人", "stream", ("fish", "wood"), (),
       _slots((0, "dock", "守船"), (360, "dock", "摆渡"), (1080, "dock", "收船"))),
    _R("stream_spring", "泉眼", "守泉人", "stream", ("moonstone", "crystal"), (),
       _slots((0, "spring", "守夜"), (360, "spring", "汲水"), (1140, "spring", "守夜"))),
    _R("stream_tea", "溪边阿婆", "煮茶人", "stream", ("tea_leaf", "jam"), (),
       _slots((0, "home", "睡觉"), (360, "kitchen", "煮水"), (420, "kitchen", "沏茶"),
              (1080, "yard", "待客"))),
    _R("stream_child", "石子", "孩童", "stream", ("pebble", "bread"), (),
       _slots((0, "home", "睡觉"), (420, "bank", "打水漂"), (1020, "home", "洗澡"))),
    # --- field 金黄田野 ---
    _R("field_farmer", "田伯", "农夫", "field", ("wheat", "seed"), (),
       _slots((0, "home", "睡觉"), (300, "field", "浇水"), (360, "field", "劳作"),
              (1080, "barn", "收工"))),
    _R("field_windmill", "风车夫", "磨坊主", "field", ("wheat", "wood"), (),
       _slots((0, "home", "睡觉"), (360, "mill", "开磨"), (420, "mill", "磨面"),
              (1140, "mill", "收工"))),
    _R("field_pumpkin", "南瓜王", "瓜农", "field", ("pumpkin", "jam"), (),
       _slots((0, "home", "睡觉"), (360, "patch", "查看"), (420, "patch", "采瓜"))),
    _R("field_firefly", "萤童", "捕虫童", "field", ("firefly_jar", "honey"), (),
       _slots((0, "home", "睡觉"), (1140, "field", "捕萤"), (1200, "field", "捕萤"))),
    _R("field_baker", "面包叔", "面包师", "field", ("wheat", "jam"), ("fish",),
       _slots((0, "home", "睡觉"), (300, "bakery", "生火"), (360, "bakery", "烤面包"),
              (1020, "market", "出摊"))),
    _R("field_shepherd", "牧童", "牧羊人", "field", ("cotton", "vegetable"), (),
       _slots((0, "home", "睡觉"), (360, "pasture", "放羊"), (1080, "barn", "赶羊"))),
    _R("field_mayor", "村长", "村长", "field", ("bread", "flower"), (),
       _slots((0, "home", "睡觉"), (360, "office", "办公"), (480, "field", "巡视"),
              (1080, "square", "应酬"))),
    _R("field_carpenter", "老木", "木匠", "field", ("wood", "handicraft"), (),
       _slots((0, "home", "睡觉"), (360, "shop", "开工"), (420, "shop", "接单"))),
    _R("field_festival", "花祭司", "祭典主持", "field", ("flower", "jam"), (),
       _slots((0, "home", "睡觉"), (420, "square", "布置"), (1020, "square", "主持"))),
    _R("field_kid", "稻子", "孩童", "field", ("bread", "flower"), (),
       _slots((0, "home", "睡觉"), (420, "field", "追鸡"), (1020, "square", "玩耍"))),
    # --- planet 观星台 ---
    _R("planet_astronomer", "星官", "观星者", "planet", ("stardust", "moonstone"), (),
       _slots((0, "tower", "守观"), (1140, "tower", "记录"), (1200, "tower", "守观"))),
    _R("planet_miner", "矿哥", "矿工", "planet", ("crystal", "alloy_ore"), (),
       _slots((0, "home", "睡觉"), (360, "pit", "下矿"), (420, "pit", "采掘"),
              (1080, "camp", "收工"))),
    _R("planet_repair", "扳手", "机修师", "planet", ("alloy_ore", "energy_cell"), (),
       _slots((0, "home", "睡觉"), (360, "garage", "修整"), (420, "garage", "焊接"))),
    _R("planet_trader", "星商", "外星商人", "planet", ("crystal", "resin"), ("moss",),
       _slots((0, "home", "睡觉"), (360, "market", "开店"), (420, "market", "交易"),
              (1080, "market", "收摊"))),
    _R("planet_drone", "巡机", "无人机", "planet", ("energy_cell", "stardust"), (),
       _slots((0, "dock", "充电"), (360, "sky", "巡航"), (1020, "dock", "归舱"))),
    _R("planet_botanist", "孢子", "孢子培育", "planet", ("resin", "herb"), (),
       _slots((0, "lab", "休眠"), (360, "lab", "培养"), (420, "dome", "采样"))),
    _R("planet_signal", "回声", "信号员", "planet", ("crystal", "magic_scroll"), (),
       _slots((0, "station", "值班"), (360, "station", "监听"), (1080, "tower", "汇报"))),
    _R("planet_medic", "白袍", "医疗兵", "planet", ("herb", "jam"), (),
       _slots((0, "clinic", "值班"), (360, "clinic", "问诊"), (1020, "clinic", "查房"))),
    _R("planet_scout", "远行者", "勘探员", "planet", ("moonstone", "resin"), (),
       _slots((0, "camp", "睡觉"), (360, "crater", "勘探"), (1020, "camp", "写报告"))),
    _R("planet_kid", "小陨", "孩童", "planet", ("crystal", "bread"), (),
       _slots((0, "home", "睡觉"), (420, "dome", "看星星"), (1020, "dock", "数船"))),
    # --- magic 魔法大陆 ---
    _R("magic_elf", "叶灵", "精灵", "magic", ("magic_herb", "peach_blossom"), (),
       _slots((0, "home", "睡觉"), (360, "treehouse", "醒来"), (420, "forest", "采药"),
              (1020, "village", "巡逻"))),
    _R("magic_wizard", "灰袍", "巫师", "magic", ("magic_crystal", "magic_scroll"), (),
       _slots((0, "tower", "睡觉"), (360, "tower", "配药"), (420, "tower", "推演"),
              (1140, "tower", "夜读"))),
    _R("magic_slime_merchant", "果冻老板", "史莱姆商人", "magic", ("slime_jelly", "jam"), (),
       _slots((0, "shop", "打烊"), (360, "shop", "开门"), (420, "shop", "叫卖"))),
    _R("magic_guardian", "守林人", "森林守护者", "magic", ("moss", "wood"), (),
       _slots((0, "shrine", "守夜"), (360, "shrine", "祈礼"), (420, "forest", "巡查"))),
    _R("magic_alchemist", "小瓶", "药剂学徒", "magic", ("magic_herb", "honey"), (),
       _slots((0, "home", "睡觉"), (360, "lab", "研磨"), (420, "lab", "熬制"))),
    _R("magic_teacher", "月课师", "导师", "magic", ("magic_scroll", "tea_leaf"), (),
       _slots((0, "home", "睡觉"), (360, "school", "授课"), (420, "school", "答疑"))),
    _R("magic_unicorn", "踏雪", "幻兽", "magic", ("flower", "moonstone"), (),
       _slots((0, "clearing", "休息"), (420, "lake", "饮月"), (1140, "clearing", "漫步"))),
    _R("magic_miner", "晶匠", "水晶匠", "magic", ("magic_crystal", "alloy_ore"), (),
       _slots((0, "home", "睡觉"), (360, "cave", "开采"), (420, "cave", "打磨"))),
    _R("magic_baker", "蜜糕师", "点心师", "magic", ("honey", "jam"), (),
       _slots((0, "home", "睡觉"), (330, "kitchen", "生火"), (360, "kitchen", "烘焙"),
              (1020, "market", "出摊"))),
    _R("magic_kid", "萤小", "精灵童", "magic", ("stardust", "bread"), (),
       _slots((0, "home", "睡觉"), (420, "forest", "捉萤"), (1020, "village", "玩耍"))),
    # --- scifi 科幻星球 ---
    _R("scifi_robot", "R-07", "机器人", "scifi", ("energy_cell", "alloy_ore"), (),
       _slots((0, "dock", "充电"), (360, "base", "巡逻"), (1020, "dock", "休眠"))),
    _R("scifi_astronaut", "航天员", "宇航员", "scifi", ("gas_canister", "jam"), (),
       _slots((0, "base", "睡眠"), (360, "base", "值勤"), (420, "hangar", "整备"),
              (1020, "base", "报告"))),
    _R("scifi_scientist", "低温博士", "科学家", "scifi", ("bio_sample", "crystal"), (),
       _slots((0, "lab", "睡眠"), (360, "lab", "培养"), (420, "lab", "记录"))),
    _R("scifi_trader", "加价", "外星商人", "scifi", ("alloy_ore", "tech_gear"), ("moss",),
       _slots((0, "shop", "打烊"), (360, "shop", "开店"), (420, "shop", "议价"))),
    _R("scifi_engineer", "扳手星", "机械师", "scifi", ("tech_gear", "energy_cell"), (),
       _slots((0, "base", "睡眠"), (360, "garage", "检修"), (420, "garage", "改装"))),
    _R("scifi_medic", "白盾", "医疗官", "scifi", ("bio_sample", "herb"), (),
       _slots((0, "clinic", "值班"), (360, "clinic", "问诊"), (1020, "clinic", "巡房"))),
    _R("scifi_pilot", "破风", "驾驶员", "scifi", ("gas_canister", "resin"), (),
       _slots((0, "barracks", "睡眠"), (360, "port", "整备"), (1020, "sky", "巡航"))),
    _R("scifi_botanist", "孢舱员", "生态官", "scifi", ("bio_sample", "herb"), (),
       _slots((0, "dome", "睡眠"), (360, "dome", "巡舱"), (420, "dome", "育苗"))),
    _R("scifi_archivist", "档案员", "记录官", "scifi", ("crystal", "data_shard"), (),
       _slots((0, "archive", "睡眠"), (360, "archive", "归档"), (420, "archive", "校订"))),
    _R("scifi_kid", "小螺丝", "机械学徒", "scifi", ("bread", "energy_cell"), (),
       _slots((0, "barracks", "睡眠"), (420, "garage", "打下手"), (1020, "base", "玩耍"))),
    # --- country 田园乡村 ---
    _R("country_farmer", "阿禾", "农夫", "country", ("wheat", "vegetable"), (),
       _slots((0, "home", "睡觉"), (300, "farm", "喂鸡"), (360, "farm", "耕作"),
              (1080, "barn", "收工"))),
    _R("country_baker", "麦香", "面包师", "country", ("wheat", "jam"), ("fish",),
       _slots((0, "home", "睡觉"), (300, "bakery", "生火"), (360, "bakery", "烘焙"),
              (1020, "market", "出摊"))),
    _R("country_fisher", "阿漾", "渔夫", "country", ("fish", "reed"), (),
       _slots((0, "home", "睡觉"), (360, "lake", "下网"), (420, "lake", "垂钓"))),
    _R("country_carpenter", "木叔", "木匠", "country", ("wood", "handicraft"), (),
       _slots((0, "home", "睡觉"), (360, "shop", "开工"), (420, "shop", "打家具"))),
    _R("country_mayor", "镇长", "村长", "country", ("bread", "flower"), (),
       _slots((0, "home", "睡觉"), (360, "office", "办公"), (480, "town", "巡查"))),
    _R("country_orchard", "桃姨", "果农", "country", ("fruit", "jam"), (),
       _slots((0, "home", "睡觉"), (360, "orchard", "疏果"), (420, "orchard", "采摘"))),
    _R("country_flower", "花铺主", "花匠", "country", ("flower", "seed"), (),
       _slots((0, "home", "睡觉"), (330, "greenhouse", "浇花"), (420, "shop", "插花"))),
    _R("country_shepherd", "阿牧", "牧人", "country", ("cotton", "vegetable"), (),
       _slots((0, "home", "睡觉"), (360, "pasture", "放牧"), (1080, "barn", "圈羊"))),
    _R("country_kid", "小穗", "孩童", "country", ("bread", "flower"), (),
       _slots((0, "home", "睡觉"), (420, "farm", "追鸡"), (1020, "square", "玩耍"))),
    _R("country_festival", "节庆组", "丰收节主事", "country", ("pumpkin", "jam"), (),
       _slots((0, "home", "睡觉"), (420, "square", "布置"), (1020, "square", "主持"))),
    # --- ink 古风桃源 ---
    _R("ink_scholar", "书童", "书生", "ink", ("tea", "magic_scroll"), (),
       _slots((0, "inn", "睡觉"), (360, "study", "读书"), (420, "bridge", "散步"),
              (1140, "teahouse", "夜读"))),
    _R("ink_tea_master", "茶博士", "茶博士", "ink", ("tea_leaf", "pastry"), (),
       _slots((0, "home", "睡觉"), (360, "teahouse", "备茶"), (420, "teahouse", "待客"))),
    _R("ink_embroidery", "绣娘", "绣娘", "ink", ("peach_blossom", "cotton"), (),
       _slots((0, "home", "睡觉"), (360, "workshop", "绣花"), (420, "workshop", "配色"))),
    _R("ink_fisher", "棹歌", "渔夫", "ink", ("fish", "reed"), (),
       _slots((0, "home", "睡觉"), (360, "river", "撒网"), (420, "river", "撑船"))),
    _R("ink_hermit", "云隐", "隐居高人", "ink", ("tea", "wine"), ("slime_jelly",),
       _slots((0, "hut", "静坐"), (420, "bamboo", "抚琴"), (1140, "hut", "夜话"))),
    _R("ink_apothecary", "百草堂", "药铺主", "ink", ("herbal", "jam"), (),
       _slots((0, "shop", "打烊"), (360, "shop", "抓药"), (420, "shop", "晒药"))),
    _R("ink_poet", "词人", "诗人", "ink", ("wine", "peach_blossom"), (),
       _slots((0, "inn", "睡觉"), (420, "bridge", "吟诗"), (1020, "teahouse", "雅集"))),
    _R("ink_painter", "丹青", "画师", "ink", ("tea", "flower"), (),
       _slots((0, "home", "睡觉"), (360, "studio", "调色"), (420, "river", "写生"))),
    _R("ink_lantern", "掌灯人", "掌灯人", "ink", ("incense", "handicraft"), (),
       _slots((0, "home", "睡觉"), (1020, "street", "挂灯"), (1140, "street", "巡夜"))),
    _R("ink_kid", "阿桃", "孩童", "ink", ("pastry", "peach_blossom"), (),
       _slots((0, "home", "睡觉"), (420, "river", "捞花"), (1020, "street", "玩耍"))),
)

#: 委托派发规则（确定性）：按 NPC 的喜好材料派生 1 条委托，需求量由 id 哈希稳定给出。
#: 这样「每张图都有可接的委托」成立，同时**不需要**为 90 个人手写 90 条数据，
#: 且同一天同一 NPC 的需求不会漂移（可复算）。
def _commission_for(npc: NpcDef) -> tuple[tuple[str, str, int], ...]:
    if not npc.likes:
        return ()
    material = npc.likes[0]
    need = 2 + (sum(ord(c) for c in npc.id) % 3)
    return ((f"{npc.id}_ask", material, need),)


_NPCS = tuple(
    n if n.commissions else dataclasses.replace(n, commissions=_commission_for(n))
    for n in _NPCS
)

#: 修正一处笔误 id（保持历史数据可读；新增必须走这个白名单）
_NPCS = tuple(
    NpcDef(**{**vars(n), "id": "forest_shrine"}) if n.id == "forst_shrine" else n
    for n in _NPCS
)

NPCS: dict[str, NpcDef] = {n.id: n for n in _NPCS}
assert len(NPCS) == len(_NPCS), "NPC id 重复"

MIN_NPCS_PER_THEME = 10
for _tid in LEGACY_THEME_IDS + SPEC_THEME_IDS:
    _count = sum(1 for n in _NPCS if n.theme == _tid)
    assert _count >= MIN_NPCS_PER_THEME, f"{_tid} 只有 {_count} 个 NPC，少于 {MIN_NPCS_PER_THEME}"


def npcs_of(theme: str) -> tuple[NpcDef, ...]:
    """某主题的全部常驻 NPC（确定性顺序：按 id）。"""
    return tuple(sorted((n for n in _NPCS if n.theme == theme), key=lambda n: n.id))


def get_npc(npc_id: str) -> NpcDef:
    try:
        return NPCS[npc_id]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"unknown npc {npc_id!r}; known={sorted(NPCS)}") from exc


# ----------------------------------------------------------------------
# 作息
# ----------------------------------------------------------------------

def locate(npc: NpcDef, clock: GameClock) -> ScheduleSlot:
    """当前时段 NPC 在哪、在做什么（取最后一个 start <= minute 的段）。"""
    slot = npc.schedule[0]
    for candidate in npc.schedule:
        if candidate.start <= clock.minute:
            slot = candidate
        else:
            break
    return slot


def awake(npc: NpcDef, clock: GameClock) -> bool:
    return locate(npc, clock).activity != "睡觉"


def npc_status_rows(theme: str, clock: GameClock) -> list[dict[str, object]]:
    """给前端 NPC 页签 / HUD 用的一览（纯数据，无 IO）。"""
    return [
        {
            "id": n.id,
            "name": n.name,
            "role": n.role,
            "place": locate(n, clock).place,
            "activity": locate(n, clock).activity,
            "awake": awake(n, clock),
        }
        for n in npcs_of(theme)
    ]


# ----------------------------------------------------------------------
# 好感
# ----------------------------------------------------------------------

def hearts_for_points(points: int) -> int:
    """点数 → 心数（0..10，每 100 点 1 心）。"""
    if points < 0:
        raise ValueError("points must be >= 0")
    return clamp(points // POINTS_PER_HEART, 0, MAX_HEARTS)


def gift_points(npc: NpcDef, gift: str) -> int:
    """送礼好感点数：喜好 +60 / 中立 +20 / 厌恶 0（明确不加，不是偷偷加）。"""
    if gift in npc.likes:
        return 60
    if gift in npc.dislikes:
        return 0
    return 20


def gift_reaction(npc: NpcDef, gift: str, points: int) -> str:
    kind = "喜欢" if gift in npc.likes else ("讨厌" if gift in npc.dislikes else "还行")
    return f"{npc.name}（{kind}这个）· 好感 +{points}"


def apply_gift(
    npc: NpcDef, gift: str, points: int, *, gifts_today: int, personality_bonus: bool = False
) -> tuple[int, int, int, str]:
    """在已有 points 上叠加一次送礼，返回 (新 points, 新 hearts, 消耗的心, 文案)。

    `gifts_today` 为**本次之前今日已送次数**；已达上限则点数不变并说明原因。
    """
    if points < 0:
        raise ValueError("points must be >= 0")
    if gifts_today >= MAX_GIFTS_PER_DAY:
        return points, hearts_for_points(points), 0, (
            f"{npc.name} 今天已经收过 {MAX_GIFTS_PER_DAY} 份礼物了，明天再送吧（好感不变）。"
        )
    delta = gift_points(npc, gift) + (10 if personality_bonus else 0)
    new_points = points + delta
    before = hearts_for_points(points)
    after = hearts_for_points(new_points)
    note = gift_reaction(npc, gift, delta)
    if after > before:
        note += f"　好感升到 {after} 心！"
    return new_points, after, delta, note


def unlock_lines(npc: NpcDef, hearts: int) -> tuple[str, ...]:
    """当前心数下已解锁的全部剧情台词（0..hearts）。"""
    h = clamp(hearts, 0, MAX_HEARTS)
    return npc.heart_lines[: h + 1]


def next_unlock_at(npc: NpcDef, hearts: int) -> int | None:
    """下一心解锁点（已满心返回 None，用于 UI 诚实展示「已满」）。"""
    return None if hearts >= MAX_HEARTS else hearts + 1


# ----------------------------------------------------------------------
# 对话
# ----------------------------------------------------------------------

#: 时段语气偏移（不同时间去对话内容不同）
PART_TONE: dict[str, str] = {
    "dawn": "天刚亮，说话都轻一点。",
    "morning": "早上好，活儿正多。",
    "noon": "日头正毒，找个阴凉地方吧。",
    "afternoon": "下午容易犯困。",
    "dusk": "天要黑了，回家路上小心。",
    "night": "夜里安静，适合说点真话。",
    "late_night": "这么晚还没睡？",
}

#: 角色身份/性格对白的影响（B7 要求「角色身份/性格影响对话」）
IDENTITY_TONE: dict[str, str] = {
    "farmer": "你手上那层茧，一看就是干农活的。",
    "scholar": "你说话有条理，不像在这儿待久了的。",
    "artisan": "你这手稳，适合做精细活。",
    "traveler": "你是外来的吧？我看你什么都新鲜。",
    "healer": "你身上有草药味。",
    "default": "",
}

PERSONALITY_TONE: dict[str, str] = {
    "lively": "你笑起来的时候，这地方都亮一点。",
    "cool": "……嗯，你不用解释。",
    "melancholy": "你有点想事情？我也是。",
    "chatty": "你话真多——我喜欢。",
}


def dialogue(npc: NpcDef, clock: GameClock, *, hearts: int, identity: str = "default",
             personality: str = "default") -> dict[str, object]:
    """生成一次对话（服务端同款：同一 NPC + 同一状态 → 同一句，可复算）。"""
    slot = locate(npc, clock)
    lines = [npc.heart_lines[clamp(hearts, 0, MAX_HEARTS)]]
    tone_parts = [
        PART_TONE.get(clock.part, ""),
        IDENTITY_TONE.get(identity, ""),
        PERSONALITY_TONE.get(personality, ""),
    ]
    lines.extend(p for p in tone_parts if p)
    if clock.is_night or slot.activity == "睡觉":
        return {
            "npc_id": npc.id,
            "text": f"{npc.name}已经睡了（{slot.place}），明天再说。",
            "scene": "asleep",
            "hearts": hearts,
            "unlocked": list(unlock_lines(npc, hearts)),
        }
    # 随机补充一句：同 (npc, day, part) 必得同句 → 可复算
    extra = pick(rng_for("dialogue", npc.id, clock.day, clock.part),
                 ("今天也没什么特别的事。", "你要是不忙，坐会儿。", "风从那边来。",
                  "路上小心。", "明天还来吗？"))
    lines.append(extra)
    return {
        "npc_id": npc.id,
        "text": "\n".join(lines),
        "scene": slot.activity,
        "hearts": hearts,
        "unlocked": list(unlock_lines(npc, hearts)),
    }


def commissions_for(npc: NpcDef) -> tuple[dict[str, object], ...]:
    """该 NPC 可发布的委托（材料需求显式列出，材料不足由调用方报错）。"""
    out = []
    for cid, material, need in npc.commissions:
        out.append({"id": cid, "material": material, "need": need,
                    "label": f"{npc.name}想要 {need} 个 {material}"})
    return tuple(out)


# ----------------------------------------------------------------------
# 委托结算
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class CommissionDef:
    id: str
    npc_id: str
    material: str
    need: int
    reward_coins: int
    reward_hearts_points: int
    min_hearts: int = 0


#: 委托总表（由名册派生 + 手工奖励，保证「可复算」：奖励写死，不随时间变）
COMMISSIONS: tuple[CommissionDef, ...] = tuple(
    CommissionDef(
        cid,
        n.id,
        mat,
        need,
        reward_coins=40 + need * 15,
        reward_hearts_points=30 + need * 2,
    )
    for n in _NPCS
    for cid, mat, need in n.commissions
)


def commission_by_id(commission_id: str) -> CommissionDef:
    for c in COMMISSIONS:
        if c.id == commission_id:
            return c
    raise KeyError(f"unknown commission {commission_id!r}")


def turn_in_commission(
    commission: CommissionDef, bag: dict[str, int], *, hearts: int
) -> tuple[dict[str, int], int, int, str]:
    """交委托：返回 (新背包, 获金币, 获好感点数, 文案)。材料不足抛 ValueError（调用方转 422）。"""
    if hearts < commission.min_hearts:
        raise ValueError(f"需要好感 {commission.min_hearts} 心才能接这个委托")
    have = int(bag.get(commission.material, 0) or 0)
    if have < commission.need:
        raise ValueError(
            f"材料不足：{commission.material} 需要 {commission.need}，只有 {have}"
        )
    new_bag = dict(bag)
    left = have - commission.need
    if left:
        new_bag[commission.material] = left
    else:
        new_bag.pop(commission.material, None)
    return new_bag, commission.reward_coins, commission.reward_hearts_points, (
        f"交付 {commission.material} ×{commission.need} 完成："
        f"+{commission.reward_coins} 金币 / 好感 +{commission.reward_hearts_points}"
    )


# 便捷：主题名给 NPC 表用（避免调用方各自 import）
__all__ = [
    "MAX_HEARTS", "POINTS_PER_HEART", "MAX_GIFTS_PER_DAY", "NpcDef", "ScheduleSlot",
    "NPCS", "npcs_of", "get_npc", "locate", "awake", "npc_status_rows",
    "hearts_for_points", "gift_points", "gift_reaction", "apply_gift",
    "unlock_lines", "next_unlock_at", "dialogue", "commissions_for",
    "CommissionDef", "COMMISSIONS", "commission_by_id", "turn_in_commission",
    "PART_TONE", "IDENTITY_TONE", "PERSONALITY_TONE", "theme_label", "DAY_PARTS",
]