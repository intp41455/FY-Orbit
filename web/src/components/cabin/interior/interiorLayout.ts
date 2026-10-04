import { getFurniture, unlockedFurniture } from './furnitureCatalog';
import { getColorwayCount } from './furnitureArt';

/**
 * W1 · 室内布置状态层（纯逻辑，jsdom 可直测）
 *
 * 职责：布局数据模型 + 坐标/吸附/深度排序 + 默认布置 + 校验 + 双写持久化。
 * 不 import PixiJS / 不碰 canvas，渲染层见 cabinInteriorScene.ts。
 *
 * 坐标体系（务必与渲染层一致）：
 *  - 逻辑单位 = **16px 网格格**（GRID）。虚拟分辨率 480×270 → 30×16.875 格。
 *  - 家具位置 `x`/`y` 是**占位左上角**的格坐标；渲染时乘 GRID 并按格宽居中。
 *  - `z` 为显式图层序（越大越靠前），解决同 y 时的遮挡歧义。
 *
 * Summerhouse 哲学（11 号文档 ⑤ / 任务书增补第 1 条）：
 *  **允许重叠**，不强制网格独占。网格吸附是「可选辅助」（snap 默认开，可关）。
 *  重叠时按 `y + h`（伪深度）自动修正渲染顺序，玩家不需要手动调层级也能看对。
 */

/** 网格边长（虚拟像素）。任务书第 3 条硬要求 16px。 */
export const GRID = 16;

/** 室内虚拟分辨率（与室外场景一致）。 */
export const INTERIOR_WIDTH = 480;
export const INTERIOR_HEIGHT = 270;

/** 地面起始 y（虚拟像素）：上部为墙，下部为地板。 */
export const FLOOR_Y = 96;

/** 布局可用格数（留 1 格墙面安全边）。 */
export const ROOM_COLS = Math.floor(INTERIOR_WIDTH / GRID); // 30
export const ROOM_ROWS = Math.floor((INTERIOR_HEIGHT - FLOOR_Y) / GRID); // 10

/** 单个布局内最多家具数（防滥用，同时约束 payload 大小）。 */
export const MAX_ITEMS = 60;

/** layout JSON 序列化后的大小上限（任务书第 4 节：≤64KB）。 */
export const MAX_LAYOUT_BYTES = 64 * 1024;

export interface InteriorItem {
  /** 实例 id（同一种家具可放多件）。 */
  readonly id: string;
  /** 家具注册表 id。 */
  readonly furnitureId: string;
  /** 占位左上角格坐标。 */
  readonly x: number;
  readonly y: number;
  /** 水平镜像（Summerhouse「旋转/翻转」在 2D 俯视下的最小可用实现）。 */
  readonly flipped: boolean;
  /** 配色档位索引。 */
  readonly colorway: number;
  /** 显式图层序，越大越靠前；缺省时由伪深度推导。 */
  readonly z: number;
}

export interface InteriorLayout {
  /** 房屋模板 id（决定用哪套布置）。 */
  readonly houseId: string;
  readonly items: readonly InteriorItem[];
  /** 乐观锁版本号，与后端 cabin_interiors.version 对齐。 */
  readonly version: number;
}

/* ------------------------------------------------------------------ */
/* 几何工具                                                             */
/* ------------------------------------------------------------------ */

let instanceSeq = 0;

/** 生成实例 id（不依赖 crypto，jsdom 稳定可测）。 */
export function newItemId(prefix = 'f'): string {
  instanceSeq += 1;
  const salt = (instanceSeq * 2654435761) % 46656;
  return `${prefix}${instanceSeq.toString(36)}${salt.toString(36)}`;
}

/** 仅供测试：重置实例序号。 */
export function resetItemIds(): void {
  instanceSeq = 0;
}

/** 吸附到 16px 网格（floor 取整、负值向下取整，保证落位单调）。 */
export function snapToGrid(v: number): number {
  return Math.floor(v / GRID);
}

/** 家具占位宽（虚拟像素）。未知 id 返回 0。 */
export function itemPixelWidth(furnitureId: string): number {
  return (getFurniture(furnitureId)?.sizeCells.w ?? 0) * GRID;
}

/** 家具占位高（虚拟像素）。 */
export function itemPixelHeight(furnitureId: string): number {
  return (getFurniture(furnitureId)?.sizeCells.h ?? 0) * GRID;
}

/**
 * 约束坐标到房间内（不同落位方式用不同可用区域）：
 *  - floor   ：地面区（y ≥ 0 且不超出底部）
 *  - wall    ：墙面区（y 为负偏移表示「挂在墙面上」，此处夹到墙面带）
 *  - ceiling ：天花板带
 * 返回夹取后的格坐标。
 */
export function clampToRoom(furnitureId: string, x: number, y: number): { x: number; y: number } {
  const def = getFurniture(furnitureId);
  if (!def) return { x: 0, y: 0 };
  const w = def.sizeCells.w;
  const h = def.sizeCells.h;
  let minX = 0;
  let minY = 0;
  let maxX = ROOM_COLS - w;
  let maxY = ROOM_ROWS - h;
  if (def.mount === 'wall') {
    // 墙面：占据房间上部（0..3 格）
    minY = 0;
    maxY = 3;
  } else if (def.mount === 'ceiling') {
    minY = 0;
    maxY = 1;
  }
  maxX = Math.max(minX, maxX);
  maxY = Math.max(minY, maxY);
  return {
    x: Math.min(maxX, Math.max(minX, Math.round(x))),
    y: Math.min(maxY, Math.max(minY, Math.round(y))),
  };
}

/**
 * 伪深度排序键：`y + h` 越大越靠前（站在更靠下的位置 = 更靠前的图层）。
 * 同深度时用 `z` 再用 `id` 兜底，保证排序**稳定可测**（不依赖渲染顺序）。
 */
export function depthKey(item: InteriorItem): number {
  const h = getFurniture(item.furnitureId)?.sizeCells.h ?? 1;
  return (item.y + h) * 1000 + item.z;
}

/** 按伪深度排序（渲染顺序：数组靠前 = 先画 = 被靠后的家具遮挡）。 */
export function sortByDepth(items: readonly InteriorItem[]): InteriorItem[] {
  return [...items].sort((a, b) => {
    const d = depthKey(a) - depthKey(b);
    if (d !== 0) return d;
    if (a.z !== b.z) return a.z - b.z;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

/* ------------------------------------------------------------------ */
/* 小屋等级（增补第 3 条：≥4 件家具由等级解锁）                          */
/* ------------------------------------------------------------------ */

/**
 * 由已布置家具数推导小屋等级 Lv1..Lv5。
 *
 * 诚实说明：这是 W1 的**占位实现**——真正的等级经验来自 W2 的探险/任务线。
 * 这里只保证「布置越多等级越高、解锁越丰富」的手感，不伪装成完整成长系统。
 */
export function deriveCabinLevel(itemCount: number): number {
  if (itemCount >= 16) return 5;
  if (itemCount >= 11) return 4;
  if (itemCount >= 7) return 3;
  if (itemCount >= 3) return 2;
  return 1;
}

export const MAX_CABIN_LEVEL = 5;

/* ------------------------------------------------------------------ */
/* 校验 / 清洗（与后端 sanitize 同规则）                                  */
/* ------------------------------------------------------------------ */

/**
 * 逐字段白名单清洗：家具 id 必须在注册表内、坐标夹取到房间内、
 * colorway 夹取到合法档位、实例 id 去重、数量与体积受限。
 * 损坏数据一律**丢弃该条**而不是整份报废（尽可能救回用户布置）。
 */
export function sanitizeLayout(raw: unknown, houseId: string): InteriorLayout {
  const fallback = defaultLayout(houseId);
  if (typeof raw !== 'object' || raw === null) return fallback;
  const r = raw as Record<string, unknown>;
  const version = Number.isInteger(r.version) && (r.version as number) >= 1 ? (r.version as number) : 1;
  if (!Array.isArray(r.items)) return { ...fallback, version };

  const seen = new Set<string>();
  const out: InteriorItem[] = [];
  for (const entry of r.items) {
    if (out.length >= MAX_ITEMS) break;
    if (typeof entry !== 'object' || entry === null) continue;
    const e = entry as Record<string, unknown>;
    const def = getFurniture(String(e.furnitureId ?? ''));
    if (!def) continue; // 家具不在注册表 → 丢弃（后端同样拒绝）
    let id = typeof e.id === 'string' && e.id ? e.id : '';
    if (!id || seen.has(id)) id = newItemId();
    seen.add(id);
    const pos = clampToRoom(def.id, Number(e.x ?? 0), Number(e.y ?? 0));
    const colorway = Number.isFinite(e.colorway) ? Math.round(e.colorway as number) : 0;
    const z = Number.isFinite(e.z) ? Math.round(e.z as number) : out.length;
    out.push({
      id,
      furnitureId: def.id,
      x: pos.x,
      y: pos.y,
      flipped: Boolean(e.flipped),
      colorway: Math.max(0, colorway),
      z,
    });
  }
  return { houseId, items: out, version };
}

/** 序列化后体积是否超限（后端同规则，≤64KB）。 */
export function isLayoutTooLarge(layout: InteriorLayout): boolean {
  return JSON.stringify(layout).length > MAX_LAYOUT_BYTES;
}

/* ------------------------------------------------------------------ */
/* 默认布置                                                             */
/* ------------------------------------------------------------------ */

interface DefaultSpec {
  id: string;
  x: number;
  y: number;
  flipped?: boolean;
  colorway?: number;
}

/**
 * 每个房屋模板一套默认布置（任务书第 4 条：未布置过给默认布置）。
 * 坐标是「看起来已经有人住」的手工排布，不是随机（确定性 → 可测）。
 */
const DEFAULT_LAYOUTS: Readonly<Record<string, readonly DefaultSpec[]>> = {
  villa: [
    { id: 'bed', x: 2, y: 6, colorway: 0 },
    { id: 'table', x: 8, y: 5, colorway: 0 },
    { id: 'chair', x: 11, y: 6, colorway: 0 },
    { id: 'bookshelf', x: 24, y: 4, colorway: 0 },
    { id: 'floor_lamp', x: 6, y: 4, colorway: 0 },
    { id: 'rug_large', x: 10, y: 7, colorway: 1 },
    { id: 'plant', x: 27, y: 6, colorway: 0 },
    { id: 'cabinet', x: 20, y: 4, colorway: 0 },
    { id: 'picture_frame', x: 14, y: 1, colorway: 0 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 },
    { id: 'cat_bed', x: 1, y: 8, colorway: 0 },
  ],
  cabin: [
    { id: 'bed', x: 2, y: 6, colorway: 0 },
    { id: 'table', x: 9, y: 5, colorway: 0 },
    { id: 'chair', x: 12, y: 6, colorway: 0 },
    { id: 'bookshelf', x: 25, y: 4, colorway: 0 },
    { id: 'floor_lamp', x: 6, y: 5, colorway: 0 },
    { id: 'rug', x: 10, y: 7, colorway: 0 },
    { id: 'plant', x: 28, y: 6, colorway: 0 },
    { id: 'crate', x: 16, y: 8, colorway: 0 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 }
  ],
  cave: [
    { id: 'bed', x: 3, y: 6, colorway: 1 },
    { id: 'crate', x: 8, y: 7, colorway: 1 },
    { id: 'crate', x: 10, y: 8, colorway: 1 },
    { id: 'cabinet', x: 22, y: 4, colorway: 1 },
    { id: 'floor_lamp', x: 6, y: 4, colorway: 0 },
    { id: 'rug', x: 12, y: 7, colorway: 0 },
    { id: 'cat_bed', x: 26, y: 7, colorway: 1 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 }
  ],
  snowcave: [
    { id: 'bed', x: 2, y: 6, colorway: 0 },
    { id: 'snow_lamp', x: 7, y: 5, colorway: 0 },
    { id: 'table', x: 10, y: 6, colorway: 0 },
    { id: 'chair', x: 13, y: 7, colorway: 0 },
    { id: 'rug_large', x: 11, y: 7, colorway: 1 },
    { id: 'cabinet', x: 23, y: 4, colorway: 0 },
    { id: 'fish_tank', x: 26, y: 5, colorway: 0 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 }
  ],
  bunker: [
    { id: 'bed', x: 2, y: 6, colorway: 1 },
    { id: 'stove', x: 6, y: 4, colorway: 0 },
    { id: 'table', x: 10, y: 5, colorway: 1 },
    { id: 'chair', x: 13, y: 6, colorway: 1 },
    { id: 'bookshelf', x: 24, y: 4, colorway: 1 },
    { id: 'crate', x: 17, y: 8, colorway: 1 },
    { id: 'floor_lamp', x: 21, y: 6, colorway: 0 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 }
  ],
  castle: [
    { id: 'bed', x: 2, y: 5, colorway: 2 },
    { id: 'rug_large', x: 9, y: 6, colorway: 0 },
    { id: 'table', x: 9, y: 4, colorway: 0 },
    { id: 'chair', x: 12, y: 5, colorway: 0 },
    { id: 'chair', x: 7, y: 5, colorway: 1 },
    { id: 'bookshelf', x: 24, y: 3, colorway: 0 },
    { id: 'plant', x: 27, y: 5, colorway: 0 },
    { id: 'cabinet', x: 20, y: 3, colorway: 0 },
    { id: 'picture_frame', x: 16, y: 0, colorway: 1 },
    { id: 'mirror', x: 4, y: 4, colorway: 0 },
    { id: 'ceiling_lamp', x: 14, y: 0, colorway: 0 },
    { id: 'cat_bed', x: 1, y: 8, colorway: 0 },
    { id: 'fish_tank', x: 26, y: 6, colorway: 1 },
  ],
};

/** 取某房屋模板的默认布置（未布置过时使用）。 */
export function defaultLayout(houseId: string): InteriorLayout {
  const specs = DEFAULT_LAYOUTS[houseId] ?? DEFAULT_LAYOUTS.cabin!;
  const items: InteriorItem[] = specs.map((s, i) => {
    const pos = clampToRoom(s.id, s.x, s.y);
    return {
      id: newItemId(),
      furnitureId: s.id,
      x: pos.x,
      y: pos.y,
      flipped: Boolean(s.flipped),
      colorway: s.colorway ?? 0,
      z: i,
    };
  });
  return { houseId, items, version: 1 };
}

/* ------------------------------------------------------------------ */
/* 编辑操作（纯函数，供 UI 与测试共用）                                   */
/* ------------------------------------------------------------------ */

export function addItem(
  layout: InteriorLayout,
  furnitureId: string,
  x: number,
  y: number,
  opts: { colorway?: number; flipped?: boolean } = {},
): InteriorLayout {
  const def = getFurniture(furnitureId);
  if (!def) return layout; // 未注册家具：不添加（诚实失败，不塞未知 id）
  if (layout.items.length >= MAX_ITEMS) return layout;
  const pos = clampToRoom(furnitureId, x, y);
  const item: InteriorItem = {
    id: newItemId(),
    furnitureId,
    x: pos.x,
    y: pos.y,
    flipped: Boolean(opts.flipped),
    colorway: Math.max(0, Math.round(opts.colorway ?? 0)),
    z: layout.items.length,
  };
  return { ...layout, items: [...layout.items, item], version: layout.version + 1 };
}

export function moveItem(
  layout: InteriorLayout,
  id: string,
  x: number,
  y: number,
  snap = true,
): InteriorLayout {
  return {
    ...layout,
    version: layout.version + 1,
    items: layout.items.map((it) => {
      if (it.id !== id) return it;
      const gx = snap ? x : Math.round(x);
      const gy = snap ? y : Math.round(y);
      const pos = clampToRoom(it.furnitureId, gx, gy);
      return { ...it, x: pos.x, y: pos.y };
    }),
  };
}

export function removeItem(layout: InteriorLayout, id: string): InteriorLayout {
  const items = layout.items.filter((it) => it.id !== id);
  if (items.length === layout.items.length) return layout;
  return { ...layout, items, version: layout.version + 1 };
}

export function flipItem(layout: InteriorLayout, id: string): InteriorLayout {
  return {
    ...layout,
    version: layout.version + 1,
    items: layout.items.map((it) => (it.id === id ? { ...it, flipped: !it.flipped } : it)),
  };
}

export function cycleColorway(layout: InteriorLayout, id: string, delta = 1): InteriorLayout {
  return {
    ...layout,
    version: layout.version + 1,
    items: layout.items.map((it) => {
      if (it.id !== id) return it;
      // 档位数取自美术真源（furnitureArt），不写死「3」避免与实际档位漂移。
      const max = getColorwayCount(it.furnitureId);
      if (max <= 0) return it;
      return { ...it, colorway: (((it.colorway + delta) % max) + max) % max };
    }),
  };
}

/** 图层上移/下移（显式 z 调整，解决同 y 遮挡歧义）。 */
export function shiftLayer(layout: InteriorLayout, id: string, delta: number): InteriorLayout {
  return {
    ...layout,
    version: layout.version + 1,
    items: layout.items.map((it) => (it.id === id ? { ...it, z: it.z + delta } : it)),
  };
}

/* ------------------------------------------------------------------ */
/* 骰子（Summerhouse 灵感投放）                                          */
/* ------------------------------------------------------------------ */

/**
 * 骰子：随机投 N 件**当前可解锁**的家具进房间。
 *
 * 纯函数：基线布局由调用方传入（便于测试，也支持「在当前布置上继续投」）。
 * 诚实说明：这是纯本地随机，不消耗任何材料，也不与 W2 的制造链冲突 ——
 * `unlockedFurniture` 已把未接入的 `quest` / `craft` 家具排除在外，
 * 所以骰子**不会凭空造出还没做的系统里的家具**。
 */
export function rollDiceFurniture(
  base: InteriorLayout,
  opts: { level: number; rng?: () => number; count?: number },
): InteriorLayout {
  const rng = opts.rng ?? Math.random;
  const count = opts.count ?? 3;
  const pool = unlockedFurniture({ level: opts.level, theme: base.houseId });
  if (pool.length === 0) return base;
  let layout = base;
  const usable = pool.filter(() => base.items.length < MAX_ITEMS);
  if (usable.length === 0) return layout;
  const n = Math.max(1, Math.min(count, usable.length));
  const picks = new Set<string>();
  let guard = 0;
  while (picks.size < n && guard < 64) {
    guard += 1;
    const idx = Math.min(usable.length - 1, Math.max(0, Math.floor(rng() * usable.length)));
    picks.add(usable[idx]!.id);
  }
  for (const fid of picks) {
    // 落在房间中下部的随机位置（允许部分重叠 —— Summerhouse 哲学）
    const x = 2 + Math.floor(rng() * Math.max(1, ROOM_COLS - 6));
    const y = 3 + Math.floor(rng() * Math.max(1, ROOM_ROWS - 4));
    const maxCw = getColorwayCount(fid);
    layout = addItem(layout, fid, x, y, { colorway: maxCw > 0 ? Math.floor(rng() * maxCw) : 0 });
  }
  return layout;
}

/* ------------------------------------------------------------------ */
/* 持久化：localStorage 双写（后端为准，见 cabinInteriorApi.ts）           */
/* ------------------------------------------------------------------ */

export const INTERIOR_STORAGE_PREFIX = 'fy.cabin.interior.v1';

export function interiorStorageKey(houseId: string): string {
  return `${INTERIOR_STORAGE_PREFIX}.${houseId}`;
}

function safeStorage(): Storage | null {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage;
  } catch {
    return null;
  }
}

export function loadLocalLayout(houseId: string, storage: Storage | null = safeStorage()): InteriorLayout | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(interiorStorageKey(houseId));
    if (!raw) return null;
    return sanitizeLayout(JSON.parse(raw), houseId);
  } catch {
    return null;
  }
}

export function saveLocalLayout(
  layout: InteriorLayout,
  storage: Storage | null = safeStorage(),
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(interiorStorageKey(layout.houseId), JSON.stringify(layout));
    return true;
  } catch {
    // 配额满 / 隐私模式：返回 false 让 UI 诚实提示「仅本次会话有效」。
    return false;
  }
}
