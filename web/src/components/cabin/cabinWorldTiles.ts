/**
 * B10 · 大世界瓦片绘制与地形系统（数据真源对齐 world.py 160×100）。
 *
 * 1. 瓦片规格：严格遵循 A1 冻结的 TILE = 32px，总尺寸 160 × 100 格（5120 × 3200 像素）。
 * 2. 12 种地形类型（对齐 TERRAIN_IDS）：
 *    grass, flower, moss, fog, ruin, sand, path, water, tree, rock, cliff, deep
 * 3. 性能护栏（R2）：
 *    - 纹理级别单例缓存（12 种地形 × 32×32，启动期或按需生成一次，零运行时重复分配）；
 *    - 视口裁剪（culling）：仅实例化和更新屏幕内的可见格（~21×12 格），保障渲染 ≤ 6ms。
 */

import { TILE, VIRTUAL_W, VIRTUAL_H } from './cabinConfig';
import { PixelBuffer, bufferToTexture, type PixelTexture } from './cabinPixels';
import { TERRAIN_IDS, type TerrainId, type TileWindow } from './gameplay/worldApi';

export { TILE };
export const MAP_COLS = 160;
export const MAP_ROWS = 100;
export const MAP_WIDTH_PX = MAP_COLS * TILE; // 5120
export const MAP_HEIGHT_PX = MAP_ROWS * TILE; // 3200

/** 12 种地形的基础配色与微结构定义（32×32 像素图元） */
interface TerrainVisualSpec {
  baseColor: number;
  accentColor: number;
  highlightColor: number;
  shadowColor: number;
  patternType: 'stipple' | 'brick' | 'wave' | 'dense' | 'mist' | 'crystal';
}

export const TERRAIN_VISUALS: Record<TerrainId, TerrainVisualSpec> = {
  grass: {
    baseColor: 0x4a7c59,
    accentColor: 0x3d6b49,
    highlightColor: 0x5c996b,
    shadowColor: 0x2e5237,
    patternType: 'stipple',
  },
  flower: {
    baseColor: 0x52885d,
    accentColor: 0xf48fb1,
    highlightColor: 0xffd54f,
    shadowColor: 0x375e3e,
    patternType: 'stipple',
  },
  moss: {
    baseColor: 0x3c644a,
    accentColor: 0x2e4e3a,
    highlightColor: 0x4d7c5c,
    shadowColor: 0x223829,
    patternType: 'stipple',
  },
  fog: {
    baseColor: 0x6e8a8a,
    accentColor: 0x8aa8a8,
    highlightColor: 0xa4c2c2,
    shadowColor: 0x546e6e,
    patternType: 'mist',
  },
  ruin: {
    baseColor: 0x736b63,
    accentColor: 0x5a524b,
    highlightColor: 0x8a8279,
    shadowColor: 0x423c36,
    patternType: 'brick',
  },
  sand: {
    baseColor: 0xd2b57e,
    accentColor: 0xc1a268,
    highlightColor: 0xe2c792,
    shadowColor: 0xa88c52,
    patternType: 'stipple',
  },
  path: {
    baseColor: 0x8b8589,
    accentColor: 0x716b6f,
    highlightColor: 0xa39da1,
    shadowColor: 0x555054,
    patternType: 'brick',
  },
  water: {
    baseColor: 0x3377aa,
    accentColor: 0x28638f,
    highlightColor: 0x5599cc,
    shadowColor: 0x1d4a6d,
    patternType: 'wave',
  },
  tree: {
    baseColor: 0x2a5438,
    accentColor: 0x1d3d28,
    highlightColor: 0x3d704d,
    shadowColor: 0x122619,
    patternType: 'dense',
  },
  rock: {
    baseColor: 0x5f6769,
    accentColor: 0x484e50,
    highlightColor: 0x798284,
    shadowColor: 0x323738,
    patternType: 'dense',
  },
  cliff: {
    baseColor: 0x4a4340,
    accentColor: 0x36302e,
    highlightColor: 0x635b57,
    shadowColor: 0x25201e,
    patternType: 'dense',
  },
  deep: {
    baseColor: 0x1c3b5e,
    accentColor: 0x142b45,
    highlightColor: 0x274f7a,
    shadowColor: 0x0c1a2b,
    patternType: 'wave',
  },
};

/** 纹理单例缓存（theme + terrainId → PixelTexture） */
const TERRAIN_TEXTURE_CACHE = new Map<string, PixelTexture>();

/**
 * 绘制单个 32×32 像素瓦片纹理。
 */
export function createTerrainTileTexture(terrain: TerrainId, theme: string = 'forest'): PixelTexture {
  const cacheKey = `${theme}:${terrain}`;
  const cached = TERRAIN_TEXTURE_CACHE.get(cacheKey);
  if (cached) return cached;

  const spec = TERRAIN_VISUALS[terrain] ?? TERRAIN_VISUALS.grass;
  const buf = new PixelBuffer(TILE, TILE);

  // 1. 基底填充
  buf.rect(0, 0, TILE, TILE, spec.baseColor, 1);

  // 2. 根据纹理类型绘制精致像素风纹理
  const { patternType, accentColor, highlightColor, shadowColor } = spec;

  if (patternType === 'stipple') {
    // 杂点质感（草地、花草、沙地、苔藓）
    for (let y = 0; y < TILE; y += 1) {
      for (let x = 0; x < TILE; x += 1) {
        const hash = (x * 37 + y * 19 + terrain.charCodeAt(0)) % 100;
        if (hash < 12) {
          buf.setPx(x, y, highlightColor, 0.7);
        } else if (hash < 25) {
          buf.setPx(x, y, shadowColor, 0.6);
        } else if (terrain === 'flower' && (hash === 42 || hash === 77 || hash === 88)) {
          // 花朵点缀
          buf.setPx(x, y, accentColor, 1);
          if (x + 1 < TILE) buf.setPx(x + 1, y, highlightColor, 0.9);
        }
      }
    }
    // 1px 细微网格边界暗线
    for (let i = 0; i < TILE; i += 1) {
      buf.setPx(i, 0, shadowColor, 0.15);
      buf.setPx(0, i, shadowColor, 0.15);
    }
  } else if (patternType === 'brick') {
    // 石砖/遗迹砌块（4×4 或 8×4 石砖结构）
    for (let y = 0; y < TILE; y += 1) {
      const isHJoint = y % 8 === 0;
      for (let x = 0; x < TILE; x += 1) {
        const row = Math.floor(y / 8);
        const shift = (row % 2) * 8;
        const isVJoint = (x + shift) % 16 === 0;
        if (isHJoint || isVJoint) {
          buf.setPx(x, y, shadowColor, 0.85);
        } else if ((y % 8 === 1 && !isVJoint) || ((x + shift) % 16 === 1 && !isHJoint)) {
          buf.setPx(x, y, highlightColor, 0.45);
        } else if ((x + y) % 9 === 0) {
          buf.setPx(x, y, accentColor, 0.35);
        }
      }
    }
  } else if (patternType === 'wave') {
    // 水流涟漪
    for (let y = 0; y < TILE; y += 1) {
      for (let x = 0; x < TILE; x += 1) {
        const wave = Math.sin(x * 0.4 + y * 0.3) * 4;
        if (Math.abs((y % 8) - wave) < 1.2) {
          buf.setPx(x, y, highlightColor, 0.65);
        } else if (Math.abs((y % 8) - wave) > 5) {
          buf.setPx(x, y, shadowColor, 0.55);
        }
      }
    }
  } else if (patternType === 'dense') {
    // 密林 / 乱石 / 崖壁密集立体阴影
    for (let y = 0; y < TILE; y += 1) {
      for (let x = 0; x < TILE; x += 1) {
        const d = (x * 13 + y * 29) % 31;
        if (d < 8) buf.setPx(x, y, shadowColor, 0.85);
        else if (d > 24) buf.setPx(x, y, highlightColor, 0.5);
        else if (d % 3 === 0) buf.setPx(x, y, accentColor, 0.4);
      }
    }
  } else if (patternType === 'mist') {
    // 迷雾柔光
    for (let y = 0; y < TILE; y += 1) {
      for (let x = 0; x < TILE; x += 1) {
        const v = Math.cos(x * 0.2) * Math.sin(y * 0.2);
        buf.setPx(x, y, v > 0 ? highlightColor : shadowColor, 0.35);
      }
    }
  }

  const texture = bufferToTexture(buf, { label: `tile-${cacheKey}` });
  TERRAIN_TEXTURE_CACHE.set(cacheKey, texture);
  return texture;
}

/**
 * 确定性地形图生成器（160×100 格）。
 * 遵循与后端 world.py generate_map 完全一致的算法思想与尺寸，保证 16000 格严密排布。
 */
export function generateDeterministicWorldGrid(
  owner: string = 'player',
  theme: string = 'forest',
): {
  cols: number;
  rows: number;
  tiles: TerrainId[][];
  histogram: Record<TerrainId, number>;
} {
  // 基于 owner 与 theme 的 Mulberry32 伪随机种子
  let seed = 0;
  for (let i = 0; i < owner.length; i += 1) seed = (seed * 31 + owner.charCodeAt(i)) >>> 0;
  for (let i = 0; i < theme.length; i += 1) seed = (seed * 17 + theme.charCodeAt(i)) >>> 0;
  if (seed === 0) seed = 123456789;

  function prng(): number {
    seed = (seed + 0x6d2b79f5) >>> 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  const cols = MAP_COLS;
  const rows = MAP_ROWS;
  const grid: TerrainId[][] = [];
  const histogram = {} as Record<TerrainId, number>;
  for (const tid of TERRAIN_IDS) histogram[tid] = 0;

  for (let r = 0; r < rows; r += 1) {
    const row: TerrainId[] = [];
    for (let c = 0; c < cols; c += 1) {
      const roll = prng();
      let tid: TerrainId = 'grass';
      if (theme === 'forest') {
        if (roll < 0.55) tid = 'grass';
        else if (roll < 0.70) tid = 'flower';
        else if (roll < 0.82) tid = 'moss';
        else if (roll < 0.90) tid = 'path';
        else if (roll < 0.95) tid = 'water';
        else if (roll < 0.98) tid = 'tree';
        else tid = 'rock';
      } else if (theme === 'magic') {
        if (roll < 0.40) tid = 'flower';
        else if (roll < 0.65) tid = 'moss';
        else if (roll < 0.80) tid = 'fog';
        else if (roll < 0.90) tid = 'ruin';
        else tid = 'deep';
      } else if (theme === 'scifi') {
        if (roll < 0.45) tid = 'sand';
        else if (roll < 0.70) tid = 'ruin';
        else if (roll < 0.85) tid = 'rock';
        else tid = 'cliff';
      } else {
        if (roll < 0.50) tid = 'grass';
        else if (roll < 0.70) tid = 'sand';
        else if (roll < 0.85) tid = 'path';
        else if (roll < 0.95) tid = 'water';
        else tid = 'rock';
      }
      row.push(tid);
      histogram[tid] += 1;
    }
    grid.push(row);
  }

  return { cols, rows, tiles: grid, histogram };
}

/**
 * 视口裁剪工具：计算在给定镜头虚拟坐标下，应渲染哪些瓦片。
 */
export function getVisibleTileBounds(
  cameraX: number,
  cameraY: number = 0,
  viewW: number = VIRTUAL_W,
  viewH: number = VIRTUAL_H,
): TileWindow {
  const col0 = Math.max(0, Math.floor(cameraX / TILE));
  const col1 = Math.min(MAP_COLS - 1, Math.floor((cameraX + viewW) / TILE) + 1);
  const row0 = Math.max(0, Math.floor(cameraY / TILE));
  const row1 = Math.min(MAP_ROWS - 1, Math.floor((cameraY + viewH) / TILE) + 1);
  return { col0, col1, row0, row1 };
}
