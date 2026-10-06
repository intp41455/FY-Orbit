import { Assets, Spritesheet, Texture, type SpritesheetData } from 'pixi.js';
import { FOREST_ATLAS_DATA, FOREST_CABIN_TMJ_DATA } from './forestAssetData';

/**
 * 数码小屋外部 CC0 像素图集与 Tiled 地图加载器。
 * 遵循像素纪律：
 * - scaleMode: 'nearest' 杜绝模糊
 * - 异步/同步双通道加载，支持单测环境与浏览器生产环境
 */

export interface TmjLayer {
  id: number;
  name: string;
  type: string;
  visible: boolean;
  opacity: number;
  width: number;
  height: number;
  x: number;
  y: number;
  data: number[];
  [key: string]: unknown;
}

export interface TmjTilesetRef {
  firstgid: number;
  name: string;
  tilewidth: number;
  tileheight: number;
  columns?: number;
  tilecount?: number;
  image?: string;
  imageheight?: number;
  imagewidth?: number;
  margin?: number;
  spacing?: number;
  [key: string]: unknown;
}

export interface TmjMap {
  width: number;
  height: number;
  tilewidth: number;
  tileheight: number;
  orientation: string;
  renderorder: string;
  layers: TmjLayer[];
  tilesets: TmjTilesetRef[];
  compressionlevel?: number;
  infinite?: boolean;
  nextlayerid?: number;
  nextobjectid?: number;
  tiledversion?: string;
  version?: string;
  [key: string]: unknown;
}

export interface LoadedCabinAtlas {
  readonly id: string;
  readonly sheet: Spritesheet;
  readonly baseTexture: Texture;
  getTileTexture(gidOrName: number | string): Texture | null;
}

const atlasCache = new Map<string, LoadedCabinAtlas>();
const mapCache = new Map<string, TmjMap>();

/**
 * 从 Texture + AtlasData 构造并解析 Spritesheet（支持注入或离线环境）。
 */
export async function createCabinAtlas(
  id: string,
  baseTexture: Texture,
  atlasData: SpritesheetData,
): Promise<LoadedCabinAtlas> {
  if (atlasCache.has(id)) {
    return atlasCache.get(id)!;
  }

  // 严格执行最近邻采样
  if (baseTexture?.source) {
    baseTexture.source.scaleMode = 'nearest';
  }
  const sheet = new Spritesheet(baseTexture, atlasData);
  await sheet.parse();

  const atlas: LoadedCabinAtlas = {
    id,
    sheet,
    baseTexture,
    getTileTexture(gidOrName: number | string): Texture | null {
      if (typeof gidOrName === 'string') {
        return sheet.textures[gidOrName] ?? null;
      }
      // Tiled GID 是 1-based，对应 tile_0000 偏移
      const index = gidOrName - 1;
      if (index < 0) return null;
      const key = `tile_${String(index).padStart(4, '0')}`;
      return sheet.textures[key] ?? null;
    },
  };

  atlasCache.set(id, atlas);
  return atlas;
}

/**
 * 在浏览器环境下通过 URL 异步加载 Spritesheet 图集。
 */
export async function loadCabinAtlasFromUrl(
  id: string,
  jsonUrl: string,
): Promise<LoadedCabinAtlas> {
  if (atlasCache.has(id)) {
    return atlasCache.get(id)!;
  }

  try {
    const loaded = await Assets.load<Spritesheet>(jsonUrl);
    if (loaded && loaded.textures) {
      if (loaded.textureSource) {
        loaded.textureSource.scaleMode = 'nearest';
      }
      const atlas: LoadedCabinAtlas = {
        id,
        sheet: loaded,
        baseTexture: loaded.textureSource ? Texture.from(loaded.textureSource) : Texture.WHITE,
        getTileTexture(gidOrName: number | string): Texture | null {
          if (typeof gidOrName === 'string') {
            return loaded.textures[gidOrName] ?? null;
          }
          const index = gidOrName - 1;
          if (index < 0) return null;
          const key = `tile_${String(index).padStart(4, '0')}`;
          return loaded.textures[key] ?? null;
        },
      };
      atlasCache.set(id, atlas);
      return atlas;
    }
  } catch (err) {
    console.warn(`[CabinAtlasLoader] Assets.load failed for ${jsonUrl}, attempting manual fetch:`, err);
  }

  // 手动 fetch 回退
  const res = await fetch(jsonUrl);
  const data = (await res.json()) as SpritesheetData;
  const imageName = data.meta?.image ?? 'forest_atlas.png';
  const imgUrl = new URL(imageName, new URL(jsonUrl, window.location.href)).href;
  const baseTex = await Assets.load<Texture>(imgUrl);
  if (baseTex.source) {
    baseTex.source.scaleMode = 'nearest';
  }
  return createCabinAtlas(id, baseTex, data);
}

/**
 * 加载并缓存 Tiled .tmj 地图数据。
 */
export async function loadTmjMap(mapUrl: string): Promise<TmjMap> {
  if (mapCache.has(mapUrl)) {
    return mapCache.get(mapUrl)!;
  }
  const res = await fetch(mapUrl);
  const json = (await res.json()) as TmjMap;
  mapCache.set(mapUrl, json);
  return json;
}

/**
 * 注册内置或预加载的地图数据（单测/离线友好）。
 */
export function registerTmjMap(id: string, mapData: TmjMap): void {
  mapCache.set(id, mapData);
}

export function getLoadedAtlas(id: string): LoadedCabinAtlas | undefined {
  return atlasCache.get(id);
}

// 预注册老林子内置地图
mapCache.set('forest_cabin', FOREST_CABIN_TMJ_DATA);

/**
 * 专为「老林子」样板加载 CC0 图集（浏览器异步加载真实 PNG / 离线单测使用内置数据）。
 */
export async function loadForestAtlas(): Promise<LoadedCabinAtlas> {
  if (atlasCache.has('forest')) {
    return atlasCache.get('forest')!;
  }

  // 1. 尝试浏览器环境下的 URL 加载
  if (typeof window !== 'undefined' && typeof window.location !== 'undefined') {
    try {
      const atlas = await loadCabinAtlasFromUrl('forest', '/assets/atlas/forest_atlas.json');
      return atlas;
    } catch {
      // 网络或测试环境降级到内置数据
    }
  }

  // 2. 离线/单测环境降级：使用 Texture.WHITE + FOREST_ATLAS_DATA
  return createCabinAtlas('forest', Texture.WHITE, FOREST_ATLAS_DATA);
}

/**
 * 获取「老林子」Tiled 地图数据
 */
export async function loadForestCabinMap(): Promise<TmjMap> {
  if (mapCache.has('forest_cabin')) {
    return mapCache.get('forest_cabin')!;
  }
  if (typeof window !== 'undefined' && typeof window.location !== 'undefined') {
    try {
      return await loadTmjMap('/assets/maps/forest_cabin.tmj');
    } catch {
      // 降级使用内置数据
    }
  }
  return FOREST_CABIN_TMJ_DATA;
}

export function clearAtlasCache(): void {
  for (const a of atlasCache.values()) {
    try {
      a.sheet.destroy(false);
    } catch {
      // ignore in tests
    }
  }
  atlasCache.clear();
  mapCache.clear();
  mapCache.set('forest_cabin', FOREST_CABIN_TMJ_DATA);
}

