"""B10 · 大世界（**数据层**：地形生成 / 秘密区域 / 隐藏宝箱 / 随机事件 / 寻路）。

说明书 §5-B10 要求：
    * 单图 ≥ **100×100 格**无缝滚动；镜头跟随（角色居中、平滑）；
    * **隐藏宝箱 / 秘密区域 / 随机事件**。

本切片只做**规则与数据**，不碰渲染：
    * A1 已冻结 `cabinConfig.ts` 常量（`VIRTUAL_W=640` / `VIRTUAL_H=360` /
      `TILE=32` / `WORLD.width=5120`），本模块**只引用不修改**：
      `MAP_COLS = WORLD.width // TILE = 160`，横向与 A1 严格对齐；
      纵向取 100 格 → 地图 160×100 = **16000 格**（≥ 规范下限 100×100）。
    * 渲染段（镜头跟随 / 无缝滚动 / 瓦片绘制）要改 `cabinScene.ts`，
      按 §2.5 串行点 **A5 收工后再做**，故本模块不 import 任何前端符号。
    * 零 IO、零时钟、零全局随机；一切随机走 `rng.rng_for(owner, ...)`。

诚实原则（§2-1）
----------------
    * 地形 / 宝箱位置由 `rng_for(owner, theme, ...)` 派生 —— 同一存档读回
      世界**不漂移**；换 owner 会得到另一片大陆（每人一块自己的地）。
    * 地形 id / 宝箱 id / 事件 id 未知一律**抛错**，不静默回落。
    * 寻路在「无路可达」时返回 `None` 并附带原因，不谎称可达。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .rng import clamp, pick, weighted_pick, rng_for
from .themes import THEME_IDS, get_theme

# ---------------------------------------------------------------------- #
# 与 A1 冻结常量对齐的地图尺寸（**引用**，不是重新定义）               #
# ---------------------------------------------------------------------- #

#: 一格边长（虚拟像素）。对应 A1 `cabinConfig.ts` 的 `TILE = 32`。
TILE = 32

#: 横向格数 = A1 `WORLD.width`（5120 虚拟像素）÷ TILE(32) = 160。**不改 A1 数值**。
MAP_COLS = 5120 // TILE

#: 纵向格数。规范下限为 100，取 100（3200 虚拟像素高）。
MAP_ROWS = 100

#: 世界尺寸（虚拟像素），供渲染段直接消费。
MAP_WIDTH_PX = MAP_COLS * TILE
MAP_HEIGHT_PX = MAP_ROWS * TILE

#: 出生点（格坐标）。与 A1 `WORLD.spawnX = 2208` 虚拟像素对齐：2208 / 32 = 69。
SPAWN_TILE: tuple[int, int] = (69, MAP_ROWS // 2)

# ---------------------------------------------------------------------- #
# 地形                                                                      #
# ---------------------------------------------------------------------- #

#: 地形 id → 中文标签（前端 tooltip / 调试面板直接用，不再二次翻译）
TERRAIN_LABELS: dict[str, str] = {
    "grass": "草地",
    "flower": "花地",
    "path": "石板路",
    "sand": "浅滩",
    "water": "溪水",
    "deep": "深水",
    "tree": "密林",
    "rock": "乱石",
    "cliff": "崖壁",
    "ruin": "旧遗迹",
    "moss": "苔地",
    "fog": "迷雾",
}


@dataclass(frozen=True)
class TerrainDef:
    id: str
    label: str
    walkable: bool
    gatherable: bool
    #: 移动消耗（1 = 基准）。不可通行时该值无意义，恒为 0。
    move_cost: int


_T = TerrainDef

#: 地形表。**不可通行**的只有 deep / cliff / tree / rock 四种：
#: 其余（含 ruin、fog）都可走，保证 100×100 图不会生成「孤岛死区」。
TERRAIN: dict[str, TerrainDef] = {
    t.id: t
    for t in (
        _T("grass", "草地", True, True, 1),
        _T("flower", "花地", True, True, 1),
        _T("moss", "苔地", True, True, 1),
        _T("fog", "迷雾", True, True, 1),
        _T("ruin", "旧遗迹", True, True, 1),
        _T("sand", "浅滩", True, True, 1),
        _T("path", "石板路", True, False, 1),
        _T("water", "溪水", True, False, 2),
        _T("tree", "密林", False, True, 0),
        _T("rock", "乱石", False, True, 0),
        _T("cliff", "崖壁", False, False, 0),
        _T("deep", "深水", False, False, 0),
    )
}


def terrain(id: str) -> TerrainDef:
    """按 id 取地形定义；未知 id 直接抛错（不静默回落成草地）。"""
    try:
        return TERRAIN[id]
    except KeyError:
        raise KeyError(f"未知地形 id: {id!r}；可用: {sorted(TERRAIN)}") from None


def walkable(tile: tuple[int, int], grid: "WorldMap") -> bool:
    return terrain(grid.at(tile)).walkable


# ---------------------------------------------------------------------- #
# 主题地形配方                                                              #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class TerrainRecipe:
    """一个主题的地图配方：底色 + 装饰密度 + 水系强度。"""

    theme: str
    base: str
    #: 装饰地形及密度权重（面积占比按 100 份抽）
    scatter: tuple[tuple[str, int], ...]
    #: 水系：河道占整图面积百分比（0 表示该主题无河）
    river_pct: int
    #: 雾区面积百分比（水汽重的地形才有）
    fog_pct: int
    #: 遗迹数量（旧文明遗留点，也作秘密区域锚点）
    ruins: int


RECIPES: dict[str, TerrainRecipe] = {
    "forest": TerrainRecipe("forest", "moss", (("tree", 22), ("grass", 26), ("rock", 4)), 6, 0, 3),
    "garden": TerrainRecipe("garden", "grass", (("flower", 30), ("grass", 24), ("tree", 5)), 4, 0, 2),
    "stream": TerrainRecipe("stream", "grass", (("rock", 8), ("sand", 20), ("grass", 26)), 14, 2, 2),
    "field": TerrainRecipe("field", "grass", (("flower", 22), ("grass", 30), ("rock", 2)), 3, 0, 1),
    "planet": TerrainRecipe("planet", "sand", (("rock", 14), ("sand", 26), ("grass", 10)), 2, 4, 4),
    "magic": TerrainRecipe("magic", "flower", (("flower", 20), ("moss", 18), ("ruin", 4), ("rock", 4)), 5, 6, 6),
    "scifi": TerrainRecipe("scifi", "path", (("path", 20), ("rock", 9), ("sand", 14), ("grass", 12)), 2, 8, 5),
    "country": TerrainRecipe("country", "grass", (("grass", 28), ("flower", 14), ("tree", 8), ("sand", 4)), 6, 0, 3),
    "ink": TerrainRecipe("ink", "fog", (("fog", 24), ("moss", 16), ("rock", 8), ("tree", 8)), 8, 14, 7),
}

assert set(RECIPES) == set(THEME_IDS), "地形配方必须覆盖全部主题"

#: 基底必须可通行——否则整片图会被不可行地形铺满、flood-fill 只能到出生点。
assert all(TERRAIN[t.base].walkable for t in RECIPES.values()), "基底地形必须可通行"


def recipe_of(theme: str) -> TerrainRecipe:
    get_theme(theme)  # 未知主题报错
    return RECIPES[theme]


# ---------------------------------------------------------------------- #
# 地图                                                                      #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorldMap:
    """一张已生成的地图。`tiles` 为行优先的 `MAP_ROWS × MAP_COLS` 地形 id 元组。"""

    theme: str
    owner: str
    cols: int
    rows: int
    tiles: tuple[str, ...]
    #: 秘密区域（每个都藏宝箱），id 形如 `magic_secret_3`
    secrets: tuple["SecretArea", ...] = ()
    #: 隐藏宝箱（id 形如 `magic_chest_t2_0`）
    chests: tuple["Chest", ...] = ()
    spawn: tuple[int, int] = SPAWN_TILE

    # -- 取值 -----------------------------------------------------------
    def in_bounds(self, tile: tuple[int, int]) -> bool:
        x, y = tile
        return 0 <= x < self.cols and 0 <= y < self.rows

    def at(self, tile: tuple[int, int]) -> str:
        """取该格地形。**越界抛错**——不静默返回草地，那会让「走出地图」
        看起来像「走到草地」。"""
        x, y = tile
        if not self.in_bounds(tile):
            raise IndexError(f"格坐标越界: {tile}（地图 {self.cols}×{self.rows}）")
        return self.tiles[y * self.cols + x]

    def is_walkable(self, tile: tuple[int, int]) -> bool:
        return self.in_bounds(tile) and terrain(self.at(tile)).walkable

    def row_of(self, y: int) -> tuple[str, ...]:
        if not 0 <= y < self.rows:
            raise IndexError(f"行越界: {y}（0..{self.rows - 1}）")
        return self.tiles[y * self.cols : (y + 1) * self.cols]

    # -- 统计（供测试与 HUD「探索度」） --------------------------------
    def histogram(self) -> dict[str, int]:
        counts: dict[str, int] = {tid: 0 for tid in TERRAIN}
        for tid in self.tiles:
            counts[tid] += 1
        return counts

    def walkable_count(self) -> int:
        return sum(1 for tid in self.tiles if terrain(tid).walkable)

    def walkable_ratio(self) -> float:
        return self.walkable_count() / (self.cols * self.rows)

    def connected_from_spawn(self) -> frozenset[tuple[int, int]]:
        """从出生点 flood-fill 出的可达格集合（供「无孤岛」自检）。"""
        seen: set[tuple[int, int]] = set()
        if not self.is_walkable(self.spawn):
            return frozenset()
        stack = [self.spawn]
        seen.add(self.spawn)
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (x + dx, y + dy)
                if nb in seen or not self.is_walkable(nb):
                    continue
                seen.add(nb)
                stack.append(nb)
        return frozenset(seen)


def generate_map(owner: str, theme: str, *, cols: int = MAP_COLS, rows: int = MAP_ROWS) -> WorldMap:
    """确定性生成 `cols × rows` 地图。同 (owner, theme, 尺寸) → 同一片大陆。

    生成顺序（顺序即确定性契约，改动会让老存档的地形漂移）：
        1. 全部铺底色；
        2. 撒装饰（按配方权重）；
        3. 挖河道（一条贯穿的蜿蜒带，顺手清出通路，避免断头河）；
        4. 撒雾区；
        5. 放遗迹（避开出生点）；
        6. 打通出生点周围 3×3；
        7. 埋秘密区域与宝箱。
    """
    if cols < 8 or rows < 8:
        raise ValueError(f"地图至少 8×8，收到 {cols}×{rows}")
    r = recipe_of(theme)
    total = cols * rows
    grid = [r.base] * total
    rng = rng_for(owner, theme, "map", cols, rows)

    def put(x: int, y: int, tid: str) -> None:
        if 0 <= x < cols and 0 <= y < rows:
            grid[y * cols + x] = tid

    # 1) 底色已铺好
    # 2) 装饰：整图按权重抽若干比例的格
    scatter_pool = {tid: w for tid, w in r.scatter}
    for _ in range(total // 8):
        x = rng.randrange(cols)
        y = rng.randrange(rows)
        put(x, y, weighted_pick(rng, scatter_pool))
        # 让装饰成团（4 邻域各掷一次），避免「均匀噪点」观感
        if rng.random() < 0.65:
            dx, dy = pick(rng, ((1, 0), (-1, 0), (0, 1), (0, -1)))
            put(x + dx, y + dy, weighted_pick(rng, scatter_pool))

    # 3) 河道：一条自上而下的蜿蜒水带，宽度随 y 抖动
    if r.river_pct > 0:
        width = max(1, cols * r.river_pct // 100)
        x = rng.randrange(width, cols - width)
        for y in range(rows):
            x = clamp(x + rng.choice((-1, 0, 0, 1)), 0, cols - 1)
            w = width + rng.choice((-1, 0, 0, 1))
            for i in range(w):
                put(x + i, y, "deep" if i >= max(1, w - 1) else "water")
            # 每 6 行在岸边清出一格通路，防止河道把图切成两半
            if y % 6 == 0:
                put(x - 2, y, "sand")

    # 4) 雾区
    if r.fog_pct > 0:
        n = total * r.fog_pct // 100
        for _ in range(n):
            put(rng.randrange(cols), rng.randrange(rows), "fog")

    # 5) 遗迹
    ruins = []
    for i in range(r.ruins):
        for _ in range(40):  # 最多重掷 40 次找一块远离出生点的位置
            x = rng.randrange(4, cols - 4)
            y = rng.randrange(4, rows - 4)
            if abs(x - SPAWN_TILE[0]) + abs(y - SPAWN_TILE[1]) < 12:
                continue
            if any(abs(x - rx) + abs(y - ry) < 10 for rx, ry in ruins):
                continue
            ruins.append((x, y))
            break
    for x, y in ruins:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                put(x + dx, y + dy, "ruin")

    # 6) 出生点清空：出生点及其 3×3 一律草地，且永远可走
    sx, sy = SPAWN_TILE if (cols, rows) == (MAP_COLS, MAP_ROWS) else (cols // 2, rows // 2)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            put(sx + dx, sy + dy, "grass")

    wm = WorldMap(theme=theme, owner=owner, cols=cols, rows=rows,
                  tiles=tuple(grid), spawn=(sx, sy))
    secrets, chests = place_secrets(wm)
    return WorldMap(theme=wm.theme, owner=wm.owner, cols=wm.cols, rows=wm.rows,
                    tiles=wm.tiles, secrets=secrets, chests=chests, spawn=wm.spawn)


# ---------------------------------------------------------------------- #
# 秘密区域 + 隐藏宝箱                                                       #
# ---------------------------------------------------------------------- #

#: 宝箱档位：t1 无门槛 / t2 需钥匙 / t3 需工具
CHEST_TIERS: tuple[str, ...] = ("t1", "t2", "t3")

CHEST_TIER_RULES: dict[str, dict[str, object]] = {
    "t1": {"label": "旧木箱", "needs": None, "coins": (30, 80), "materials": 1},
    "t2": {"label": "铜锁箱", "needs": "key", "coins": (90, 200), "materials": 2},
    "t3": {"label": "封蜡箱", "needs": "tool", "coins": (240, 480), "materials": 3},
}

#: 每主题每档宝箱数量。
CHESTS_PER_TIER: dict[str, tuple[int, int, int]] = {
    # theme:        t1  t2  t3
    "forest": (4, 3, 1),
    "garden": (4, 3, 1),
    "stream": (4, 3, 1),
    "field": (5, 3, 1),
    "planet": (4, 4, 2),
    "magic": (4, 4, 2),
    "scifi": (4, 4, 2),
    "country": (5, 3, 1),
    "ink": (3, 4, 3),
}

#: 秘密区域数量（每主题）。区域是「宝箱 + 一小片特殊地形的标记」。
SECRETS_PER_THEME: int = 6


@dataclass(frozen=True)
class SecretArea:
    """秘密区域：玩家走近才发现（`hint` 只给方向不给坐标）。"""

    id: str
    theme: str
    tile: tuple[int, int]
    radius: int
    label: str
    #: 区域内被改写的地形（区域专属观感）
    terrain_id: str


@dataclass(frozen=True)
class Chest:
    """隐藏宝箱。`found` 属于玩家进度，不在地图里（由存档持有）。"""

    id: str
    theme: str
    tier: str
    tile: tuple[int, int]
    secret_id: str
    label: str
    needs: str | None
    coins: tuple[int, int]
    materials: int


#: 秘密区域的主题专属地形（水/岩/雾，随主题气质走）
SECRET_TERRAIN: dict[str, str] = {
    "forest": "moss",
    "garden": "flower",
    "stream": "sand",
    "field": "flower",
    "planet": "rock",
    "magic": "flower",
    "scifi": "path",
    "country": "grass",
    "ink": "fog",
}

SECRET_LABELS: tuple[str, ...] = (
    "被藤蔓遮住的角落",
    "塌了半边的石屋",
    "没人来过的野台",
    "水底发亮的东西",
    "老槐树下的洞",
    "写着奇怪符号的石板",
)


def place_secrets(wm: WorldMap) -> tuple[tuple[SecretArea, ...], tuple[Chest, ...]]:
    """在**可达**格上埋秘密区域与宝箱。同 (owner, theme, 尺寸) → 同一埋点。"""
    r = rng_for(wm.owner, wm.theme, "secrets", wm.cols, wm.rows)
    reach = wm.connected_from_spawn()
    if not reach:
        return (), ()  # 出生点被堵死（不该发生）→ 诚实返回空，不硬塞
    pool = sorted(reach)

    secrets: list[SecretArea] = []
    chests: list[Chest] = []
    used: set[tuple[int, int]] = set()

    for i in range(SECRETS_PER_THEME):
        tile = _spread_pick(r, pool, used, min_sep=14)
        if tile is None:
            break
        used.add(tile)
        area = SecretArea(
            id=f"{wm.theme}_secret_{i}",
            theme=wm.theme,
            tile=tile,
            radius=2 + i % 3,
            label=SECRET_LABELS[i % len(SECRET_LABELS)],
            terrain_id=SECRET_TERRAIN[wm.theme],
        )
        secrets.append(area)

    for ti, tier in enumerate(CHEST_TIERS):
        for i in range(CHESTS_PER_TIER[wm.theme][ti]):
            area = secrets[i % len(secrets)] if secrets else None
            tile = _spread_pick(r, pool, used, min_sep=6)
            if tile is None:
                break
            used.add(tile)
            rule = CHEST_TIER_RULES[tier]
            chests.append(
                Chest(
                    id=f"{wm.theme}_chest_{tier}_{i}",
                    theme=wm.theme,
                    tier=tier,
                    tile=tile,
                    secret_id=area.id if area else "",
                    label=str(rule["label"]),
                    needs=rule["needs"],  # type: ignore[arg-type]
                    coins=rule["coins"],  # type: ignore[arg-type]
                    materials=int(rule["materials"]),  # type: ignore[arg-type]
                )
            )

    return tuple(sorted(secrets, key=lambda s: s.id)), tuple(sorted(chests, key=lambda c: c.id))


def _spread_pick(rng, pool: Sequence[tuple[int, int]], used: set[tuple[int, int]],
                 *, min_sep: int) -> tuple[int, int] | None:
    """从候选格里挑一个离已用点足够远的；最多试 60 次，失败返回 None。"""
    for _ in range(60):
        tile = pool[rng.randrange(len(pool))]
        if any(abs(tile[0] - u[0]) + abs(tile[1] - u[1]) < min_sep for u in used):
            continue
        return tile
    return None


def secrets_within(wm: WorldMap, tile: tuple[int, int]) -> list[SecretArea]:
    """玩家所在格附近能看到线索的秘密区域（曼哈顿距离 ≤ radius）。"""
    return [s for s in wm.secrets
            if abs(s.tile[0] - tile[0]) + abs(s.tile[1] - tile[1]) <= s.radius]


def chests_at(wm: WorldMap, tile: tuple[int, int]) -> list[Chest]:
    return [c for c in wm.chests if c.tile == tile]


def open_chest(chest: Chest, rng, *, bag_has_key: bool, bag_has_tool: bool) -> dict[str, object]:
    """开箱 → 战利品。缺钥匙/工具**如实拒绝**，不给「假成功」的空箱。"""
    if chest.needs == "key" and not bag_has_key:
        return {"ok": False, "reason": "需要一把钥匙", "chest_id": chest.id}
    if chest.needs == "tool" and not bag_has_tool:
        return {"ok": False, "reason": "需要能撬开封蜡的工具", "chest_id": chest.id}
    lo, hi = chest.coins
    coins = lo + rng.randrange(hi - lo + 1)
    return {
        "ok": True,
        "chest_id": chest.id,
        "coins": coins,
        "materials": chest.materials,
        "tier": chest.tier,
    }


def chest_hint(wm: WorldMap, player: tuple[int, int], *, range_tiles: int = 6) -> str:
    """未发现宝箱时的方向提示——**只给方向，不泄露精确坐标**（保持「隐藏」）。"""
    near = [c for c in wm.chests
            if abs(c.tile[0] - player[0]) + abs(c.tile[1] - player[1]) <= range_tiles]
    if not near:
        return ""
    best = min(near, key=lambda c: abs(c.tile[0] - player[0]) + abs(c.tile[1] - player[1]))
    dx = best.tile[0] - player[0]
    dy = best.tile[1] - player[1]
    vert = "北" if dy < 0 else "南"
    horz = "西" if dx < 0 else "东"
    if abs(dy) >= abs(dx):
        return f"土里好像有东西，往{vert}边走走"
    return f"那边草丛在动，往{horz}边走走"


# ---------------------------------------------------------------------- #
# 随机事件（走到未探索格时 roll）                                          #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class EventDef:
    id: str
    label: str
    #: 权重（相对频率）
    weight: int
    #: 是否给材料（用于「惊喜感」统计）
    gives_material: bool
    #: 是否可能是空手而归（诚实态，必须存在）
    empty: bool


EVENTS: tuple[EventDef, ...] = (
    EventDef("small_find", "小发现", 26, True, False),
    EventDef("stray_pet", "小东西跟着你", 14, False, False),
    EventDef("inscription", "石板上的字", 10, True, False),
    EventDef("old_cache", "旧行囊", 8, True, False),
    EventDef("night_light", "夜里的光", 7, True, False),
    EventDef("market_rumor", "路人的闲话", 9, False, False),
    EventDef("rain_pool", "雨后的水洼", 8, True, False),
    EventDef("nest", "草丛里的窝", 7, False, False),
    EventDef("faded_photo", "褪色的照片", 5, True, False),
    EventDef("quiet", "什么都没发生", 16, False, True),
    EventDef("nothing", "一无所获", 12, False, True),
)

EVENT_IDS: tuple[str, ...] = tuple(e.id for e in EVENTS)
EVENT_WEIGHTS: dict[str, int] = {e.id: e.weight for e in EVENTS}
EMPTY_EVENT_IDS: tuple[str, ...] = tuple(e.id for e in EVENTS if e.empty)


def event_of(id: str) -> EventDef:
    for e in EVENTS:
        if e.id == id:
            return e
    raise KeyError(f"未知事件 id: {id!r}；可用: {list(EVENT_IDS)}")


def roll_event(owner: str, theme: str, day: int, tile: tuple[int, int]) -> EventDef:
    """在格 `tile` roll 一个事件。同参数 → 同结果（存档可复算）。"""
    r = rng_for(owner, theme, "event", day, tile[0], tile[1])
    return event_of(weighted_pick(r, EVENT_WEIGHTS))


def event_pct() -> int:
    """空手而归（诚实态）在全部事件中的百分比，供测试钉死。"""
    total = sum(EVENT_WEIGHTS.values())
    return round(sum(EVENT_WEIGHTS[e] for e in EMPTY_EVENT_IDS) * 100 / total)


@dataclass(frozen=True)
class EventOutcome:
    event_id: str
    label: str
    line: str
    materials: tuple[str, int, ...]
    coins: int
    empty: bool


#: 事件 → 文案模板。`{npc}` 由调用方填当日主角名（无 NPC 时填「路过的人」）。
EVENT_LINES: dict[str, str] = {
    "small_find": "你在{npc}提醒的地方挖到了一点东西。",
    "stray_pet": "一只小东西怯生生地跟了你一段路。",
    "inscription": "{npc}说，这行字明天就会自己消失。",
    "old_cache": "旧行囊里有前人留下的零碎。",
    "night_light": "夜里那点光，是萤火虫替你照的路。",
    "market_rumor": "{npc}小声说了句今天生意不太好的事。",
    "rain_pool": "水洼里映出一整片你没注意过的天。",
    "nest": "草丛里的窝空着——小东西刚离开。",
    "faded_photo": "照片里的人站在你现在站的地方。",
    "quiet": "风过了一下，就没了。",
    "nothing": "这里什么也没有。你还是把这段路走完了。",
}


def resolve_event(owner: str, theme: str, day: int, tile: tuple[int, int],
                  *, npc: str = "路过的人") -> EventOutcome:
    """roll 事件并算出实际收益。空手事件**照实发**，不偷偷塞材料。"""
    ev = roll_event(owner, theme, day, tile)
    r = rng_for(owner, theme, "event-pay", day, tile[0], tile[1])
    theme_def = get_theme(theme)
    materials: tuple[str, int, int] = ()
    coins = 0
    if ev.gives_material:
        mid = pick(r, theme_def.materials)
        materials = ((mid, 1 + r.randrange(2)),)
    if ev.id == "small_find":
        coins = r.randrange(5, 25)
    return EventOutcome(
        event_id=ev.id,
        label=ev.label,
        line=EVENT_LINES[ev.id].format(npc=npc),
        materials=materials,
        coins=coins,
        empty=ev.empty,
    )


def explore_rows(owner: str, theme: str, day: int, tiles: Iterable[tuple[int, int]],
                 *, npc: str = "路过的人") -> list[EventOutcome]:
    """一次探险的逐格结算（前端逐格播提示，后端逐格权威）。"""
    return [resolve_event(owner, theme, day, t, npc=npc) for t in tiles]


# ---------------------------------------------------------------------- #
# 移动 / 寻路（纯逻辑，供渲染段与 A6 HUD 消费）                            #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class MoveResult:
    ok: bool
    tile: tuple[int, int]
    minutes: int
    reason: str = ""


def move_cost(wm: WorldMap, frm: tuple[int, int], to: tuple[int, int]) -> int:
    """一步移动的游戏分钟数：地形代价 × 时段系数（夜里更慢，但不阻断）。"""
    base = terrain(wm.at(to)).move_cost
    if base == 0:
        return 0
    # 距离因素：斜走按曼哈顿两格算，交给调用方自己拆步
    return base


def step_towards(wm: WorldMap, frm: tuple[int, int], to: tuple[int, int]) -> MoveResult:
    """朝目标走**一步**（横向优先）。被挡 / 到界 → 明确原因，不假装移动。"""
    x, y = frm
    tx, ty = to
    if frm == to:
        return MoveResult(True, frm, 0)
    candidates = [(tx - x, 0), (0, ty - y)] if abs(tx - x) >= abs(ty - y) else [(0, ty - y), (tx - x, 0)]
    # 零位移不是「走了一步」——否则同行/同列的目标会被当成原地移动成功
    candidates = [(dx, dy) for dx, dy in candidates if (dx, dy) != (0, 0)]
    if not candidates:
        return MoveResult(True, frm, 0)
    last = ""
    for dx, dy in candidates:
        nxt = (x + dx, y + dy)
        if not wm.in_bounds(nxt):
            last = "地图边界"
            continue
        tid = wm.at(nxt)
        if not terrain(tid).walkable:
            last = TERRAIN_LABELS[tid]
            continue
        return MoveResult(True, nxt, move_cost(wm, frm, nxt))
    return MoveResult(False, frm, 0, last or "无路")


def path_length(wm: WorldMap, frm: tuple[int, int], to: tuple[int, int]) -> int | None:
    """BFS 最短步数。不可达返回 `None`（不谎称能到）。"""
    if not wm.is_walkable(frm) or not wm.is_walkable(to):
        return None
    if frm == to:
        return 0
    seen = {frm}
    frontier = [frm]
    steps = 0
    while frontier:
        steps += 1
        nxt_level: list[tuple[int, int]] = []
        for x, y in frontier:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (x + dx, y + dy)
                if nb in seen or not wm.is_walkable(nb):
                    continue
                if nb == to:
                    return steps
                seen.add(nb)
                nxt_level.append(nb)
        frontier = nxt_level
    return None


def reachable_count(wm: WorldMap, tile: tuple[int, int]) -> int:
    """以 `tile` 为起点的可达格数（探索度分母 / 摆家具时用）。"""
    if not wm.is_walkable(tile):
        return 0
    seen = {tile}
    stack = [tile]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in seen or not wm.is_walkable(nb):
                continue
            seen.add(nb)
            stack.append(nb)
    return len(seen)


# ---------------------------------------------------------------------- #
# 探索度（B10 与 HUD 的共享派生）                                          #
# ---------------------------------------------------------------------- #


def explored_ratio(wm: WorldMap, visited: Iterable[tuple[int, int]]) -> float:
    """探索度 = 已访问格 / 可走格。上限 1.0（不可走格不计入分母）。"""
    total = wm.walkable_count()
    if total == 0:
        return 0.0
    seen = {t for t in visited if wm.in_bounds(t) and wm.is_walkable(t)}
    return min(1.0, len(seen) / total)


def explore_grade(pct: float) -> tuple[str, str]:
    """探索度 → 评语。前端直接显示，不再润色。"""
    if pct < 0.05:
        return "初来乍到", "这片地方你才刚看见。"
    if pct < 0.20:
        return "转了转", "几条常走的路已经熟了。"
    if pct < 0.45:
        return "熟门熟路", "你开始记得哪块石头会绊脚。"
    if pct < 0.75:
        return "四处走走", "地图在你脑子里有了大致的形状。"
    return "无所不知", "这片土地上没你不知道的角落。"
