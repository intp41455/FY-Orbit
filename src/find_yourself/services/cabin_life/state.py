"""B 包 · 单机存档快照组装（B11 的纯函数部分）。

把 `cabin_life` 各模块的纯函数**组装成一份可序列化的存档文档**（`LifeSave`），
供持久化层 / HTTP 通道 / 前端契约测试使用。

本模块仍然**零 IO**：它只做「给定游戏状态 → 给出一份 dict」。落库与路由由后续切片负责。

诚实原则：
    * 快照里的每个字段都必须有来源函数；没有来源的字段**不写**（宁可缺字段，
      也不让前端拿到一个恒为 0 的假字段）；
    * 天气 / 时间 / 售价等派生值都写进快照，前端因此**不需要**（也不允许）自己重算规则。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import clock, crafting, interaction, npcs, quests, shop, themes
from .clock import GameClock
from .shop import ShopState


@dataclass
class LifeSave:
    """一份完整的 B 包存档（可 JSON 序列化）。"""

    owner: str
    theme: str
    clock: dict[str, object]
    weather: dict[str, object]
    bag: dict[str, int]
    coins: int
    skill_exp: int
    #: npc_id -> 好感点数
    affinity: dict[str, int] = field(default_factory=dict)
    #: npc_id -> 今日已送礼次数
    gifts_today: dict[str, int] = field(default_factory=dict)
    shop: dict[str, object] = field(default_factory=dict)
    quest_log: dict[str, object] = field(default_factory=dict)
    #: 采集点每日计数（node_id -> 次数）
    gather_counts: dict[str, int] = field(default_factory=dict)
    #: 室内建造与家具摆放状态（H6 建造布局落库）
    build_state: dict[str, object] = field(default_factory=dict)
    version: int = 1


def new_save(owner: str, theme: str, *, day: int = 1) -> LifeSave:
    """新存档：06:00 起床、零金币、零背包、Lv1 技能、主线第一环已接。"""
    themes.get_theme(theme)  # 未知主题直接报错
    c = clock.GameClock(day=day, minute=clock.DAILY_RESET_MINUTE)
    return LifeSave(
        owner=owner,
        theme=theme,
        clock={"day": c.day, "minute": c.minute, "part": c.part, "part_label": c.part_label},
        weather=_weather_block(owner, theme, c),
        bag={},
        coins=0,
        skill_exp=0,
        affinity={n.id: 0 for n in npcs.npcs_of(theme)},
        gifts_today={},
        shop=asdict(shop.new_shop("grocery", owner, day)),
        quest_log={
            "day": day,
            "entries": [asdict(e) for e in quests.start_log(theme).entries],
        },
        gather_counts={},
        build_state={},
    )


def _weather_block(owner: str, theme: str, c: GameClock) -> dict[str, object]:
    w = clock.weather_of(c, owner, theme)
    return {
        "id": w.id,
        "label": w.label,
        "icon": w.icon,
        "light": clock.light_level(c, owner, theme),
        "yield_pct": w.yield_pct,
        "pace_pct": w.pace_pct,
        "demand_pct": w.demand_pct,
        "blocks_gather": w.blocks_gather,
    }


def to_dict(save: LifeSave) -> dict[str, object]:
    return asdict(save)


def hud_line(save: LifeSave) -> str:
    """顶部状态栏一行文案（前端直接显示，不二次润色）。"""
    c = clock.GameClock(day=int(save.clock["day"]), minute=int(save.clock["minute"]))
    w = clock.weather_of(c, save.owner, save.theme)
    return f"第{c.day}天 {c.part_label} {w.label} · {save.coins} 金币"


def hearts_display(save: LifeSave, npc_id: str) -> str:
    """好感心数展示（如 `♥♥♥♡♡…`，10 心满）。"""
    hearts = npcs.hearts_for_points(int(save.affinity.get(npc_id, 0)))
    return "♥" * hearts + "♡" * (npcs.MAX_HEARTS - hearts)


def npc_rows(save: LifeSave) -> list[dict[str, object]]:
    """社交面板一览：作息位置 + 心数 + 头顶标记。"""
    c = clock.GameClock(day=int(save.clock["day"]), minute=int(save.clock["minute"]))
    log = log_from_dict(save.quest_log)
    rows = []
    for n in npcs.npcs_of(save.theme):
        pts = int(save.affinity.get(n.id, 0))
        rows.append(
            {
                "id": n.id,
                "name": n.name,
                "role": n.role,
                "place": npcs.locate(n, c).place,
                "activity": npcs.locate(n, c).activity,
                "awake": npcs.awake(n, c),
                "hearts": npcs.hearts_for_points(pts),
                "hearts_display": hearts_display(save, n.id),
                "marker": quests.marker_for(log, n.id),
            }
        )
    return rows


def shop_rows(save: LifeSave) -> list[dict[str, object]]:
    c = clock.GameClock(day=int(save.clock["day"]), minute=int(save.clock["minute"]))
    st = shop.ShopState(**save.shop)  # type: ignore[arg-type]
    return shop.shop_rows(save.owner, st, c.day, save.theme)


def craft_rows(save: LifeSave) -> list[dict[str, object]]:
    level = crafting.skill_level(save.skill_exp)
    return [crafting.unlock_state(r, level) for r in crafting.recipes_for(save.theme, level)]


def gather_rows(save: LifeSave, player_tile: tuple[int, int] | None = None
                ) -> list[dict[str, object]]:
    return interaction.gatherable_rows(save.theme, player_tile)


def log_from_dict(raw: dict[str, object]) -> quests.QuestLog:
    entries = tuple(
        quests.QuestEntry(
            quest_id=str(e["quest_id"]),
            progress=int(e.get("progress", 0)),
            done=bool(e.get("done", False)),
            claimed=bool(e.get("claimed", False)),
        )
        for e in raw.get("entries", [])  # type: ignore[union-attr]
    )
    return quests.QuestLog(entries=entries, day=int(raw.get("day", 1)))  # type: ignore[arg-type]