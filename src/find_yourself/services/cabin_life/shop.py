"""B8 · 经营系统（单机，纯逻辑层）。

说明书 §5-B8：玩家自选经营方向 → 进货/生产 → 定价 → 升级店铺 → 解锁新品；
金币 + 物价波动 + 村民喜好影响销量 + 日结算；**验收：日结算数值可复算；物价波动可配置**。

可复算的关键（全部显式公式，不用隐藏随机）：
    * 进价 `buy_price = round(base_cost × (1 + fluct))`，`fluct ∈ [-0.3, +0.3]`，
      由 (owner, good, day) 确定性导出 → 同一天同一货品价格恒定，存档读回不漂移；
    * 销量 `sold = min(stock, round(demand × taste × demand_pct × price_factor))`；
      `price_factor` 由「定价相对均价」决定（便宜卖得多、贵卖得少，非线性）；
    * 日结算 `revenue - cost = profit`，每一项都出现在回执里，玩家能自己加一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .clock import GameClock, demand_pct, weather_of
from .rng import clamp, rng_for
from .themes import get_theme

#: 经营方向（玩家自选）
SHOP_KINDS: tuple[str, ...] = ("grocery", "handcraft", "tavern", "farm", "teahouse")
SHOP_KIND_LABELS: dict[str, str] = {
    "grocery": "杂货铺",
    "handcraft": "手作铺",
    "tavern": "小餐馆",
    "farm": "农场",
    "teahouse": "茶馆",
}

#: 店铺等级 → 解锁新品数量
MAX_SHOP_LEVEL = 5
LEVEL_UNLOCK_COUNT: dict[int, int] = {1: 4, 2: 6, 3: 8, 4: 10, 5: 12}

#: 物价波动幅度（可配置：改这里即改全局波动强度）
FLUCTUATION_PCT = 30
#: 基准日客流（可复算常量：销量 = f(客流, 天气, 喜好, 定价)）
BASE_DEMAND = 12


@dataclass(frozen=True)
class Good:
    id: str
    label: str
    kind: str
    base_cost: int
    base_price: int
    #: 村民喜好加成（+1 = 更好卖，-1 = 更难卖），由各主题 favorite_goods 决定
    tags: tuple[str, ...] = ()


_GOODS: tuple[Good, ...] = (
    # 杂货
    Good("mushroom", "蘑菇", "grocery", 4, 9),
    Good("wood", "木材", "grocery", 6, 13),
    Good("herb", "草药", "grocery", 7, 15),
    Good("fish", "鱼", "grocery", 8, 17),
    Good("vegetable", "蔬菜", "grocery", 5, 11),
    Good("honey", "蜂蜜", "grocery", 10, 21),
    Good("bread", "面包", "grocery", 9, 19),
    Good("jam", "果酱", "grocery", 12, 25),
    # 手作
    Good("handicraft", "手工品", "handcraft", 18, 38),
    Good("incense", "线香", "handcraft", 14, 30),
    Good("tech_gear", "科技装备", "handcraft", 40, 82),
    Good("magic_scroll", "魔法卷轴", "handcraft", 45, 95),
    # 餐馆
    Good("soup", "热汤", "tavern", 16, 33),
    Good("grill", "烤肉", "tavern", 22, 46),
    Good("tea", "茶", "tavern", 10, 22),
    Good("wine", "酒", "tavern", 20, 44),
    # 农场
    Good("wheat", "麦穗", "farm", 5, 12),
    Good("pumpkin", "南瓜", "farm", 7, 16),
    Good("fruit", "果子", "farm", 8, 18),
    Good("peach_blossom", "桃花", "farm", 11, 25),
    # 茶馆
    Good("pastry", "糕点", "teahouse", 13, 28),
    Good("tea_leaf", "茶叶", "teahouse", 15, 32),
    Good("cotton", "棉布", "teahouse", 11, 24),
)

GOODS: dict[str, Good] = {g.id: g for g in _GOODS}


def get_good(good_id: str) -> Good:
    try:
        return GOODS[good_id]
    except KeyError as exc:  # pragma: no cover
        raise KeyError(f"unknown good {good_id!r}; known={sorted(GOODS)}") from exc


def goods_for_kind(kind: str) -> tuple[Good, ...]:
    if kind not in SHOP_KINDS:
        raise KeyError(f"unknown shop kind {kind!r}; known={list(SHOP_KINDS)}")
    return tuple(g for g in _GOODS if g.kind == kind)


def unlocked_goods(kind: str, level: int) -> tuple[Good, ...]:
    """等级解锁新品：按 base_price 升序取前 N 件（贵的后解锁，进度可见）。"""
    lv = clamp(level, 1, MAX_SHOP_LEVEL)
    cap = LEVEL_UNLOCK_COUNT[lv]
    return tuple(sorted(goods_for_kind(kind), key=lambda g: g.base_price)[:cap])


def next_unlock(kind: str, level: int) -> Good | None:
    """下一级能解锁的新品（已满级返回 None → UI 如实显示「已全解锁」）。"""
    if level >= MAX_SHOP_LEVEL:
        return None
    have = {g.id for g in unlocked_goods(kind, level)}
    pool = sorted(goods_for_kind(kind), key=lambda g: g.base_price)
    for g in pool:
        if g.id not in have:
            return g
    return None


# ----------------------------------------------------------------------
# 物价
# ----------------------------------------------------------------------

def fluctuation_pct(owner: str, good_id: str, day: int) -> int:
    """确定性物价波动（百分比，范围 ±FLUCTUATION_PCT）。"""
    if day < 1:
        raise ValueError(f"day must be >= 1, got {day}")
    get_good(good_id)  # 未知货品直接报错
    span = FLUCTUATION_PCT * 2 + 1
    return rng_for("price", owner, good_id, day).randrange(span) - FLUCTUATION_PCT


def buy_price(owner: str, good_id: str, day: int) -> int:
    """今日进价（至少 1，波动后为 0 会导致免费进货，故夹到 1）。"""
    g = get_good(good_id)
    pct = fluctuation_pct(owner, good_id, day)
    return max(1, (g.base_cost * (100 + pct) + 50) // 100)


def sell_price(owner: str, good_id: str, day: int) -> int:
    """今日建议售价（均价 × 波动，与进价同源同波动方向 → 保证有利润空间）。"""
    g = get_good(good_id)
    pct = fluctuation_pct(owner, good_id, day)
    return max(1, (g.base_price * (100 + pct) + 50) // 100)


def price_factor(owner: str, good_id: str, day: int, ask_price: int) -> float:
    """定价系数：相对建议价越便宜卖得越多（0.5~1.25，非线性）。"""
    fair = sell_price(owner, good_id, day)
    if fair <= 0:  # pragma: no cover - sell_price 已夹到 ≥1
        return 1.0
    ratio = ask_price / fair
    if ratio <= 0.7:
        return 1.25
    if ratio <= 1.0:
        return 1.10
    if ratio <= 1.3:
        return 0.85
    return 0.55


def taste_factor(theme: str, good_id: str) -> float:
    """村民喜好：主题 favorite_goods 命中 → 1.25，否则 1.0。"""
    return 1.25 if good_id in get_theme(theme).favorite_goods else 1.0


# ----------------------------------------------------------------------
# 进货 / 售出
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class TradeReceipt:
    good_id: str
    unit_buy: int
    qty: int
    cost: int
    coins_after: int
    stock_after: int


def restock(
    owner: str, good_id: str, day: int, qty: int, *, coins: int, stock: int
) -> TradeReceipt:
    """进货：金币不足直接抛 ValueError（调用方转 422），不静默扣成 0。"""
    if qty <= 0:
        raise ValueError("qty must be > 0")
    unit = buy_price(owner, good_id, day)
    cost = unit * qty
    if cost > coins:
        raise ValueError(f"金币不足：需要 {cost}（{unit}×{qty}），只有 {coins}")
    return TradeReceipt(good_id, unit, qty, cost, coins - cost, stock + qty)


@dataclass(frozen=True)
class SellReceipt:
    good_id: str
    unit_price: int
    sold: int
    kept: int
    revenue: int
    coins_after: int
    stock_after: int
    note: str


def sell_day(
    owner: str,
    good_id: str,
    clock: GameClock,
    theme: str,
    ask_price: int,
    *,
    stock: int,
    coins: int,
) -> SellReceipt:
    """日结算售出：销量由客源/喜好/定价决定，未售出部分**如实**留在库存。"""
    if ask_price <= 0:
        raise ValueError("ask_price must be > 0")
    if stock <= 0:
        return SellReceipt(good_id, ask_price, 0, 0, 0, coins, 0, "库存为 0，今天没卖出去。")
    base_demand = BASE_DEMAND  # 基准客流（常量，玩家可自己复算）
    dp = demand_pct(clock, owner, theme)
    weather_mult = weather_of(clock, owner, theme).demand_pct / 100
    tf = taste_factor(theme, good_id)
    pf = price_factor(owner, good_id, clock.day, ask_price)
    night_mult = 0.6 if clock.is_night else 1.0
    raw = base_demand * dp / 100 * weather_mult * tf * pf * night_mult
    sold = min(stock, max(0, int(raw + 0.5)))
    revenue = sold * ask_price
    kept = stock - sold
    note = f"售出 {sold} / 库存 {stock}"
    if kept:
        note += f"，{kept} 件没卖掉，留在仓库"
    return SellReceipt(good_id, ask_price, sold, kept, revenue, coins + revenue, kept, note)


# ----------------------------------------------------------------------
# 店铺状态
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ShopState:
    kind: str
    level: int
    stock: dict[str, int]
    ask_prices: dict[str, int]

    def stock_of(self, good_id: str) -> int:
        return int(self.stock.get(good_id, 0) or 0)

    def ask_of(self, good_id: str, owner: str, day: int) -> int:
        return int(self.ask_prices.get(good_id) or sell_price(owner, good_id, day))


def new_shop(kind: str, owner: str, day: int) -> ShopState:
    """开新店：Lv1 解锁品全部备货 0，定价取今日建议价。"""
    goods = unlocked_goods(kind, 1)
    return ShopState(
        kind=kind,
        level=1,
        stock={g.id: 0 for g in goods},
        ask_prices={g.id: sell_price(owner, g.id, day) for g in goods},
    )


UPGRADE_COST: dict[int, int] = {1: 300, 2: 900, 3: 2400, 4: 6000}


def upgrade_cost(level: int) -> int:
    """升到 level+1 的金币花费；已满级抛 ValueError（不返回 0 假装免费）。"""
    if level >= MAX_SHOP_LEVEL:
        raise ValueError(f"已是最高等级 Lv{MAX_SHOP_LEVEL}")
    return UPGRADE_COST[level]


def can_upgrade(shop: ShopState, coins: int) -> tuple[bool, str]:
    """能否升级：返回 (bool, 原因)。金币不足时**如实**说明差多少。"""
    if shop.level >= MAX_SHOP_LEVEL:
        return False, f"已是最高等级 Lv{MAX_SHOP_LEVEL}"
    need = upgrade_cost(shop.level)
    if coins < need:
        return False, f"金币不足：升级需要 {need}，还差 {need - coins}"
    return True, f"花 {need} 金币升到 Lv{shop.level + 1}"


def upgrade(shop: ShopState, coins: int) -> tuple[ShopState, int, str]:
    ok, reason = can_upgrade(shop, coins)
    if not ok:
        raise ValueError(reason)
    need = upgrade_cost(shop.level)
    nxt = shop.level + 1
    goods = unlocked_goods(shop.kind, nxt)
    known = set(shop.stock) | {g.id for g in goods}
    stock = {gid: shop.stock_of(gid) for gid in sorted(known)}
    return replace(shop, level=nxt, stock=stock), coins - need, reason


def set_ask_price(shop: ShopState, good_id: str, price: int) -> ShopState:
    """改定价。价格必须为正；不允许卖到 0 价白送（那不是经营）。"""
    if price <= 0:
        raise ValueError("price must be > 0")
    get_good(good_id)
    return replace(shop, ask_prices={**shop.ask_prices, good_id: price})


def shop_rows(owner: str, shop: ShopState, day: int, theme: str) -> list[dict[str, object]]:
    """经营面板的一览数据（含进价/建议价/库存/喜好/是否解锁）。"""
    unlocked = {g.id for g in unlocked_goods(shop.kind, shop.level)}
    rows = []
    for g in sorted(GOODS.values(), key=lambda x: (x.kind, x.base_price)):
        if g.kind != shop.kind:
            continue
        rows.append(
            {
                "id": g.id,
                "label": g.label,
                "unlocked": g.id in unlocked,
                "buy_price": buy_price(owner, g.id, day),
                "fair_price": sell_price(owner, g.id, day),
                "ask_price": shop.ask_of(g.id, owner, day),
                "stock": shop.stock_of(g.id),
                "taste": taste_factor(theme, g.id),
            }
        )
    return rows


def daily_settlement(
    owner: str,
    shop: ShopState,
    clock: GameClock,
    theme: str,
    *,
    coins: int,
    next_clock: GameClock,
) -> dict[str, object]:
    """睡前进 day's结算：逐货品卖出 → 汇总利润 → 返回可复算明细。

    返回结构里的 `lines[]` 每一项都带 `unit_price / sold / revenue`，
    玩家（或测试）可以逐项把 `revenue` 加起来对 `total_revenue`。
    """
    lines: list[dict[str, object]] = []
    stock = dict(shop.stock)
    total_revenue = 0
    total_cost_value = 0
    for good_id in sorted(stock):
        qty = int(stock.get(good_id, 0) or 0)
        if qty <= 0:
            continue
        receipt = sell_day(
            owner, good_id, clock, theme, shop.ask_of(good_id, owner, clock.day),
            stock=qty, coins=0,
        )
        stock[good_id] = receipt.stock_after
        total_revenue += receipt.revenue
        total_cost_value += buy_price(owner, good_id, clock.day) * receipt.sold
        lines.append(
            {
                "good_id": good_id,
                "label": get_good(good_id).label,
                "unit_price": receipt.unit_price,
                "sold": receipt.sold,
                "revenue": receipt.revenue,
                "leftover": receipt.stock_after,
            }
        )
    profit = total_revenue - total_cost_value
    new_state = replace(shop, stock=stock)
    return {
        "day": clock.day,
        "weather": weather_of(clock, owner, theme).id,
        "theme": theme,
        "lines": lines,
        "total_revenue": total_revenue,
        "cost_of_goods_sold": total_cost_value,
        "profit": profit,
        "coins_after": coins + profit,
        "shop": new_state,
        "next_day": next_clock.day,
    }


def restock_all(
    owner: str,
    shop: ShopState,
    day: int,
    order: Iterable[str],
    qty_each: int,
    *,
    coins: int,
) -> tuple[ShopState, int, list[dict[str, object]]]:
    """批量进货：逐项扣款，任一项金币不足即整体失败（返回前不落库，调用方不提交）。"""
    if qty_each <= 0:
        raise ValueError("qty_each must be > 0")
    total = 0
    plan: list[dict[str, object]] = []
    for gid in order:
        unit = buy_price(owner, gid, day)
        total += unit * qty_each
        plan.append({"id": gid, "unit": unit, "qty": qty_each, "cost": unit * qty_each})
    if total > coins:
        raise ValueError(f"金币不足：整单需要 {total}，只有 {coins}（未扣款）")
    stock = dict(shop.stock)
    for item in plan:
        gid = str(item["id"])
        stock[gid] = stock.get(gid, 0) + int(item["qty"])
    return replace(shop, stock=stock), coins - total, plan