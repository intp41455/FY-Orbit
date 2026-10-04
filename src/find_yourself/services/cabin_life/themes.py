"""B 包主题注册表：现有 5 背景 + 规范四大主题（**两类全都要**，§0.1 / §8-C）。

本模块只放**玩法侧**数据（标签 / 天气池 / 采集物 / 村民喜好 / 经营货品），
美术资源归 A 包（`cabinPixelArt.ts`），此处**不引用也不修改** A1 冻结的常量。

主题 id 与 W2 已有的 forest|garden|stream|field|planet 保持同名列，
新增四主题以 magic|scifi|country|ink 追加，**不改动旧 id**，以免存量存档失效。
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEGACY_THEME_IDS: tuple[str, ...] = ("forest", "garden", "stream", "field", "planet")
SPEC_THEME_IDS: tuple[str, ...] = ("magic", "scifi", "country", "ink")
THEME_IDS: tuple[str, ...] = LEGACY_THEME_IDS + SPEC_THEME_IDS


@dataclass(frozen=True)
class GatherNode:
    """一个可采集点（B2）。产出与冷却都是纯数据。"""

    id: str
    label: str
    verb: str
    material: str
    qty: tuple[int, int]
    minutes: int
    tile: tuple[int, int]


@dataclass(frozen=True)
class ThemeDef:
    id: str
    label: str
    tagline: str
    weather_pool: tuple[str, ...]
    materials: tuple[str, ...]
    gather_nodes: tuple[GatherNode, ...]
    shop_goods: tuple[str, ...]
    favorite_goods: tuple[str, ...]
    palette_hint: tuple[str, ...] = field(default=())


def _n(
    node_id: str,
    label: str,
    verb: str,
    material: str,
    qty: tuple[int, int],
    minutes: int,
    tile: tuple[int, int],
) -> GatherNode:
    return GatherNode(node_id, label, verb, material, qty, minutes, tile)


_THEMES: tuple[ThemeDef, ...] = (
    ThemeDef(
        id="forest",
        label="老林子",
        tagline="苔藓与松脂的味道",
        weather_pool=("clear", "rain", "fog", "wind"),
        materials=("pinecone", "mushroom", "wood", "herb"),
        gather_nodes=(
            _n("forest_pine", "老松树", "砍伐", "wood", (1, 2), 12, (18, 62)),
            _n("forest_mush", "蘑菇圈", "采摘", "mushroom", (1, 2), 6, (27, 58)),
            _n("forest_cone", "落松果", "拾取", "pinecone", (1, 3), 4, (34, 66)),
            _n("forest_herb", "林间草药", "采集", "herb", (1, 2), 8, (41, 60)),
        ),
        shop_goods=("mushroom", "wood", "herb"),
        favorite_goods=("mushroom", "honey"),
    ),
    ThemeDef(
        id="garden",
        label="后花园",
        tagline="浇过水的那种安静",
        weather_pool=("clear", "rain", "cloud", "wind"),
        materials=("flower", "seed", "honey", "vegetable"),
        gather_nodes=(
            _n("garden_flower", "花丛", "采集", "flower", (1, 3), 5, (22, 60)),
            _n("garden_seed", "种子袋", "拾取", "seed", (1, 2), 4, (30, 64)),
            _n("garden_bee", "蜂箱", "取蜜", "honey", (1, 1), 10, (38, 58)),
            _n("garden_veg", "菜畦", "收割", "vegetable", (2, 3), 9, (45, 66)),
            _n("garden_well", "井台", "打水", "water", (1, 2), 5, (52, 64)),
        ),
        shop_goods=("flower", "honey", "vegetable"),
        favorite_goods=("flower", "honey", "jam"),
    ),
    ThemeDef(
        id="stream",
        label="溪水边",
        tagline="水声把白天洗得很轻",
        weather_pool=("clear", "rain", "fog", "cloud"),
        materials=("fish", "pebble", "moss", "reed"),
        gather_nodes=(
            _n("stream_fish", "浅滩", "垂钓", "fish", (1, 1), 18, (25, 63)),
            _n("stream_pebble", "河滩", "拾取", "pebble", (1, 3), 4, (33, 67)),
            _n("stream_moss", "水草", "采集", "moss", (1, 2), 7, (40, 62)),
            _n("stream_reed", "芦苇荡", "收割", "reed", (1, 2), 8, (47, 65)),
        ),
        shop_goods=("fish", "moss", "pebble"),
        favorite_goods=("fish", "honey"),
    ),
    ThemeDef(
        id="field",
        label="金黄田野",
        tagline="风一过就是一整片声音",
        weather_pool=("clear", "rain", "wind", "hail"),
        materials=("wheat", "pumpkin", "firefly_jar", "cotton"),
        gather_nodes=(
            _n("field_wheat", "麦田", "收割", "wheat", (2, 3), 10, (24, 62)),
            _n("field_pumpkin", "南瓜地", "采摘", "pumpkin", (1, 2), 8, (32, 64)),
            _n("field_firefly", "萤火虫丛", "捕捉", "firefly_jar", (1, 1), 12, (39, 60)),
            _n("field_cotton", "棉田", "采摘", "cotton", (1, 3), 9, (46, 66)),
        ),
        shop_goods=("wheat", "pumpkin", "cotton"),
        favorite_goods=("pumpkin", "jam"),
    ),
    ThemeDef(
        id="planet",
        label="观星台",
        tagline="这里的天比别处低一点",
        weather_pool=("clear", "starfall", "snow", "fog"),
        materials=("stardust", "crystal", "moonstone", "resin"),
        gather_nodes=(
            _n("planet_stardust", "星尘带", "收集", "stardust", (1, 2), 10, (26, 60)),
            _n("planet_crystal", "晶簇", "开采", "crystal", (1, 2), 14, (35, 63)),
            _n("planet_moon", "月石坑", "挖掘", "moonstone", (1, 1), 20, (43, 61)),
            _n("planet_resin", "树脂囊", "采集", "resin", (1, 2), 8, (50, 66)),
        ),
        shop_goods=("crystal", "stardust", "resin"),
        favorite_goods=("crystal", "moonstone"),
    ),
    ThemeDef(
        id="magic",
        label="魔法大陆",
        tagline="会发光的不只是灯笼",
        weather_pool=("clear", "rain", "fog", "starfall", "wind"),
        materials=("magic_herb", "magic_crystal", "stardust", "slime_jelly"),
        gather_nodes=(
            _n("magic_herb", "魔法草药", "采集", "magic_herb", (2, 3), 8, (24, 61)),
            _n("magic_crystal", "漂浮水晶", "采集", "magic_crystal", (1, 2), 12, (33, 62)),
            _n("magic_stardust", "星尘旋涡", "收集", "stardust", (1, 2), 10, (41, 59)),
            _n("magic_slime", "史莱姆", "捕捉", "slime_jelly", (1, 1), 9, (48, 65)),
        ),
        shop_goods=("potion", "magic_crystal", "magic_scroll"),
        favorite_goods=("potion", "honey"),
    ),
    ThemeDef(
        id="scifi",
        label="科幻星球",
        tagline="连风都带着循环过滤的味道",
        weather_pool=("clear", "sandstorm", "snow", "fog", "static"),
        materials=("alloy_ore", "gas_canister", "bio_sample", "energy_cell"),
        gather_nodes=(
            _n("scifi_ore", "外星矿脉", "开采", "alloy_ore", (1, 3), 12, (25, 62)),
            _n("scifi_gas", "稀有气体罐", "抽取", "gas_canister", (1, 1), 15, (34, 60)),
            _n("scifi_bio", "生物样本", "采集", "bio_sample", (1, 2), 11, (42, 64)),
            _n("scifi_cell", "能量电池", "回收", "energy_cell", (1, 2), 9, (49, 63)),
        ),
        shop_goods=("tech_gear", "battery", "alloy_ore"),
        favorite_goods=("battery", "stardust"),
    ),
    ThemeDef(
        id="country",
        label="田园乡村",
        tagline="有人比你更早醒来",
        weather_pool=("clear", "rain", "wind", "hail", "cloud"),
        materials=("wheat", "vegetable", "fish", "fruit"),
        gather_nodes=(
            _n("country_field", "农田", "收割", "wheat", (2, 4), 10, (23, 62)),
            _n("country_orchard", "果园", "采摘", "fruit", (2, 3), 9, (32, 60)),
            _n("country_pond", "鱼塘", "垂钓", "fish", (1, 2), 16, (40, 64)),
            _n("country_veg", "菜地", "收割", "vegetable", (1, 3), 8, (47, 66)),
            _n("country_well", "村井", "打水", "water", (1, 2), 5, (54, 63)),
        ),
        shop_goods=("bread", "jam", "handicraft"),
        favorite_goods=("bread", "jam", "flower"),
    ),
    ThemeDef(
        id="ink",
        label="古风桃源",
        tagline="水墨淡彩里的一盏酒旗",
        weather_pool=("clear", "rain", "fog", "snow", "wind"),
        materials=("tea_leaf", "peach_blossom", "bamboo_shoot", "herbal"),
        gather_nodes=(
            _n("ink_tea", "茶垄", "采摘", "tea_leaf", (2, 3), 9, (24, 61)),
            _n("ink_peach", "桃林", "摘花", "peach_blossom", (2, 4), 8, (33, 63)),
            _n("ink_bamboo", "竹林", "挖笋", "bamboo_shoot", (1, 2), 12, (41, 60)),
            _n("ink_herb", "山中药田", "采药", "herbal", (1, 2), 10, (48, 65)),
            _n("ink_stream", "山涧", "打水", "water", (1, 2), 6, (55, 64)),
        ),
        shop_goods=("tea", "wine", "pastry", "incense"),
        favorite_goods=("tea", "pastry", "peach_blossom"),
    ),
)

THEMES: dict[str, ThemeDef] = {t.id: t for t in _THEMES}

assert set(THEMES) == set(THEME_IDS), "主题注册表与 THEME_IDS 不一致"


def get_theme(theme_id: str) -> ThemeDef:
    """取主题定义；未知 id 明确报错（不静默回落到 forest 伪造内容）。"""
    try:
        return THEMES[theme_id]
    except KeyError as exc:  # pragma: no cover - 由调用方测试覆盖
        raise KeyError(f"unknown theme {theme_id!r}; known={sorted(THEMES)}") from exc


def theme_label(theme_id: str) -> str:
    return get_theme(theme_id).label


def theme_ids() -> tuple[str, ...]:
    return THEME_IDS