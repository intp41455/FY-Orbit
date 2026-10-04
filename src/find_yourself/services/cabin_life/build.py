"""B4 · 建造与装修（**数据层**：家具目录 / 网格吸附 / 旋转 / 重叠 / 禁放位）。

说明书 §5-B4：
    * 网格摆放 → 选中 / 移动 / 旋转 / 确认；重叠检测；
    * **不可放墙上与门口**；家具分类（桌椅 / 床 / 柜 / 装饰 / 灯具 / 植物）；
    * 验收：网格吸附；越界 / 禁用位**拒绝并提示**。

与 W1 `services/cabin_interior.py` 的关系（重要，别混用）
----------------------------------------------------------
W1 是**另一套**室内布置持久化：16px 网格、30×10 房间、`furnitureId` 白名单、
带 `flipped/colorway/z` 字段、走 HTTP 落库。本模块是 B 包**像素大世界房屋装修**
的纯规则层：**32px 网格**（对齐 A1 冻结的 `TILE`）、有旋转与「门口禁放」概念、
产出规则与判定而不落库。两套 id 命名空间独立（`b4_` 前缀），互不覆盖。

设计要点
--------
* **网格吸附**：坐标一律取整到格（`snap`），浮点输入不许直接进布局；
* **旋转**：4 向（0/90/180/270），旋转后尺寸互换，且受房间与禁放位约束；
* **重叠**：按格占用集求交，**地毯类**（`layer='under'`）可与家具叠放，
  否则「地毯 + 床」永远无法摆放——这是规则而不是漏洞；
* **禁放位**：门口矩形 + 墙面带（`WALL_BAND`）+ 壁挂类只能上墙，
  每种拒绝都带**明确原因文案**，前端直接显示，不二次润色。

诚实原则（§2-1）
----------------
* 未知家具 id / 未知分类 / 未知朝向 → 抛错，不静默回落成「椅子 1×1」；
* 越界、重叠、禁放**一律拒绝并给原因**，绝不「自动挪一下」替玩家做决定；
* 本模块**零 IO**：不落库、不读时钟、不用全局随机。玩家金币扣减属经营层（B8）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------- #
# 网格（引用 A1 冻结的 TILE，不重新定义）                                 #
# ---------------------------------------------------------------------- #

#: 一格边长（虚拟像素）—— 对齐 A1 `cabinConfig.ts` 的 `TILE = 32`。
TILE = 32

#: 可装修房间的格尺寸（不含外墙）。
ROOM_COLS = 12
ROOM_ROWS = 9

#: 门所在的那一行的厚度（格）。门在房间南墙内侧。
DOOR_ROW = ROOM_ROWS - 1

#: 门口禁放区：以门为中心的矩形（含自身），家具不许摆。
DOOR_CLEAR_X = (ROOM_COLS // 2 - 1, ROOM_COLS // 2 + 1)

#: 墙面带厚度（格）。`wall` 落位法的家具只能占这几行。
WALL_BAND = (0, 1)

#: 一件家具最多占多少格（防手滑放个 99×99 的东西撑爆布局）。
MAX_FOOTPRINT_CELLS = 12

#: 布局里最多放几件（与 W1 的 `MAX_ITEMS=60` 同量级，此处独立）。
MAX_ITEMS = 60


# ---------------------------------------------------------------------- #
# 家具目录                                                                  #
# ---------------------------------------------------------------------- #

#: 六大分类（说明书点名：桌椅/床/柜/装饰/灯具/植物）
CATEGORIES: tuple[str, ...] = ("table_chair", "bed", "cabinet", "decor", "lamp", "plant")

CATEGORY_LABELS: dict[str, str] = {
    "table_chair": "桌椅",
    "bed": "床",
    "cabinet": "柜",
    "decor": "装饰",
    "lamp": "灯具",
    "plant": "植物",
}

#: 落位层：`floor` 贴地 / `wall` 挂墙 / `under` 铺在家具下面（地毯）。
LAYERS: tuple[str, ...] = ("floor", "wall", "under")


@dataclass(frozen=True)
class FurnitureDef:
    """一件家具的静态定义（尺寸为**未旋转**时）。"""

    id: str
    label: str
    category: str
    layer: str
    width: int
    height: int
    #: 摆放代价（金币）。建造层只读不算，避免与 B8 经营的定价规则打架。
    cost: int
    #: 舒适度贡献（装修评分用）。
    comfort: int = 0

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: 未知分类 {self.category!r}")
        if self.layer not in LAYERS:
            raise ValueError(f"{self.id}: 未知落位层 {self.layer!r}")
        if self.width < 1 or self.height < 1:
            raise ValueError(f"{self.id}: 尺寸必须为正")
        if self.width * self.height > MAX_FOOTPRINT_CELLS:
            raise ValueError(f"{self.id}: 占格 {self.width * self.height} 超上限")

    @property
    def footprint(self) -> int:
        return self.width * self.height


def _f(fid: str, label: str, category: str, layer: str, w: int, h: int,
       cost: int, comfort: int = 0) -> FurnitureDef:
    return FurnitureDef(fid, label, category, layer, w, h, cost, comfort)


#: 家具目录（id 统一 `b4_` 前缀，与 W1 的 id 命名空间隔离）
CATALOG: tuple[FurnitureDef, ...] = (
    # ---- 桌椅 ----
    _f("b4_dining_table", "方桌", "table_chair", "floor", 2, 2, 120, 4),
    _f("b4_stool", "小凳", "table_chair", "floor", 1, 1, 30, 1),
    _f("b4_tea_table", "矮茶几", "table_chair", "floor", 2, 1, 80, 3),
    _f("b4_writing_desk", "书桌", "table_chair", "floor", 3, 1, 160, 5),
    # ---- 床 ----
    _f("b4_single_bed", "单人床", "bed", "floor", 2, 3, 240, 6),
    _f("b4_double_bed", "双人床", "bed", "floor", 3, 3, 420, 8),
    _f("b4_bunk_bed", "上下铺", "bed", "floor", 2, 4, 380, 6),
    _f("b4_hammock", "吊床", "bed", "floor", 2, 1, 150, 4),
    # ---- 柜 ----
    _f("b4_wardrobe", "衣柜", "cabinet", "floor", 2, 1, 300, 5),
    _f("b4_shelf", "置物架", "cabinet", "floor", 1, 2, 140, 3),
    _f("b4_drawer", "抽屉柜", "cabinet", "floor", 2, 1, 170, 3),
    _f("b4_coatrack", "衣帽架", "cabinet", "floor", 1, 1, 90, 2),
    # ---- 装饰 ----
    _f("b4_rug_small", "小地毯", "decor", "under", 2, 2, 60, 3),
    _f("b4_rug_long", "长地毯", "decor", "under", 3, 1, 90, 4),
    _f("b4_painting", "挂画", "decor", "wall", 1, 1, 110, 3),
    _f("b4_wall_clock", "挂钟", "decor", "wall", 1, 1, 70, 2),
    _f("b4_mirror_small", "穿衣镜", "decor", "wall", 1, 1, 130, 3),
    _f("b4_rug_large", "大块地毯", "decor", "under", 3, 3, 180, 6),
    # ---- 灯具 ----
    _f("b4_ceiling_lamp", "吊灯", "lamp", "floor", 2, 1, 200, 4),
    _f("b4_floor_lamp", "落地灯", "lamp", "floor", 1, 2, 120, 3),
    _f("b4_desk_lamp", "台灯", "lamp", "floor", 1, 1, 60, 2),
    _f("b4_lantern", "纸灯笼", "lamp", "wall", 1, 1, 80, 2),
    _f("b4_candle", "烛台", "lamp", "floor", 1, 1, 40, 1),
    # ---- 植物 ----
    _f("b4_pot_plant", "盆栽", "plant", "floor", 1, 1, 50, 2),
    _f("b4_tall_plant", "高盆栽", "plant", "floor", 1, 2, 95, 3),
    _f("b4_hanging_vine", "垂吊绿萝", "plant", "wall", 1, 1, 70, 2),
    _f("b4_herb_box", "香草箱", "plant", "floor", 2, 1, 85, 3),
    _f("b4_bonsai", "小盆景", "plant", "floor", 1, 1, 160, 4),
)

CATALOG_BY_ID: dict[str, FurnitureDef] = {f.id: f for f in CATALOG}


def furniture(id: str) -> FurnitureDef:
    """按 id 取家具；未知 id 抛错（不静默回落成「什么都能放」）。"""
    try:
        return CATALOG_BY_ID[id]
    except KeyError:
        raise KeyError(f"未知家具 id: {id!r}；可用 {len(CATALOG)} 件") from None


def of_category(category: str) -> tuple[FurnitureDef, ...]:
    """按分类取家具；未知分类抛错。"""
    if category not in CATEGORIES:
        raise KeyError(f"未知家具分类: {category!r}；可用 {list(CATEGORIES)}")
    return tuple(f for f in CATALOG if f.category == category)


def category_rows() -> list[dict[str, object]]:
    """家具面板：按分类分组，计数与最低价都如实给出。"""
    rows = []
    for cat in CATEGORIES:
        items = of_category(cat)
        rows.append({
            "category": cat,
            "label": CATEGORY_LABELS[cat],
            "count": len(items),
            "cheapest": min(f.cost for f in items),
            "ids": [f.id for f in items],
        })
    return rows


# ---------------------------------------------------------------------- #
# 旋转                                                                      #
# ---------------------------------------------------------------------- #

#: 四向朝向（顺时针度数）
ROTATIONS: tuple[int, ...] = (0, 90, 180, 270)


def normalize_rotation(deg: int) -> int:
    """把任意角度归一到 0/90/180/270；不是 90 的倍数 → 抛错。"""
    if deg not in ROTATIONS:
        raise ValueError(f"旋转角必须是 {ROTATIONS} 之一，收到 {deg}")
    return deg


def size_at(f: FurnitureDef, rotation: int) -> tuple[int, int]:
    """旋转后的占位尺寸（90° / 270° 时宽高互换）。"""
    normalize_rotation(rotation)
    return (f.height, f.width) if rotation in (90, 270) else (f.width, f.height)


def next_rotation(deg: int, step: int = 1) -> int:
    """从当前朝向再转 `step` × 90°。"""
    idx = ROTATIONS.index(normalize_rotation(deg))
    return ROTATIONS[(idx + step) % len(ROTATIONS)]


# ---------------------------------------------------------------------- #
# 网格吸附                                                                  #
# ---------------------------------------------------------------------- #


def snap(value: float, tile: int = TILE) -> int:
    """把虚拟像素坐标吸附到格（A1 `cabinConfig.snapToGrid` 的同口径实现）。

    注意**不用** Python 内置 `round`：它是银行家舍入（`round(0.5) == 0`），
    而 A1 的 `Math.round(0.5) === 1`。两端在整半格上会差一格，
    表现为「后端说吸附到这格、前端却画到隔壁格」。故显式走 floor(x + 0.5)。
    """
    if tile <= 0:
        raise ValueError(f"tile 必须为正，收到 {tile}")
    return math.floor(value / tile + 0.5) * tile


def snap_tile(value: float) -> int:
    """像素 → 格号（向下取整）。"""
    return int(value // TILE)


def tile_of(px: float) -> tuple[int, int]:
    """像素坐标 → 格坐标。"""
    return (int(px // TILE), 0)  # 单轴用；二维见 `tile_of_xy`


def tile_of_xy(px: float, py: float) -> tuple[int, int]:
    return (int(px // TILE), int(py // TILE))


# ---------------------------------------------------------------------- #
# 布局                                                                      #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlacedItem:
    """一件已摆放的家具。坐标是**格**，不是像素——像素由渲染层乘 `TILE`。"""

    id: str
    furniture_id: str
    x: int
    y: int
    rotation: int = 0

    def __post_init__(self) -> None:
        furniture(self.furniture_id)
        normalize_rotation(self.rotation)

    @property
    def def_(self) -> FurnitureDef:
        return furniture(self.furniture_id)

    @property
    def size(self) -> tuple[int, int]:
        return size_at(self.def_, self.rotation)

    @property
    def cells(self) -> tuple[tuple[int, int], ...]:
        """占用的格集合（旋转后）。"""
        w, h = self.size
        return tuple((self.x + i, self.y + j) for i in range(w) for j in range(h))

    @property
    def footprint(self) -> int:
        return len(self.cells)


@dataclass
class Layout:
    """一间房的布局（可序列化）。`items` 有序 = 绘制顺序。"""

    items: list[PlacedItem] = field(default_factory=list)
    cols: int = ROOM_COLS
    rows: int = ROOM_ROWS

    def by_id(self, item_id: str) -> PlacedItem:
        for it in self.items:
            if it.id == item_id:
                return it
        raise KeyError(f"布局里没有这件家具: {item_id!r}")

    def occupied(self) -> dict[tuple[int, int], str]:
        """格 → 挡住路的 item_id。`under` 层不计入（可与家具叠放）。"""
        taken: dict[tuple[int, int], str] = {}
        for it in self.items:
            if it.def_.layer == "under":
                continue
            for c in it.cells:
                taken[c] = it.id
        return taken

    def layered(self) -> dict[tuple[int, int], tuple[str, str]]:
        """格 → `(item_id, layer)`。用于「谁挡谁」的对称判定。"""
        taken: dict[tuple[int, int], tuple[str, str]] = {}
        for it in self.items:
            for c in it.cells:
                taken[c] = (it.id, it.def_.layer)
        return taken

    def ids(self) -> list[str]:
        return [it.id for it in self.items]


def new_layout() -> Layout:
    return Layout(items=[], cols=ROOM_COLS, rows=ROOM_ROWS)


# ---------------------------------------------------------------------- #
# 禁放位                                                                    #
# ---------------------------------------------------------------------- #


def door_cells() -> set[tuple[int, int]]:
    """门口禁放矩形。"""
    lo, hi = DOOR_CLEAR_X
    return {(x, DOOR_ROW) for x in range(lo, hi + 1)}


def wall_cells() -> set[tuple[int, int]]:
    """墙面带所有格（挂画/挂钟等的合法带）。"""
    lo, hi = WALL_BAND
    return {(x, y) for x in range(ROOM_COLS) for y in range(lo, hi + 1)}


def is_door_cell(cell: tuple[int, int]) -> bool:
    return cell in door_cells()


# ---------------------------------------------------------------------- #
# 校验                                                                      #
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class Verdict:
    """一次摆放/移动的判定结果。`ok=False` 时 `reason` 必填。"""

    ok: bool
    reason: str = ""
    item: PlacedItem | None = None

    def __bool__(self) -> bool:
        return self.ok


def _fits(layout: Layout, item: PlacedItem) -> str:
    """返回空串表示通过，否则返回拒绝原因。"""
    w, h = item.size
    if w > layout.cols or h > layout.rows:
        return f"{item.def_.label}放不下这个房间（需要 {w}×{h} 格）"
    if item.x < 0 or item.y < 0 or item.x + w > layout.cols or item.y + h > layout.rows:
        return (f"超出房间范围：需要 x∈[0,{layout.cols - w}]、"
                f"y∈[0,{layout.rows - h}]，实际 ({item.x},{item.y})")

    layer = item.def_.layer
    if layer == "wall":
        lo, hi = WALL_BAND
        if item.y < lo or item.y + h - 1 > hi:
            return f"{item.def_.label}只能挂在墙上（第 {lo}~{hi} 行）"
    elif layer == "floor":
        # 贴地家具不许占墙上那两行（否则等于悬空）
        lo, hi = WALL_BAND
        if item.y < lo + 1:
            return f"{item.def_.label}不能放到墙上（第 {lo}~{hi} 行留给挂件）"
    else:  # under
        if item.y < WALL_BAND[1] + 1:
            return f"{item.def_.label}不能铺到墙根"

    for c in item.cells:
        if is_door_cell(c):
            return f"门口要留出路，{item.def_.label}不能挡着门"

    # 重叠判定**对称**：只要有一方是 `under`（地毯），就允许叠放——
    # 否则「先放床再铺地毯」会被拒而「先铺地毯再放床」能过，顺序决定成败是 bug。
    for c in item.cells:
        holder = layout.layered().get(c)
        if holder is None:
            continue
        other_layer = holder[1]
        if "under" in (item.def_.layer, other_layer):
            continue
        return f"和{layout.by_id(holder[0]).def_.label}重叠了"
    return ""


def validate(layout: Layout, item: PlacedItem, *, ignore_id: str | None = None) -> Verdict:
    """校验「把这件放到 `item` 的位置」是否合法。

    `ignore_id` 用于移动/旋转：把自身原有占位从重叠集里排除。
    """
    if len(layout.items) >= MAX_ITEMS and (ignore_id is None or ignore_id not in layout.ids()):
        return Verdict(False, f"家具最多 {MAX_ITEMS} 件，房间放不下了")
    probe = layout
    if ignore_id is not None:
        probe = Layout(items=[i for i in layout.items if i.id != ignore_id],
                       cols=layout.cols, rows=layout.rows)
    reason = _fits(probe, item)
    if reason:
        return Verdict(False, reason)
    return Verdict(True, "", item)


def can_place(layout: Layout, item: PlacedItem) -> Verdict:
    """放置前检查（不改动布局）。"""
    if item.id in layout.ids():
        return Verdict(False, f"布局里已经有同 id 的家具: {item.id!r}")
    return validate(layout, item)


# ---------------------------------------------------------------------- #
# 操作：放置 / 移动 / 旋转 / 确认 / 移除                                   #
# ---------------------------------------------------------------------- #


def place(layout: Layout, item: PlacedItem) -> Layout:
    """放置并返回**新布局**（不改原对象——前端预览需要「试摆」）。"""
    v = can_place(layout, item)
    if not v.ok:
        raise ValueError(v.reason)
    return Layout(items=[*layout.items, item], cols=layout.cols, rows=layout.rows)


def move_to(layout: Layout, item_id: str, x: int, y: int) -> Layout:
    """把已有家具移到 (x, y)（格坐标）。非法 → 抛错并说明原因。"""
    old = layout.by_id(item_id)
    moved = PlacedItem(old.id, old.furniture_id, int(x), int(y), old.rotation)
    v = validate(layout, moved, ignore_id=item_id)
    if not v.ok:
        raise ValueError(v.reason)
    return Layout(items=[moved if i.id == item_id else i for i in layout.items],
                  cols=layout.cols, rows=layout.rows)


def rotate_item(layout: Layout, item_id: str, step: int = 1) -> Layout:
    """旋转已有家具。转完放不下 → 抛错（不「转一半」也不静默回退）。"""
    old = layout.by_id(item_id)
    turned = PlacedItem(old.id, old.furniture_id, old.x, old.y,
                        next_rotation(old.rotation, step))
    v = validate(layout, turned, ignore_id=item_id)
    if not v.ok:
        raise ValueError(f"转不开：{v.reason}")
    return Layout(items=[turned if i.id == item_id else i for i in layout.items],
                  cols=layout.cols, rows=layout.rows)


def remove(layout: Layout, item_id: str) -> Layout:
    """移除一件家具。"""
    layout.by_id(item_id)  # 不存在 → KeyError
    return Layout(items=[i for i in layout.items if i.id != item_id],
                  cols=layout.cols, rows=layout.rows)


def confirm(layout: Layout) -> dict[str, object]:
    """「确认装修」结算：花掉的总价、舒适度、评语。**只算不清零**。

    金币扣减属 B8 经营层；这里只把总价报出来，由调用方决定是否扣。
    """
    total_cost = sum(i.def_.cost for i in layout.items)
    comfort = sum(i.def_.comfort for i in layout.items)
    counts = {cat: len(items_of(layout, cat)) for cat in CATEGORIES}
    return {
        "items": len(layout.items),
        "total_cost": total_cost,
        "comfort": comfort,
        "counts": counts,
        "grade": comfort_grade(comfort),
        "line": f"布置了 {len(layout.items)} 件家具，舒适度 {comfort}",
    }


def comfort_grade(comfort: int) -> str:
    """舒适度 → 一句评语。阈值写死在此，便于测试逐档钉住。"""
    if comfort <= 0:
        return "还什么都没有。"
    if comfort < 10:
        return "勉强能住。"
    if comfort < 25:
        return "像个家了。"
    if comfort < 45:
        return "待着很舒服。"
    if comfort < 70:
        return "来客人都舍不得走。"
    return "这里是你自己的地方。"


# ---------------------------------------------------------------------- #
# 序列化                                                                    #
# ---------------------------------------------------------------------- #


def to_dict(layout: Layout) -> dict[str, object]:
    return {
        "cols": layout.cols,
        "rows": layout.rows,
        "items": [
            {"id": i.id, "furniture_id": i.furniture_id, "x": i.x, "y": i.y,
             "rotation": i.rotation}
            for i in layout.items
        ],
    }


def from_dict(raw: dict[str, object]) -> Layout:
    """反序列化。**严格**：未知家具 id / 非整数坐标 → 抛错，不静默修正。"""
    cols = int(raw.get("cols", ROOM_COLS))  # type: ignore[arg-type]
    rows = int(raw.get("rows", ROOM_ROWS))  # type: ignore[arg-type]
    items = []
    for entry in raw.get("items", []):  # type: ignore[union-attr]
        x, y = entry["x"], entry["y"]  # type: ignore[index]
        if not isinstance(x, int) or isinstance(x, bool):
            raise ValueError(f"坐标必须是整数，收到 {x!r}")
        if not isinstance(y, int) or isinstance(y, bool):
            raise ValueError(f"坐标必须是整数，收到 {y!r}")
        items.append(PlacedItem(
            str(entry["id"]),          # type: ignore[index]
            str(entry["furniture_id"]),  # type: ignore[index]
            x, y,
            int(entry.get("rotation", 0)),  # type: ignore[union-attr]
        ))
    return Layout(items=items, cols=cols, rows=rows)


# ---------------------------------------------------------------------- #
# 拖动预览：找出最近的合法落点                                              #
# ---------------------------------------------------------------------- #


def nearest_legal(layout: Layout, item: PlacedItem,
                  *, max_radius: int = 6) -> PlacedItem | None:
    """鼠标停在非法位置时，找出曼哈顿半径内最近的合法落点（用于吸附高亮）。

    找不到（`max_radius` 内全堵）→ 返回 `None`，**不随便挑一个**。
    """
    if can_place(layout, item).ok:
        return item
    origin = (item.x, item.y)
    for r in range(1, max_radius + 1):
        for dx, dy in _ring(origin, r):  # 按环上固定顺序扫，首个合法者即最近
            cand = PlacedItem(item.id, item.furniture_id,
                              item.x + dx, item.y + dy, item.rotation)
            if can_place(layout, cand).ok:
                return cand
    return None


def _ring(center: tuple[int, int], r: int) -> list[tuple[int, int]]:
    cx, cy = center
    out: list[tuple[int, int]] = []
    for i in range(-r, r + 1):
        out.append((i, -r))
        if i not in (-r, r):
            out.append((i, r))
    for j in range(-r + 1, r):
        out.append((-r, j))
        out.append((r, j))
    return out


def ghost_footprint(layout: Layout, furniture_id: str, x: int, y: int,
                    rotation: int = 0) -> list[tuple[int, int]]:
    """拖动中的半透明预览占位（含越界格，让玩家看见「有一格在外面」）。"""
    return list(PlacedItem("ghost", furniture_id, x, y, rotation).cells)


def blocked_cells(layout: Layout) -> set[tuple[int, int]]:
    """当前被占 / 禁放的格集合，供渲染层把这些格画成红色。"""
    cells = set(layout.occupied()) | door_cells()
    return {c for c in cells if 0 <= c[0] < layout.cols and 0 <= c[1] < layout.rows}


def items_of(layout: Layout, category: str) -> list[PlacedItem]:
    return [i for i in layout.items if i.def_.category == category]


def category_of(layout: Layout, item_id: str) -> str:
    return layout.by_id(item_id).def_.category


def all_item_ids(furniture_ids: Iterable[str]) -> list[str]:
    """给一批家具 id 生成稳定的摆放 id（前端拖动时用）。"""
    return [f"b4_{fid}_{i}" for i, fid in enumerate(furniture_ids)]
