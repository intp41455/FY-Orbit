/**
 * B4 · 建造与装修前端数据消费层。
 *
 * 与 `src/find_yourself/services/cabin_life/build.py`（后端唯一真源）一一对应。
 * 本文件只做 **类型镜像 + 像素换算 + 展示派生**，**不重算放置规则**：
 * 能不能放、为什么不能放，一律由后端 `can_place` 判定并下发 `reason`；
 * 前端只负责把 reason 显示出来、把合法落点高亮出来。
 *
 * 与 W1（`interiorLayout.ts`，16px 网格 + `furnitureId`）**是两套**：
 *   - B4 用 A1 冻结的 32px `TILE`；
 *   - B4 的家具 id 统一 `b4_` 前缀，与 W1 目录不共用命名空间。
 * 不要把两者的尺寸或 id 混用。
 */

import { TILE } from '../cabinConfig';

/** 六大分类（与后端 `build.CATEGORIES` 同名同序）。 */
export const CATEGORIES = [
  'table_chair',
  'bed',
  'cabinet',
  'decor',
  'lamp',
  'plant',
] as const;

export type Category = (typeof CATEGORIES)[number];

/** 落位层：`floor` 贴地 / `wall` 挂墙 / `under` 铺在家具下面。 */
export const LAYERS = ['floor', 'wall', 'under'] as const;
export type Layer = (typeof LAYERS)[number];

/** 四向旋转。 */
export const ROTATIONS = [0, 90, 180, 270] as const;
export type Rotation = (typeof ROTATIONS)[number];

export interface FurnitureDef {
  id: string;
  label: string;
  category: Category;
  layer: Layer;
  /** 未旋转时的占位（格） */
  width: number;
  height: number;
  cost: number;
  comfort: number;
}

export interface PlacedItem {
  id: string;
  furniture_id: string;
  /** 格坐标（不是像素；像素 = 格 × TILE） */
  x: number;
  y: number;
  rotation: Rotation;
}

export interface Layout {
  cols: number;
  rows: number;
  items: PlacedItem[];
}

export interface RoomSpec {
  cols: number;
  rows: number;
  tile: number;
  wall_band: [number, number];
  door_row: number;
  door_clear_x: [number, number];
  max_items: number;
}

export interface ConfirmResult {
  items: number;
  total_cost: number;
  comfort: number;
  counts: Record<string, number>;
  grade: string;
  line: string;
}

export interface CategoryRow {
  category: Category;
  label: string;
  count: number;
  cheapest: number;
  ids: string[];
}

/* ------------------------------------------------------------------ */
/* 目录查找                                                              */
/* ------------------------------------------------------------------ */

export function catalogById(catalog: FurnitureDef[]): Map<string, FurnitureDef> {
  return new Map(catalog.map((f) => [f.id, f]));
}

export function furnitureLabel(catalog: FurnitureDef[], id: string): string {
  return catalogById(catalog).get(id)?.label ?? '未知家具';
}

export function furnitureLayer(catalog: FurnitureDef[], id: string): Layer {
  const f = catalogById(catalog).get(id);
  if (!f) return 'floor';
  return f.layer;
}

/** 未知 id 返回 `undefined` 而不是抛错——渲染层不该因为一条脏数据白屏。 */
export function findFurniture(
  catalog: FurnitureDef[],
  id: string,
): FurnitureDef | undefined {
  return catalogById(catalog).get(id);
}

/* ------------------------------------------------------------------ */
/* 旋转与占位                                                             */
/* ------------------------------------------------------------------ */

/**
 * 旋转后的占位尺寸。**不复刻**后端 `size_at` 的判断，
 * 但结果必须一致——`buildApi.test.ts` 用后端 fixture 钉死这一条。
 */
export function sizeAt(def: FurnitureDef, rotation: Rotation): [number, number] {
  return rotation === 90 || rotation === 270
    ? [def.height, def.width]
    : [def.width, def.height];
}

export function nextRotation(rotation: Rotation, step = 1): Rotation {
  const idx = ROTATIONS.indexOf(rotation);
  return ROTATIONS[(idx + step + ROTATIONS.length) % ROTATIONS.length] as Rotation;
}

/** 一件家具占用的格（渲染 hit-test / 预览共用）。 */
export function itemCells(
  item: PlacedItem,
  def: FurnitureDef,
): [number, number][] {
  const [w, h] = sizeAt(def, item.rotation);
  const out: [number, number][] = [];
  for (let i = 0; i < w; i += 1) {
    for (let j = 0; j < h; j += 1) {
      out.push([item.x + i, item.y + j]);
    }
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* 像素换算：格 → 虚拟像素（A1 的 TILE，不另写 32）                      */
/* ------------------------------------------------------------------ */

export function cellToPixel(cell: [number, number]): { x: number; y: number } {
  return { x: cell[0] * TILE, y: cell[1] * TILE };
}

export function cellRect(cell: [number, number]): {
  x: number;
  y: number;
  width: number;
  height: number;
} {
  return { x: cell[0] * TILE, y: cell[1] * TILE, width: TILE, height: TILE };
}

/**
 * 鼠标像素坐标 → 格坐标。
 * 用 `Math.floor` 而不是 Python 侧的 `snap`（后者是 Math.round 语义）：
 * 命中测试要的是「指针落在哪一格」，取整方向不同会差一格。
 */
export function pixelToCell(px: number, py: number): [number, number] {
  return [Math.floor(px / TILE), Math.floor(py / TILE)];
}

export function roomPixelSize(room: RoomSpec): { width: number; height: number } {
  return { width: room.cols * TILE, height: room.rows * TILE };
}

/* ------------------------------------------------------------------ */
/* 禁放位与占位（渲染层据此画红色格）                                     */
/* ------------------------------------------------------------------ */

export function doorCells(room: RoomSpec): [number, number][] {
  const [lo, hi] = room.door_clear_x;
  const out: [number, number][] = [];
  for (let x = lo; x <= hi; x += 1) out.push([x, room.door_row]);
  return out;
}

export function wallCells(room: RoomSpec): [number, number][] {
  const [lo, hi] = room.wall_band;
  const out: [number, number][] = [];
  for (let y = lo; y <= hi; y += 1) {
    for (let x = 0; x < room.cols; x += 1) out.push([x, y]);
  }
  return out;
}

/** 被占 + 禁放的格。`under` 层不计入（地毯不挡路）。 */
export function blockedCells(
  layout: Layout,
  room: RoomSpec,
  catalog: FurnitureDef[],
): Set<string> {
  const byId = catalogById(catalog);
  const key = (x: number, y: number) => `${x},${y}`;
  const out = new Set<string>(doorCells(room).map(([x, y]) => key(x, y)));
  for (const item of layout.items) {
    const def = byId.get(item.furniture_id);
    if (!def || def.layer === 'under') continue;
    for (const [x, y] of itemCells(item, def)) out.add(key(x, y));
  }
  return out;
}

/** 拖动预览的落点格；越界格也一并返回，好让玩家看见「有一格在外面」。 */
export function ghostCells(
  furnitureId: string,
  x: number,
  y: number,
  rotation: Rotation,
  catalog: FurnitureDef[],
): [number, number][] {
  const def = catalogById(catalog).get(furnitureId);
  if (!def) return [];
  return itemCells({ id: 'ghost', furniture_id: furnitureId, x, y, rotation }, def);
}

/* ------------------------------------------------------------------ */
/* 展示派生                                                               */
/* ------------------------------------------------------------------ */

export function categoryLabel(category: Category): string {
  const map: Record<Category, string> = {
    table_chair: '桌椅',
    bed: '床',
    cabinet: '柜',
    decor: '装饰',
    lamp: '灯具',
    plant: '植物',
  };
  return map[category];
}

/** 家具面板按分类分组；空分类也**保留**（显示「暂无」，不静默消失）。 */
export function categoryRows(catalog: FurnitureDef[]): CategoryRow[] {
  return CATEGORIES.map((category) => {
    const items = catalog.filter((f) => f.category === category);
    return {
      category,
      label: categoryLabel(category),
      count: items.length,
      cheapest: items.length ? Math.min(...items.map((f) => f.cost)) : 0,
      ids: items.map((f) => f.id),
    };
  });
}

/** 一件家具的中文尺寸描述（如「3 × 2 格」），随旋转变化。 */
export function sizeLabel(def: FurnitureDef, rotation: Rotation): string {
  const [w, h] = sizeAt(def, rotation);
  return `${w} × ${h} 格`;
}

/** 落位层说明，鼠标悬停时显示，避免玩家把挂画往地上放。 */
export function layerHint(layer: Layer): string {
  switch (layer) {
    case 'wall':
      return '挂在墙上';
    case 'under':
      return '铺在家具下面';
    default:
      return '放在地上';
  }
}

/** 拖动中的合法性提示；后端给了 reason 就直接显示，不二次润色。 */
export function dragHint(reason: string | null | undefined): {
  ok: boolean;
  text: string;
} {
  if (!reason) return { ok: true, text: '可以放这里' };
  return { ok: false, text: reason };
}

/** 确认按钮上的总价文案（后端已算好，这里只排版）。 */
export function confirmLabel(confirm: ConfirmResult): string {
  return `确认布置 · ${confirm.items} 件 · ${confirm.total_cost} 金`;
}

/** 列表里的家具条目：名称 + 尺寸 + 价格。 */
export function itemLine(def: FurnitureDef, rotation: Rotation = 0): string {
  return `${def.label} · ${sizeLabel(def, rotation)} · ${def.cost} 金`;
}
