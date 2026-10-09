"""B1 交互框架 + B2 采集（纯逻辑层）。

说明书 §5-B1/B2：
    * B1：靠近可交互物 → 头顶弹「小手」提示 → 确认键交互；
    * B2：砍树/挖矿/钓鱼/采果/拔草/打水/捡杂物，每动作配像素动画与产出物；
      验收「3 秒规则」+「产出进背包」。

本模块只产出**数据**（哪些点在什么格、靠近提示什么、按确认后得到什么、花多少游戏分钟），
渲染与按键由前端消费。这里**不碰** `cabinScene.ts`（A1 冻结 / A5 主渲染段）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .clock import GameClock, gather_blocked, gather_minutes, gather_yield
from .rng import rng_for
from .themes import GatherNode, ThemeDef, get_theme

#: 交互距离（格）。玩家所在格与目标格曼哈顿距离 ≤ 此值即出现提示。
INTERACT_RANGE_TILES = 1

#: 动作 → 动画键（前端按 key 播像素动画；键名写死，前端有则播、无则如实 fallback）
ANIMATIONS: dict[str, str] = {
    "chop": "anim_chop",
    "mine": "anim_mine",
    "fish": "anim_fish",
    "pick": "anim_pick",
    "harvest": "anim_harvest",
    "dig": "anim_dig",
    "water": "anim_water",
    "collect": "anim_collect",
}

#: 动作 → 头顶提示语（3 秒规则：提示语要说清「能做什么」）
ACTION_LABELS: dict[str, str] = {
    "chop": "砍伐",
    "mine": "开采",
    "fish": "垂钓",
    "pick": "拾取",
    "harvest": "收割",
    "dig": "挖掘",
    "water": "打水",
    "collect": "收集",
}


@dataclass(frozen=True)
class Interactable:
    """一个可交互物：格坐标 + 动作 + 产出 + 耗时。"""

    node: GatherNode
    action: str

    @property
    def id(self) -> str:
        return self.node.id

    @property
    def tile(self) -> tuple[int, int]:
        return self.node.tile


#: 动作类型（按 B2 要求覆盖：砍树/挖矿/钓鱼/采果/拔草/打水/捡杂物）
ACTION_OF_VERB: dict[str, str] = {
    # ---- B2 要求覆盖的八大动作 ----
    "砍伐": "chop",      # 砍树
    "开采": "mine",      # 挖矿
    "垂钓": "fish",      # 钓鱼
    "摘花": "pick",      # 采果 / 摘花
    "收割": "harvest",   # 拔草 / 收割
    "打水": "water",     # 打水
    "拾取": "collect",   # 捡杂物
    # ---- themes.py 实际用到的动词（必须全部登记，缺一即 KeyError 暴露） ----
    "采集": "collect",
    "采摘": "harvest",
    "采收": "harvest",
    "采药": "collect",
    "采蜜": "collect",
    "取蜜": "collect",
    "捕捉": "collect",
    "收集": "collect",
    "回收": "collect",
    "抽取": "collect",
    "取样": "collect",
    "撒网": "collect",
    "收网": "collect",
    "翻找": "collect",
    "摸鱼": "fish",
    "收花": "harvest",
    "看花": "pick",
    "挖掘": "dig",
    "挖笋": "dig",
    "开垦": "dig",
}


def interactables_of(theme: str) -> tuple[Interactable, ...]:
    """主题的全部可交互物（坐标来自 `themes.py`，与美术资源解耦）。"""
    defn: ThemeDef = get_theme(theme)
    out = []
    for node in defn.gather_nodes:
        action = ACTION_OF_VERB.get(node.verb)
        if action is None:  # pragma: no cover - themes.py 已覆盖全部动词
            raise KeyError(f"{node.id}: 未知动词 {node.verb!r}（需登记到 ACTION_OF_VERB）")
        out.append(Interactable(node, action))
    return tuple(out)


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def nearest_interactable(
    theme: str, player_tile: tuple[int, int]
) -> tuple[Interactable | None, int]:
    """最近可交互物 + 距离（超出范围返回 (None, 距离)）。"""
    best: Interactable | None = None
    best_d = 10**9
    for it in interactables_of(theme):
        d = manhattan(it.tile, player_tile)
        if d < best_d:
            best, best_d = it, d
    if best_d > INTERACT_RANGE_TILES:
        return None, best_d
    return best, best_d


def prompt_for(it: Interactable) -> str:
    """头顶提示（前端渲染为小手图标 + 文字）。"""
    action = ACTION_LABELS.get(it.action, it.action)
    return f"✋ {action} {it.node.label}（{it.node.material}）"


def prompt_for_tile(theme: str, player_tile: tuple[int, int]) -> str | None:
    it, _ = nearest_interactable(theme, player_tile)
    return prompt_for(it) if it else None


# ----------------------------------------------------------------------
# 采集结算
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class GatherReceipt:
    node_id: str
    label: str
    action: str
    animation: str
    material: str
    qty: int
    minutes: int
    bag_after: dict[str, int]
    clock_after: GameClock
    weather: str
    note: str


def roll_qty(owner: str, node: GatherNode, day: int, lo: int, hi: int) -> int:
    """节点产量的随机部分：同 (owner, node, day, 次数) 必得同一结果。

    `lo/hi` 由调用方传入（已含天气修正），这里只做区间内的确定性取整。
    """
    if hi < lo:
        raise ValueError("hi must be >= lo")
    span = hi - lo + 1
    return lo + rng_for("yield", owner, node.id, day).randrange(span)


def gather(
    owner: str,
    theme: str,
    node_id: str,
    clock: GameClock,
    bag: dict[str, int],
    *,
    times_today: int = 0,
) -> GatherReceipt:
    """执行一次采集。

    诚实原则：
      * 极端天气（沙暴/静电风暴）全域禁采集 → 抛 ValueError 并说明替代路径；
      * 每个点每天上限 `DAILY_NODE_LIMIT`，超限如实报错，不静默给 0；
      * 产出直接进背包（返回 bag_after），不假装成功。
    """
    it = next((x for x in interactables_of(theme) if x.id == node_id), None)
    if it is None:
        raise KeyError(f"unknown gather node {node_id!r} in theme {theme!r}")
    if gather_blocked(clock, owner, theme):
        raise ValueError(
            f"天气不允许采集（{theme} 今日极端天气），先去室内或等天气过去"
        )
    if times_today >= DAILY_NODE_LIMIT:
        raise ValueError(
            f"{it.node.label} 今天已经采过 {DAILY_NODE_LIMIT} 次了，明天再来"
        )

    node = it.node
    lo, hi = node.qty
    # 天气修正后的区间（两个端点都乘 weather 系数 → 单调，可复算）
    base_lo = gather_yield(clock, owner, theme, lo)
    base_hi = gather_yield(clock, owner, theme, hi)
    qty = roll_qty(owner, node, clock.day, min(base_lo, base_hi), max(base_lo, base_hi))
    minutes = gather_minutes(clock, owner, theme, node.minutes)

    new_bag = {k: int(v) for k, v in bag.items()}
    new_bag[node.material] = new_bag.get(node.material, 0) + qty

    from .clock import weather_of  # 局部导入避免循环依赖

    return GatherReceipt(
        node_id=node.id,
        label=node.label,
        action=it.action,
        animation=ANIMATIONS[it.action],
        material=node.material,
        qty=qty,
        minutes=minutes,
        bag_after=new_bag,
        clock_after=clock.advanced(minutes),
        weather=weather_of(clock, owner, theme).id,
        note=f"{ACTION_LABELS[it.action]} {node.label}：{node.material} +{qty}"
             f"（耗时 {minutes} 分钟）",
    )


#: 单点每日采集上限
DAILY_NODE_LIMIT = 8


def gatherable_rows(theme: str, player_tile: tuple[int, int] | None = None
                    ) -> list[dict[str, object]]:
    """采集面板/小地图用的一览（可交互物 + 是否在范围内）。"""
    rows = []
    for it in interactables_of(theme):
        dist = None if player_tile is None else manhattan(it.tile, player_tile)
        rows.append(
            {
                "id": it.id,
                "label": it.node.label,
                "action": it.action,
                "action_label": ACTION_LABELS[it.action],
                "animation": ANIMATIONS[it.action],
                "material": it.node.material,
                "qty_range": list(it.node.qty),
                "minutes": it.node.minutes,
                "tile": list(it.tile),
                "distance": dist,
                "in_range": dist is not None and dist <= INTERACT_RANGE_TILES,
            }
        )
    return rows


def prompt_grammar_check() -> list[str]:
    """自检：每个动作都要有动画键与中文标签（缺一即前端会静默 fallback → 提前暴露）。"""
    missing = []
    for action in set(ANIMATIONS):
        if action not in ACTION_LABELS:
            missing.append(action)
        if action not in ANIMATIONS:
            missing.append(action)
    return missing
