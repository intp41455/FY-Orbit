"""B3 · 制作系统（纯逻辑层）。

说明书 §5-B3：配方（材料 + 金币）随**角色技能**解锁；制作台 UI；产物入背包。
验收：配方表可配置；材料不足明确报错（不伪造成功）。

设计：
    * 配方表 `RECIPES` 是纯数据（材料 dict + 金币 + 产物 + 所需技能等级），
      「可配置」= 加一行就是一个新配方，不需要改逻辑；
    * 技能等级来自角色画像（`SKILL_POINTS` 表按主题/行为累计），不是随时间白送；
    * `craft()` 返回**不可变**结果：扣料扣币都体现在返回值里，调用方落库。
"""

from __future__ import annotations

from dataclasses import dataclass

from .rng import clamp


@dataclass(frozen=True)
class Recipe:
    id: str
    label: str
    outputs: tuple[tuple[str, int], ...]
    inputs: tuple[tuple[str, int], ...]
    coins: int
    #: 所需技能等级（`skills.level_for` 算出）
    skill_level: int
    #: 主题：'' = 通用
    theme: str = ""
    #: 归类（制作台 UI 分页）
    category: str = "misc"


def _r(
    rid: str,
    label: str,
    outputs: tuple[tuple[str, int], ...],
    inputs: tuple[tuple[str, int], ...],
    coins: int,
    skill_level: int,
    *,
    theme: str = "",
    category: str = "misc",
) -> Recipe:
    return Recipe(rid, label, outputs, inputs, coins, skill_level, theme, category)


#: 配方总表（可配置：增删改这一段即可）
RECIPES: tuple[Recipe, ...] = (
    # 通用
    _r("bread", "面包", (("bread", 2),), (("wheat", 2), ("honey", 1)), 8, 1, category="food"),
    _r("jam", "果酱", (("jam", 2),), (("fruit", 2), ("honey", 1)), 12, 1, category="food"),
    _r("soup", "热汤", (("soup", 2),), (("vegetable", 2), ("pebble", 1)), 10, 2, category="food"),
    _r("handicraft", "手工品", (("handicraft", 1),), (("wood", 3), ("cotton", 1)), 20, 2,
       category="craft"),
    _r("incense", "线香", (("incense", 2),), (("herb", 2), ("wood", 1)), 14, 2, category="craft"),
    _r("tea", "茶", (("tea", 2),), (("tea_leaf", 2),), 15, 3, category="drink"),
    _r("wine", "酒", (("wine", 1),), (("fruit", 3), ("honey", 1)), 24, 3, category="drink"),
    _r("pastry", "糕点", (("pastry", 2),), (("wheat", 2), ("honey", 1), ("tea_leaf", 1)),
       18, 3, category="food"),
    # forest / garden
    _r("wood_chair", "木椅", (("furniture_chair", 1),), (("wood", 4),), 30, 2,
       theme="forest", category="furniture"),
    _r("flower_vase", "花瓶", (("furniture_vase", 1),), (("flower", 3), ("pebble", 1)), 26, 2,
       theme="garden", category="furniture"),
    _r("mushroom_stew", "蘑菇汤", (("soup", 3),), (("mushroom", 3), ("herb", 1)), 16, 2,
       theme="forest", category="food"),
    # stream / field
    _r("dried_fish", "鱼干", (("fish", 3),), (("fish", 1), ("reed", 1)), 12, 1,
       theme="stream", category="food"),
    _r("pumpkin_pie", "南瓜派", (("pastry", 3),), (("pumpkin", 2), ("wheat", 2)), 26, 3,
       theme="field", category="food"),
    _r("field_bundle", "麦捆", (("wheat", 4),), (("wheat", 2), ("cotton", 1)), 10, 1,
       theme="field", category="material"),
    # planet
    _r("crystal_lamp", "晶灯", (("furniture_lamp", 1),), (("crystal", 2), ("resin", 1)), 48, 4,
       theme="planet", category="furniture"),
    _r("moon_charm", "月石护符", (("furniture_charm", 1),), (("moonstone", 1), ("stardust", 3)),
       66, 5, theme="planet", category="furniture"),
    # magic
    _r("potion", "魔法药水", (("potion", 1),), (("magic_herb", 2), ("magic_crystal", 1)), 55, 4,
       theme="magic", category="alchemy"),
    _r("magic_scroll", "魔法卷轴", (("magic_scroll", 1),),
       (("peach_blossom", 2), ("stardust", 2)), 70, 5, theme="magic", category="alchemy"),
    _r("slime_jam", "史莱姆果冻", (("jam", 2),), (("slime_jelly", 2), ("honey", 1)), 20, 2,
       theme="magic", category="alchemy"),
    # scifi
    _r("battery", "能量电池", (("battery", 2),), (("energy_cell", 1), ("alloy_ore", 2)), 52, 4,
       theme="scifi", category="tech"),
    _r("tech_gear", "科技装备", (("tech_gear", 1),), (("alloy_ore", 3), ("gas_canister", 1)),
       88, 5, theme="scifi", category="tech"),
    _r("bio_lamp", "生态灯", (("furniture_lamp", 1),), (("bio_sample", 1), ("crystal", 2)),
       50, 4, theme="scifi", category="furniture"),
    # country
    _r("farm_jam", "农家果酱", (("jam", 3),), (("fruit", 3), ("honey", 1)), 14, 1,
       theme="country", category="food"),
    _r("cotton_cloth", "棉布", (("cotton", 2),), (("cotton", 3),), 18, 2,
       theme="country", category="craft"),
    # ink
    _r("peach_wine", "桃花酿", (("wine", 2),), (("peach_blossom", 3), ("honey", 1)), 42, 4,
       theme="ink", category="drink"),
    _r("bamboo_basket", "竹器", (("handicraft", 2),), (("bamboo_shoot", 2), ("reed", 2)), 24, 2,
       theme="ink", category="craft"),
    _r("ink_incense", "药香", (("incense", 3),), (("herbal", 2), ("peach_blossom", 1)), 30, 3,
       theme="ink", category="craft"),
)

RECIPES_BY_ID: dict[str, Recipe] = {r.id: r for r in RECIPES}


def get_recipe(recipe_id: str) -> Recipe:
    try:
        return RECIPES_BY_ID[recipe_id]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"unknown recipe {recipe_id!r}; known={sorted(RECIPES_BY_ID)}") from exc


# ----------------------------------------------------------------------
# 技能
# ----------------------------------------------------------------------

#: 技能升级所需经验（可配置）
SKILL_THRESHOLDS: tuple[int, ...] = (0, 30, 80, 160, 280, 450)


def skill_level(exp: int) -> int:
    """经验 → 技能等级（1..6）。经验为负报错，不静默当 0。"""
    if exp < 0:
        raise ValueError("exp must be >= 0")
    level = 1
    for i, need in enumerate(SKILL_THRESHOLDS):
        if exp >= need:
            level = i + 1
    return clamp(level, 1, len(SKILL_THRESHOLDS))


def recipes_for(theme: str, level: int) -> tuple[Recipe, ...]:
    """当前主题可用的配方（通用 + 本主题），按所需技能等级升序。"""
    pool = [r for r in RECIPES if r.theme in ("", theme)]
    return tuple(sorted(pool, key=lambda r: (r.skill_level, r.category, r.id)))


def is_unlocked(recipe: Recipe, level: int) -> bool:
    return level >= recipe.skill_level


def unlock_state(recipe: Recipe, level: int) -> dict[str, object]:
    """给 UI 的解锁态：解锁 / 差几点经验（不静默灰显，必须带原因）。"""
    unlocked = is_unlocked(recipe, level)
    need = None if unlocked else recipe.skill_level
    return {
        "id": recipe.id,
        "label": recipe.label,
        "category": recipe.category,
        "unlocked": unlocked,
        "required_level": recipe.skill_level,
        "current_level": level,
        "need_level": need,
    }


# ----------------------------------------------------------------------
# 制作
# ----------------------------------------------------------------------

def missing_inputs(recipe: Recipe, bag: dict[str, int]) -> dict[str, int]:
    """缺什么差多少（空 dict = 材料齐）。"""
    out: dict[str, int] = {}
    for material, need in recipe.inputs:
        have = int(bag.get(material, 0) or 0)
        if have < need:
            out[material] = need - have
    return out


@dataclass(frozen=True)
class CraftReceipt:
    recipe_id: str
    label: str
    outputs: tuple[tuple[str, int], ...]
    spent: tuple[tuple[str, int], ...]
    coins_spent: int
    bag_after: dict[str, int]
    coins_after: int
    exp_gained: int
    note: str


EXP_PER_RECIPE = 20


def craft(
    recipe_id: str,
    bag: dict[str, int],
    *,
    coins: int,
    level: int,
    theme: str = "",
) -> CraftReceipt:
    """制作一次。材料/金币/技能任一不足 → 抛 ValueError（调用方转 422，不伪造成功）。"""
    recipe = get_recipe(recipe_id)
    if recipe.theme and theme and recipe.theme != theme:
        raise ValueError(f"{recipe.label} 是 {recipe.theme} 主题的配方，当前主题 {theme}")
    if not is_unlocked(recipe, level):
        raise ValueError(
            f"{recipe.label} 需要技能 Lv{recipe.skill_level}，当前 Lv{level}"
        )
    missing = missing_inputs(recipe, bag)
    if missing:
        detail = ", ".join(f"{m} 缺 {n}" for m, n in sorted(missing.items()))
        raise ValueError(f"材料不足：{detail}（本次未制作，背包未变动）")
    if coins < recipe.coins:
        raise ValueError(f"金币不足：需要 {recipe.coins}，只有 {coins}（本次未制作）")

    new_bag = {k: int(v) for k, v in bag.items()}
    for material, need in recipe.inputs:
        left = new_bag.get(material, 0) - need
        if left > 0:
            new_bag[material] = left
        else:
            new_bag.pop(material, None)
    gained: list[str] = []
    for material, count in recipe.outputs:
        new_bag[material] = new_bag.get(material, 0) + count
        gained.append(f"{material}×{count}")
    return CraftReceipt(
        recipe_id=recipe.id,
        label=recipe.label,
        outputs=recipe.outputs,
        spent=recipe.inputs,
        coins_spent=recipe.coins,
        bag_after=new_bag,
        coins_after=coins - recipe.coins,
        exp_gained=EXP_PER_RECIPE,
        note=f"制作完成：{'、'.join(gained)}（材料与金币已扣除）",
    )