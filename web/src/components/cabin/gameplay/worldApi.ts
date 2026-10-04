/**
 * B10 · 大世界前端数据消费层。
 *
 * 与 `src/find_yourself/services/cabin_life/world.py`（后端唯一真源）一一对应。
 * 本文件只做 **类型镜像 + 像素换算 + 展示派生**，**不重算任何规则**：
 * 地形、宝箱位置、事件结果全部由后端算好后下发。
 *
 * 关于常量（重要）：
 *   `TILE` / `VIRTUAL_W` / `VIRTUAL_H` / `WORLD` 由 A1 冻结在
 *   `./cabinConfig`；本文件**只 import 引用，不重新定义数值**，
 *   以免出现两处 TILE 各自漂移（那会让碰撞判定与渲染差一格）。
 *   后端 `world.py` 用字面量 5120/32 复刻同一组值，并有单测钉死
 *   `MAP_COLS === WORLD.width / TILE`。
 *
 * 渲染段（镜头跟随 / 无缝滚动 / 瓦片绘制）按 §2.5 串行点要改 `cabinScene.ts`，
 * 等 A5 收工后再做；这里只提供渲染要消费的**纯函数**：
 *   `cameraXFor`（镜头目标 x）、`tileRect`（格 → 矩形）、`visibleTiles`
 *   （视口内应绘制的格范围，供瓦片图集裁剪）。
 */

import { TILE, WORLD } from '../cabinConfig';

/** 与后端 `world.TERRAIN` 的 id 集合对齐。 */
export const TERRAIN_IDS = [
  'grass',
  'flower',
  'moss',
  'fog',
  'ruin',
  'sand',
  'path',
  'water',
  'tree',
  'rock',
  'cliff',
  'deep',
] as const;

export type TerrainId = (typeof TERRAIN_IDS)[number];

export function isTerrainId(value: unknown): value is TerrainId {
  return typeof value === 'string' && (TERRAIN_IDS as readonly string[]).includes(value);
}

/** 地形中文名（后端已下发，此处只做兜底渲染，不做二次翻译）。 */
export const TERRAIN_FALLBACK_LABELS: Record<TerrainId, string> = {
  grass: '草地',
  flower: '花地',
  moss: '苔地',
  fog: '迷雾',
  ruin: '旧遗迹',
  sand: '浅滩',
  path: '石板路',
  water: '溪水',
  tree: '密林',
  rock: '乱石',
  cliff: '崖壁',
  deep: '深水',
};

export interface WorldBlock {
  theme: string;
  owner: string;
  /** 横向格数（= WORLD.width / TILE，160） */
  cols: number;
  /** 纵向格数（100） */
  rows: number;
  /** 一格边长（虚拟像素），对齐 A1 冻结的 TILE */
  tile: number;
  width_px: number;
  height_px: number;
  /** 出生点格坐标 */
  spawn: [number, number];
  /** 可走格占比 0..1 */
  walkable_ratio: number;
  /** 从出生点可达的格数 */
  reachable_from_spawn: number;
  histogram: Record<string, number>;
}

export interface SecretArea {
  id: string;
  theme: string;
  tile: [number, number];
  radius: number;
  label: string;
  terrain_id: string;
}

export type ChestTier = 't1' | 't2' | 't3';

export interface Chest {
  id: string;
  theme: string;
  tier: ChestTier;
  tile: [number, number];
  secret_id: string;
  label: string;
  /** null = 无门槛；'key' 需钥匙；'tool' 需工具 */
  needs: string | null;
  coins: [number, number];
  materials: number;
}

export interface EventOutcome {
  event_id: string;
  label: string;
  line: string;
  /** 形如 `[['firefly_jar', 2]]` */
  materials: [string, number][];
  coins: number;
  /** true = 诚实态「一无所获」，前端**不要**补一个安慰性图标 */
  empty: boolean;
}

export interface WorldFixture {
  world: WorldBlock;
  secrets: SecretArea[];
  chests: Chest[];
  events: EventOutcome[];
  terrain_labels: Record<string, string>;
  chest_tiers: Record<string, { label: string; needs: string | null; coins: number[]; materials: number }>;
  chest_counts: Record<string, number[]>;
  empty_event_ids: string[];
  empty_pct: number;
  grade: [string, string];
  rows: Record<string, string[]>;
}

/* ------------------------------------------------------------------ */
/* 像素换算：格 ↔ 虚拟像素                                               */
/* ------------------------------------------------------------------ */

/** 格坐标 → 虚拟像素 x（乘 A1 冻结的 `TILE`，不在这里另写 32）。 */
export function tileToPixelX(col: number): number {
  return col * TILE;
}

/** 虚拟像素 x → 格坐标（向下取整）。 */
export function pixelToTileCol(px: number): number {
  return Math.floor(px / TILE);
}

/** 一个格在虚拟坐标系里的矩形（供 Pixi 绘制 / 命中测试）。 */
export function tileRect(tile: [number, number]): {
  x: number;
  y: number;
  width: number;
  height: number;
} {
  return {
    x: tile[0] * TILE,
    y: tile[1] * TILE,
    width: TILE,
    height: TILE,
  };
}

/** 距离用曼哈顿格数（与后端 `INTERACT_RANGE_TILES` 同一口径）。 */
export function manhattan(a: [number, number], b: [number, number]): number {
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
}

/* ------------------------------------------------------------------ */
/* 视口裁剪：无缝滚动时只绘制屏内格                                     */
/* ------------------------------------------------------------------ */

export interface TileWindow {
  col0: number;
  col1: number;
  row0: number;
  row1: number;
}

/**
 * 给定镜头 x / y 与视口宽高（**虚拟像素**），算出该画哪些格。
 *
 * 上下界**闭区间**（含 end），因为格是闭区间像素块：
 * 相机 x=0、视口 640 → 应绘 col 0..20（共 21 格 = 672px，覆盖右缘半格）。
 */
export function visibleTiles(
  cameraX: number,
  cameraY: number,
  viewWidth: number,
  viewHeight: number,
  world: WorldBlock,
): TileWindow {
  const col0 = Math.max(0, pixelToTileCol(cameraX));
  const col1 = Math.min(world.cols - 1, pixelToTileCol(cameraX + viewWidth));
  const row0 = Math.max(0, pixelToTileCol(cameraY));
  const row1 = Math.min(world.rows - 1, pixelToTileCol(cameraY + viewHeight));
  return { col0, col1, row0, row1 };
}

/** 视口内格数（含边）。渲染预算自查：> 屏数 × (行数+2) 说明算法退化了。 */
export function visibleTileCount(w: TileWindow): number {
  return Math.max(0, w.col1 - w.col0 + 1) * Math.max(0, w.row1 - w.row0 + 1);
}

/**
 * 镜头目标 x：人物居中并钳在世界里。
 * **不复刻** A1 的 `cameraTargetX` —— 直接用它，本文件不重算。
 */
export function cameraXFor(personWorldX: number, viewWidth: number): number {
  return clampCameraX(personWorldX - viewWidth * WORLD.cameraLead, viewWidth);
}

/** 镜头目标 y（纵向尚无 A1 冻结的钳制规则，这里只保证不越上下界）。 */
export function cameraYFor(personWorldY: number, viewHeight: number, world: WorldBlock): number {
  const maxY = Math.max(0, world.height_px - viewHeight);
  return Math.min(maxY, Math.max(0, personWorldY - viewHeight / 2));
}

function clampCameraX(cameraX: number, viewWidth: number): number {
  const maxX = Math.max(0, WORLD.width - viewWidth);
  return Math.min(maxX, Math.max(0, cameraX));
}

/* ------------------------------------------------------------------ */
/* 展示派生                                                              */
/* ------------------------------------------------------------------ */

export function terrainLabel(id: string, labels?: Record<string, string>): string {
  return labels?.[id] ?? (isTerrainId(id) ? TERRAIN_FALLBACK_LABELS[id] : '未知地形');
}

/** 地图尺寸一行文案（如「160 × 100 格 · 5120 × 3200」）。 */
export function mapSizeLine(world: WorldBlock): string {
  return `${world.cols} × ${world.rows} 格 · ${world.width_px} × ${world.height_px}`;
}

/** 宝箱可走概率提示：`coins` 是区间，展示「约 30~80 金」。 */
export function chestValueLine(chest: Chest): string {
  const [lo, hi] = chest.coins;
  const coin = lo === hi ? `${lo}` : `${lo}~${hi}`;
  const gate = chest.needs === 'key' ? ' · 需钥匙' : chest.needs === 'tool' ? ' · 需工具' : '';
  return `${coin} 金 · ${chest.materials} 份材料${gate}`;
}

/** 已发现 / 未发现的宝箱列表（渲染只按「已发现」画图标，其余绝不预画）。 */
export function visibleChests(chests: Chest[], foundIds: Iterable<string>): Chest[] {
  const found = new Set(foundIds);
  return chests.filter((c) => found.has(c.id));
}

/** 附近可提示的秘密区域（曼哈顿 ≤ radius）。 */
export function nearbySecrets(
  secrets: SecretArea[],
  player: [number, number],
): SecretArea[] {
  return secrets.filter((s) => manhattan(s.tile, player) <= s.radius);
}

/** 探索度评语（后端已给出 grade，前端只做兜底）。 */
export function gradeOf(
  ratio: number,
  fallback: [string, string] = ['未知', '这片地方你还没走。'],
): [string, string] {
  if (!Number.isFinite(ratio) || ratio < 0) return fallback;
  if (ratio < 0.05) return ['初来乍到', '这片地方你才刚看见。'];
  if (ratio < 0.2) return ['转了转', '几条常走的路已经熟了。'];
  if (ratio < 0.45) return ['熟门熟路', '你开始记得哪块石头会绊脚。'];
  if (ratio < 0.75) return ['四处走走', '地图在你脑子里有了大致的形状。'];
  return ['无所不知', '这片土地上没你不知道的角落。'];
}

/** 事件列表一行文案；空手事件**如实**显示「没有」而不是编一个收获。 */
export function eventLine(outcome: EventOutcome): string {
  if (outcome.materials.length === 0 && outcome.coins === 0) {
    return `${outcome.label}：${outcome.line}`;
  }
  const parts = outcome.materials.map(([id, qty]) => `${id}×${qty}`);
  if (outcome.coins > 0) parts.unshift(`${outcome.coins} 金`);
  return `${outcome.label}：${outcome.line}（${parts.join('、')}）`;
}

/** 地形直方图 → 「草地 55% · 深水 1% · 其余 0%」，只列前 n 项。 */
export function terrainShareLines(
  histogram: Record<string, number>,
  labels?: Record<string, string>,
  top = 4,
): string[] {
  const entries = Object.entries(histogram)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((acc, [, n]) => acc + n, 0);
  if (total === 0) return [];
  return entries
    .slice(0, top)
    .map(([id, n]) => `${terrainLabel(id, labels)} ${Math.round((n * 100) / total)}%`);
}
