import { Container } from 'pixi.js';
import { CompositeTilemap } from '@pixi/tilemap';
import type { TmjMap, LoadedCabinAtlas } from './cabinAtlasLoader';

/**
 * 瓦片图层渲染容器
 */
export interface RenderedTilemapLayers {
  readonly root: Container;
  readonly groundTilemap: CompositeTilemap;
  readonly structuresTilemap: CompositeTilemap;
  readonly foliageTilemap: CompositeTilemap;
  readonly layerMap: Map<string, CompositeTilemap>;
  readonly width: number;
  readonly height: number;
  destroy(): void;
}

/**
 * Autotile 规则接口：根据 3x3 邻域（九宫格）计算对应的图块 ID 或纹理别名
 */
export interface AutotileRule {
  resolveTile(
    grid: number[][],
    col: number,
    row: number,
    primaryId: number,
  ): number;
}

/**
 * 4-方向（上右下左）边缘 autotile 计算：
 * Bitmask:
 *   1 = North
 *   2 = East
 *   4 = South
 *   8 = West
 */
export function computeCardinalMask(
  grid: number[][],
  col: number,
  row: number,
  matchId: number,
): number {
  let mask = 0;
  const rows = grid.length;
  const cols = rows > 0 ? grid[0].length : 0;

  // North (row - 1)
  if (row > 0 && grid[row - 1][col] === matchId) mask |= 1;
  // East (col + 1)
  if (col < cols - 1 && grid[row][col + 1] === matchId) mask |= 2;
  // South (row + 1)
  if (row < rows - 1 && grid[row + 1][col] === matchId) mask |= 4;
  // West (col - 1)
  if (col > 0 && grid[row][col - 1] === matchId) mask |= 8;

  return mask;
}

/**
 * 根据 Tiled TMJ 地图与已加载的 CC0 图集，构建完整的瓦片分层渲染树。
 * 遵循像素纪律：
 * - 整数像素对齐
 * - @pixi/tilemap CompositeTilemap 批处理（彻底杜绝 32px 缝隙与 DrawCall 膨胀）
 */
export function buildTilemapFromTmj(
  tmj: TmjMap,
  atlas: LoadedCabinAtlas,
): RenderedTilemapLayers {
  const root = new Container();
  root.label = 'CabinTmjTilemapRoot';

  const groundTilemap = new CompositeTilemap();
  groundTilemap.label = 'Layer_Ground';

  const structuresTilemap = new CompositeTilemap();
  structuresTilemap.label = 'Layer_Structures';

  const foliageTilemap = new CompositeTilemap();
  foliageTilemap.label = 'Layer_Foliage';

  const layerMap = new Map<string, CompositeTilemap>([
    ['ground', groundTilemap],
    ['structures', structuresTilemap],
    ['foliage', foliageTilemap],
  ]);

  const tileW = tmj.tilewidth;
  const tileH = tmj.tileheight;

  for (const layer of tmj.layers) {
    if (!layer.visible || layer.opacity <= 0) continue;

    let targetTilemap = groundTilemap;
    const lowerName = layer.name.toLowerCase();
    if (lowerName.includes('struct') || lowerName.includes('building') || lowerName.includes('fringe')) {
      targetTilemap = structuresTilemap;
    } else if (lowerName.includes('foliage') || lowerName.includes('tree') || lowerName.includes('upper')) {
      targetTilemap = foliageTilemap;
    }

    const data = layer.data;
    const cols = layer.width;
    const rows = layer.height;

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const gid = data[r * cols + c];
        if (!gid || gid <= 0) continue;

        const tex = atlas.getTileTexture(gid);
        if (!tex) continue;

        const x = Math.round(c * tileW);
        const y = Math.round(r * tileH);
        targetTilemap.tile(tex, x, y);
      }
    }
  }

  root.addChild(groundTilemap);
  root.addChild(structuresTilemap);
  root.addChild(foliageTilemap);

  return {
    root,
    groundTilemap,
    structuresTilemap,
    foliageTilemap,
    layerMap,
    width: tmj.width * tileW,
    height: tmj.height * tileH,
    destroy() {
      groundTilemap.clear();
      structuresTilemap.clear();
      foliageTilemap.clear();
      root.destroy({ children: true });
    },
  };
}

/**
 * 动态瓦片网格 Autotile 填充（用于无 TMJ 地图时的程序化地块渲染）
 */
export function populateTilemapGrid(
  tilemap: CompositeTilemap,
  grid: (number | string)[][],
  atlas: LoadedCabinAtlas,
  tileW: number = 32,
  tileH: number = 32,
  offsetX: number = 0,
  offsetY: number = 0,
): void {
  const rows = grid.length;
  if (rows === 0) return;
  const cols = grid[0].length;

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const tileVal = grid[r][c];
      if (tileVal === 0 || tileVal === '') continue;

      const tex = atlas.getTileTexture(tileVal);
      if (!tex) continue;

      const x = Math.round(offsetX + c * tileW);
      const y = Math.round(offsetY + r * tileH);
      tilemap.tile(tex, x, y);
    }
  }
}
