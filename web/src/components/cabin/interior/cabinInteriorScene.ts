import {
  Application,
  Container,
  Graphics,
  Sprite,
  Text,
  Texture,
  TilingSprite,
} from 'pixi.js';
import {
  PixelBuffer,
  bufferToTexture,
  mulberry32,
  spriteFromMatrix,
  type PixelPalette,
  type PixelTexture,
} from '../cabinPixels';
import {
  PERSON_PALETTE,
  PERSON_WALK_FRAMES,
  PET_WALK_FRAMES,
  hexToNumber,
  lighten,
  petPalette,
  shade,
} from '../cabinPixelArt';
import type { CabinHouseId } from '../cabinConfig';
import { resolveViewport } from '../cabinViewport';
import { getFurniture } from './furnitureCatalog';
import { assertFurnitureArtComplete, getColorway, getFurnitureRows } from './furnitureArt';
import {
  FLOOR_Y,
  GRID,
  INTERIOR_HEIGHT,
  INTERIOR_WIDTH,
  depthKey,
  type InteriorItem,
  type InteriorLayout,
} from './interiorLayout';
import {
  getLoadedTinySwordsAssets,
  loadTinySwordsAssets,
  getPawnFrames,
  getSheepFrames,
  type TinySwordsAssets,
} from '../cabinTinySwordsArt';

/**
 * W1 · 室内场景渲染层（PixiJS）
 *
 * 视觉基准 = 11 号文档 §3.1 的 7 条硬标准，逐条对应：
 *  1. ≥3 层视差 + 前景细节层 → 墙景 / 地板 / 家具与角色 / 前景暗角 四层；
 *  2. 每房屋风格独立色板 → `ROOM_THEMES`（含光语言）；
 *  3. 光源 ≥3 种 → 天光加色 + 窗光点光源（呼吸闪烁）+ 落位点亮自发光；
 *  4. 粒子 ≥2 类语义 → 室内浮尘 + 窗前光斑；
 *  5. 小人 ≥16×24、两帧行走、椭圆地面影 → 复用 cabinPixelArt 帧；
 *  6. 落位「点亮」演出 ≥1s → `playPlaceGlow`（GLOW_MS = 1100ms）；
 *  7. nearest 采样 + 坐标取整（`roundPixels` + 整数赋值）。
 *
 * 遮挡：每帧按 `depthKey`（y+h → z → id）重排渲染顺序，家具按伪深度遮挡小人。
 *
 * 诚实说明：这是渲染层。jsdom 下 PixiJS 不可用，测试整体 mock 本模块
 * （与 CabinStage 同策略），可测逻辑全部留在 interiorLayout.ts。
 */

/* ------------------------------------------------------------------ */
/* 室内主题（每房屋风格一套色板 + 光语言）                                */
/* ------------------------------------------------------------------ */

export interface RoomTheme {
  readonly id: CabinHouseId;
  readonly label: string;
  /** 墙面垂直渐变（自上而下）。 */
  readonly wall: readonly { t: number; color: number }[];
  readonly floor: number;
  readonly floorDark: number;
  /** 窗光颜色（点光源 + 呼吸）。 */
  readonly windowLight: number;
  /** 环境天光（整体加色）。 */
  readonly ambient: number;
  /** 门框 / 护墙板 / 踢脚线。 */
  readonly trim: number;
}

const ROOM_THEMES: Record<CabinHouseId, RoomTheme> = {
  villa: {
    id: 'villa',
    label: '别墅',
    wall: [
      { t: 0, color: hexToNumber('#f3e7d8') },
      { t: 0.55, color: hexToNumber('#e6d3bd') },
      { t: 1, color: hexToNumber('#d4bda3') },
    ],
    floor: hexToNumber('#c9a173'),
    floorDark: hexToNumber('#a67c50'),
    windowLight: hexToNumber('#fff0c8'),
    ambient: hexToNumber('#ffe9c0'),
    trim: hexToNumber('#8d6748'),
  },
  cabin: {
    id: 'cabin',
    label: '小木屋',
    wall: [
      { t: 0, color: hexToNumber('#e8d3b0') },
      { t: 0.6, color: hexToNumber('#d4b489') },
      { t: 1, color: hexToNumber('#b8926a') },
    ],
    floor: hexToNumber('#b5854f'),
    floorDark: hexToNumber('#8d6748'),
    windowLight: hexToNumber('#ffe2a8'),
    ambient: hexToNumber('#ffdca8'),
    trim: hexToNumber('#6b4a33'),
  },
  cave: {
    id: 'cave',
    label: '山洞',
    wall: [
      { t: 0, color: hexToNumber('#4a4458') },
      { t: 0.6, color: hexToNumber('#3b3648') },
      { t: 1, color: hexToNumber('#2c2838') },
    ],
    floor: hexToNumber('#5b5468'),
    floorDark: hexToNumber('#413c50'),
    windowLight: hexToNumber('#9fd8e8'),
    ambient: hexToNumber('#7f9fc8'),
    trim: hexToNumber('#332e42'),
  },
  snowcave: {
    id: 'snowcave',
    label: '雪洞',
    wall: [
      { t: 0, color: hexToNumber('#dceaf6') },
      { t: 0.6, color: hexToNumber('#c2d6e8') },
      { t: 1, color: hexToNumber('#a3bcd4') },
    ],
    floor: hexToNumber('#b8cfe0'),
    floorDark: hexToNumber('#93aec4'),
    windowLight: hexToNumber('#eaf6ff'),
    ambient: hexToNumber('#cfe6ff'),
    trim: hexToNumber('#7e9cb4'),
  },
  bunker: {
    id: 'bunker',
    label: '地堡',
    wall: [
      { t: 0, color: hexToNumber('#5a5f68') },
      { t: 0.6, color: hexToNumber('#474c55') },
      { t: 1, color: hexToNumber('#383c44') },
    ],
    floor: hexToNumber('#4e535c'),
    floorDark: hexToNumber('#3a3e46'),
    windowLight: hexToNumber('#ffc98a'),
    ambient: hexToNumber('#c8a070'),
    trim: hexToNumber('#2e323a'),
  },
  castle: {
    id: 'castle',
    label: '城堡',
    wall: [
      { t: 0, color: hexToNumber('#cfd4e4') },
      { t: 0.55, color: hexToNumber('#b4bcd4') },
      { t: 1, color: hexToNumber('#98a2c0') },
    ],
    floor: hexToNumber('#9aa2b8'),
    floorDark: hexToNumber('#7a8298'),
    windowLight: hexToNumber('#dfe8ff'),
    ambient: hexToNumber('#b8c4e8'),
    trim: hexToNumber('#5f6880'),
  },
};

export function getRoomTheme(houseId: CabinHouseId): RoomTheme {
  return ROOM_THEMES[houseId] ?? ROOM_THEMES.cabin;
}

/* ------------------------------------------------------------------ */
/* 纹理工厂                                                            */
/* ------------------------------------------------------------------ */

const roomTexCache = new Map<string, PixelTexture>();

function toTexture(buf: PixelBuffer, label: string): PixelTexture {
  return bufferToTexture(buf, { label });
}

/** 墙：高精实木梁架 + 护墙板 + 细致木纹（静态预渲染一次）。 */
function buildWallTexture(theme: RoomTheme): PixelTexture {
  const key = `wall:${theme.id}`;
  const hit = roomTexCache.get(key);
  if (hit) return hit;
  const buf = new PixelBuffer(INTERIOR_WIDTH, FLOOR_Y);
  buf.vGradient(0, 0, INTERIOR_WIDTH, FLOOR_Y, theme.wall, 16);

  // 1. 顶部天花板主梁 (Ceiling Timber Beam)
  buf.rect(0, 0, INTERIOR_WIDTH, 8, shade(theme.trim, 1.25));
  buf.rect(0, 7, INTERIOR_WIDTH, 1, lighten(theme.trim, 1.25), 0.65);
  buf.rect(0, 8, INTERIOR_WIDTH, 3, 0x120c18, 0.28);

  // 2. 上部实木壁板横向长条木纹 (Shiplap Horizontal Planks)
  const railY = Math.round(FLOOR_Y * 0.62); // ~59
  for (let y = 10; y < railY; y += 12) {
    buf.rect(0, y, INTERIOR_WIDTH, 1, shade(theme.wall[1]!.color, 0.94), 0.55);
    buf.rect(0, y + 1, INTERIOR_WIDTH, 1, lighten(theme.wall[0]!.color, 1.08), 0.35);
  }
  const prng = mulberry32(theme.id.length * 3721 + 7);
  for (let x = 4; x < INTERIOR_WIDTH; x += 16) {
    const rx = x + Math.floor((prng() - 0.5) * 6);
    buf.rect(rx, 10, 1, railY - 10, shade(theme.wall[1]!.color, 0.96), 0.35);
  }

  // 3. 腰线雕花装饰护墙带 (Carved Chair Rail Moulding)
  buf.rect(0, railY - 2, INTERIOR_WIDTH, 1, lighten(theme.trim, 1.35), 0.7);
  buf.rect(0, railY - 1, INTERIOR_WIDTH, 3, theme.trim, 0.95);
  buf.rect(0, railY + 2, INTERIOR_WIDTH, 1, shade(theme.trim, 1.4), 0.8);
  buf.rect(0, railY + 3, INTERIOR_WIDTH, 2, 0x100812, 0.25);

  // 4. 下半部复古竖向护壁板 (Vertical Beadboard Wainscoting)
  for (let x = 0; x < INTERIOR_WIDTH; x += 8) {
    buf.rect(x, railY + 3, 1, FLOOR_Y - railY - 7, shade(theme.trim, 1.2), 0.45);
    buf.rect(x + 1, railY + 3, 1, FLOOR_Y - railY - 7, lighten(theme.trim, 1.15), 0.25);
  }

  // 5. 底部厚实踢脚线 (Sturdy Baseboard)
  buf.rect(0, FLOOR_Y - 6, INTERIOR_WIDTH, 1, lighten(theme.trim, 1.35), 0.6);
  buf.rect(0, FLOOR_Y - 5, INTERIOR_WIDTH, 5, theme.trim, 0.95);
  buf.rect(0, FLOOR_Y - 1, INTERIOR_WIDTH, 1, shade(theme.trim, 1.5), 0.85);

  const tex = toTexture(buf, `room-wall-${theme.id}`);
  roomTexCache.set(key, tex);
  return tex;
}

/** 地板：高精错缝拼花实木地板 + 倒角高光 + 木节年轮（横向无缝平铺）。 */
function buildFloorTexture(theme: RoomTheme): PixelTexture {
  const key = `floor:${theme.id}`;
  const hit = roomTexCache.get(key);
  if (hit) return hit;
  const w = 64;
  const h = INTERIOR_HEIGHT - FLOOR_Y;
  const buf = new PixelBuffer(w, h);
  buf.rect(0, 0, w, h, theme.floor);

  const prng = mulberry32(theme.id.length * 7919 + 43);
  const plankH = 14;

  // 横向实木拼板缝隙与倒角高光
  for (let y = 0; y < h; y += plankH) {
    buf.rect(0, y, w, 1, shade(theme.floorDark, 1.25), 0.85);
    if (y + 1 < h) {
      buf.rect(0, y + 1, w, 1, lighten(theme.floor, 1.15), 0.4);
    }
    // 纵向端缝错位（每行交错）
    const rowIdx = Math.floor(y / plankH);
    const seamOffset = (rowIdx % 2 === 0 ? 0 : 32) + Math.floor(prng() * 4);
    buf.rect(seamOffset % w, y, 1, plankH, shade(theme.floorDark, 1.25), 0.7);
    buf.rect((seamOffset + 32) % w, y, 1, plankH, shade(theme.floorDark, 1.25), 0.7);
  }

  // 丰富木质肌理、微小木节与漫反射斑纹
  for (let i = 0; i < 110; i++) {
    const gx = Math.floor(prng() * w);
    const gy = Math.floor(prng() * h);
    const len = 2 + Math.floor(prng() * 4);
    buf.rect(gx, gy, len, 1, theme.floorDark, 0.28);
    if (prng() > 0.85) {
      buf.rect(gx + 1, gy, 1, 1, lighten(theme.floor, 1.2), 0.35);
    }
  }

  const tex = toTexture(buf, `room-floor-${theme.id}`);
  tex.texture.source.addressMode = 'repeat';
  roomTexCache.set(key, tex);
  return tex;
}

/** 窗：2000K 温暖晚霞天色 + 远山剪影 + 黄铜窗帘杆与褶皱束帘 + 雕花实木窗棂。 */
function buildWindowTexture(theme: RoomTheme): PixelTexture {
  const key = `window:${theme.id}`;
  const hit = roomTexCache.get(key);
  if (hit) return hit;
  const w = 60;
  const h = 46;
  const buf = new PixelBuffer(w, h);

  // 1. 窗外 2000K 琥珀金与晚霞天光渐变
  buf.vGradient(8, 6, w - 16, h - 14, [
    { t: 0, color: hexToNumber('#ffd494') },
    { t: 0.55, color: hexToNumber('#fca85d') },
    { t: 1, color: hexToNumber('#e06d53') },
  ], 10);

  // 窗外远山剪影 (Distant Mountain Silhouette)
  const mountainBase = h - 14;
  for (let x = 8; x < w - 8; x++) {
    const mh = Math.max(2, Math.round(5 + Math.sin((x - 8) * 0.18) * 3 + Math.cos((x - 8) * 0.35) * 2));
    buf.rect(x, mountainBase - mh, 1, mh, hexToNumber('#5a3d5c'), 0.72);
  }

  // 2. 实木外窗框与内十字窗棂 (Timber Frame & Muntin)
  const paneLeft = 8;
  const paneTop = 6;
  const paneW = w - 16;
  const paneH = h - 14;
  const midX = Math.floor(paneLeft + paneW / 2);
  const midY = Math.floor(paneTop + paneH / 2);

  // 窗棂
  buf.rect(midX - 1, paneTop, 2, paneH, theme.trim);
  buf.rect(paneLeft, midY - 1, paneW, 2, theme.trim);
  buf.rect(midX - 1, paneTop, 1, paneH, lighten(theme.trim, 1.25), 0.5);
  buf.rect(paneLeft, midY - 1, paneW, 1, lighten(theme.trim, 1.25), 0.5);

  // 3. 黄铜窗帘杆与球形端头 (Brass Curtain Rod & Finials)
  buf.rect(3, 2, w - 6, 2, hexToNumber('#c89b3c'));
  buf.rect(4, 2, w - 8, 1, hexToNumber('#fce38a'), 0.8);
  buf.rect(1, 1, 3, 4, hexToNumber('#b88728'));
  buf.rect(2, 2, 1, 2, hexToNumber('#ffea9f'));
  buf.rect(w - 4, 1, 3, 4, hexToNumber('#b88728'));
  buf.rect(w - 3, 2, 1, 2, hexToNumber('#ffea9f'));

  // 4. 束起垂坠窗帘布 (Tied-back Draped Curtains)
  const curtainBase = hexToNumber('#fbf3e4');
  const curtainShadow = hexToNumber('#d4c5a9');
  const curtainTie = hexToNumber('#b84a39');

  // 左侧窗帘
  for (let y = 4; y < h - 4; y++) {
    const curW = y < midY ? 8 - Math.floor((y - 4) * 0.2) : 6 + Math.floor((y - midY) * 0.25);
    buf.rect(3, y, curW, 1, curtainBase);
    buf.rect(3 + curW - 1, y, 1, 1, curtainShadow, 0.7);
    if ((y + 1) % 4 === 0) buf.rect(4, y, 2, 1, curtainShadow, 0.4);
  }
  buf.rect(3, midY + 1, 6, 2, curtainTie);

  // 右侧窗帘
  for (let y = 4; y < h - 4; y++) {
    const curW = y < midY ? 8 - Math.floor((y - 4) * 0.2) : 6 + Math.floor((y - midY) * 0.25);
    const rx = w - 3 - curW;
    buf.rect(rx, y, curW, 1, curtainBase);
    buf.rect(rx, y, 1, 1, curtainShadow, 0.7);
    if ((y + 1) % 4 === 0) buf.rect(rx + curW - 3, y, 2, 1, curtainShadow, 0.4);
  }
  buf.rect(w - 9, midY + 1, 6, 2, curtainTie);

  // 5. 加宽厚实实木窗台板 (Wide Timber Window Sill)
  buf.rect(paneLeft - 3, h - 8, paneW + 6, 2, theme.trim);
  buf.rect(paneLeft - 4, h - 6, paneW + 8, 3, lighten(theme.trim, 1.28));
  buf.rect(paneLeft - 4, h - 6, paneW + 8, 1, 0xffffff, 0.35);
  buf.rect(paneLeft - 2, h - 3, paneW + 4, 1, shade(theme.trim, 1.35), 0.75);

  const tex = toTexture(buf, `room-window-${theme.id}`);
  roomTexCache.set(key, tex);
  return tex;
}

/** 门（出口）：门框 + 门板 + 门把。 */
function buildDoorTexture(theme: RoomTheme): PixelTexture {
  const key = `door:${theme.id}`;
  const hit = roomTexCache.get(key);
  if (hit) return hit;
  const w = 22;
  const h = 44;
  const buf = new PixelBuffer(w, h);
  buf.rect(0, 0, w, h, theme.trim);
  buf.rect(2, 2, w - 4, h - 2, shade(theme.trim, 1.35));
  buf.rect(4, 5, w - 8, h - 8, shade(theme.trim, 1.6));
  buf.rect(6, 8, w - 12, 10, shade(theme.trim, 1.45));
  buf.rect(6, 22, w - 12, 12, shade(theme.trim, 1.45));
  buf.setPx(w - 6, h >> 1, hexToNumber('#e8c37a'));
  buf.setPx(w - 7, h >> 1, hexToNumber('#e8c37a'));
  const tex = toTexture(buf, `room-door-${theme.id}`);
  roomTexCache.set(key, tex);
  return tex;
}

let glowTex: PixelTexture | null = null;
function getGlowTexture(): PixelTexture {
  if (!glowTex) {
    const buf = new PixelBuffer(64, 64);
    buf.radialEllipse(32, 32, 31, 31, 0xffffff, 1, 2.2);
    glowTex = toTexture(buf, 'room-glow');
  }
  return glowTex;
}

let shadowTex: PixelTexture | null = null;
function getShadowTexture(): PixelTexture {
  if (!shadowTex) {
    const buf = new PixelBuffer(48, 14);
    buf.radialEllipse(24, 7, 23, 6.5, 0x1a1430, 0.5, 1.4);
    shadowTex = toTexture(buf, 'room-shadow');
  }
  return shadowTex;
}

let dustTex: PixelTexture | null = null;
function getDustTexture(): PixelTexture {
  if (!dustTex) {
    const buf = new PixelBuffer(3, 3);
    buf.radialEllipse(1.5, 1.5, 1.5, 1.5, 0xffffff, 1, 1.5);
    dustTex = toTexture(buf, 'room-dust');
  }
  return dustTex;
}

let gridTex: PixelTexture | null = null;
function getGridTexture(): PixelTexture {
  if (!gridTex) {
    const buf = new PixelBuffer(GRID, GRID);
    buf.rect(0, 0, 1, 1, 0x9fd8ff, 0.5);
    buf.rect(0, 0, GRID, 1, 0x9fd8ff, 0.28);
    buf.rect(0, 0, 1, GRID, 0x9fd8ff, 0.28);
    const tex = toTexture(buf, 'room-grid');
    tex.texture.source.addressMode = 'repeat';
    gridTex = tex;
  }
  return gridTex;
}

/* ------------------------------------------------------------------ */
/* 家具精灵                                                            */
/* ------------------------------------------------------------------ */

interface FurnitureNode {
  spr: Sprite;
  w: number;
  h: number;
  /** 挂墙/吊灯不进深度排序（永远压在角色之前）。 */
  fixedLayer: boolean;
}

/** 取（并缓存）家具纹理；注册表有 id 而美术缺失时抛错，绝不画空白。 */
function getFurnitureTexture(furnitureId: string, colorway: number, flipped: boolean): PixelTexture {
  const rows = getFurnitureRows(furnitureId);
  if (!rows) {
    throw new Error(`cabinInteriorScene: 家具 '${furnitureId}' 没有像素矩阵（注册表与美术不一致）`);
  }
  const cw = getColorway(furnitureId, colorway);
  // 镜像在生成纹理时烘焙，避免每帧改 scale.x 破坏像素取整。
  const finalRows = flipped ? rows.map((r) => [...r].reverse().join('')) : rows;
  return spriteFromMatrix(
    finalRows,
    cw.palette,
    { autoOutline: hexToNumber('#2b2440'), label: `furniture-${furnitureId}-${cw.id}` },
    `f:${furnitureId}:${cw.id}:${flipped ? 'f' : 'n'}`,
  );
}

/* ------------------------------------------------------------------ */
/* 公开接口                                                            */
/* ------------------------------------------------------------------ */

export interface InteriorCallbacks {
  /** 非编辑态点击家具 → 触发交互（睡觉 / 读书 / …）。 */
  onFurnitureTap?: (item: InteriorItem) => void;
  /** 点击地板 → 小人走过去。 */
  onFloorTap?: (gridX: number, gridY: number) => void;
  /** 点击门 → 回室外。 */
  onExit?: () => void;
  /** 编辑态拖拽结束。 */
  onItemMoved?: (itemId: string, gridX: number, gridY: number) => void;
  /** 编辑态选中变化（null = 取消选中）。 */
  onItemSelected?: (itemId: string | null) => void;
}

export interface InteriorScene {
  resize(width: number, height: number): void;
  setLayout(layout: InteriorLayout): void;
  setHouse(house: CabinHouseId): void;
  setEditMode(on: boolean): void;
  setSelected(itemId: string | null): void;
  /** 小人移动到格坐标。 */
  movePerson(gridX: number, gridY: number): void;
  /** 落位点亮演出（≥1s）。 */
  playPlaceGlow(itemId: string): void;
  /** 睡觉演出（躺下 + 「晚安」气泡）。 */
  playSleep(): void;
  /** 冒出一句气泡（家具交互台词）。 */
  say(text: string): void;
  destroy(): void;
}

export interface CreateInteriorSceneOptions {
  canvas: HTMLCanvasElement;
  houseId: CabinHouseId;
  layout: InteriorLayout;
  editMode?: boolean;
  callbacks?: InteriorCallbacks;
  /**
   * I3 衔接（双形态并行）：可选宿主元素。
   * 可选；不传时初始视口 = window（既有行为完全不变）；
   * 传入后按宿主测量值定标，使室内核心同样可挂进内嵌容器。
   */
  host?: Element | null;
  /**
   * W11 衔接：自定义小人行走矩阵 + 调色板（与 cabinScene 同款契约）。
   * 可选；不传或帧数 <2 时回退默认小人，行为与注入前完全一致。
   */
  personWalkFrames?: readonly (readonly string[])[];
  personIdleFrames?: readonly (readonly string[])[];
  personPalette?: PixelPalette;
  personName?: string;
}

const PARTICLE_POOL = 48;
const GLOW_MS = 1100; // 增补第 2 条：点亮演出 ≥1s
const BUBBLE_MS = 3200;
const DRAG_THRESHOLD = 4; // 与 TeamCanvas 一致：4px 内算点击，防拖拽误触
const DOOR_W = 22;
const DOOR_H = 44;

export async function createCabinInteriorScene(
  options: CreateInteriorSceneOptions,
): Promise<InteriorScene> {
  // 开发期自检：美术不完整就别开画布（避免玩家看到半成品场景）
  assertFurnitureArtComplete();

  const app = new Application();
  // I3：初始视口优先取宿主元素（内嵌形态），无宿主时回退 window（既有行为不变）。
  const initialView = resolveViewport(options.host);
  await app.init({
    canvas: options.canvas,
    width: initialView.width,
    height: initialView.height,
    background: hexToNumber('#0b1030'),
    antialias: false,
    resolution: 1,
    autoDensity: false,
  });

  let houseId = options.houseId;
  let theme = getRoomTheme(houseId);
  let layout = options.layout;
  let editMode = Boolean(options.editMode);
  let selectedId: string | null = null;
  let disposed = false;
  const callbacks = options.callbacks ?? {};

  /* ------------------------------ 层级 ------------------------------ */
  const root = new Container();
  app.stage.addChild(root);

  // 层 1：墙面 + 窗 + 门
  const wallLayer = new Container();
  const wallSpr = new Sprite(buildWallTexture(theme).texture);
  const windowSpr = new Sprite(buildWindowTexture(theme).texture);
  windowSpr.roundPixels = true;
  const windowGlow = new Sprite(getGlowTexture().texture);
  windowGlow.blendMode = 'add';
  const doorSpr = new Sprite(buildDoorTexture(theme).texture);
  doorSpr.roundPixels = true;
  const doorGlow = new Sprite(getGlowTexture().texture);
  doorGlow.blendMode = 'add';
  doorGlow.tint = theme.windowLight;
  doorGlow.alpha = 0.22;

  // 层 2：地板
  const floorLayer = new Container();
  const floorSpr = new TilingSprite({
    texture: buildFloorTexture(theme).texture,
    width: INTERIOR_WIDTH,
    height: INTERIOR_HEIGHT - FLOOR_Y,
  });
  floorSpr.y = FLOOR_Y;

  // 2000K 晨光地面漫射梯形光斑 (Trapezoidal sunlight beam on hardwood floor)
  const windowFloorLight = new Graphics();
  windowFloorLight.blendMode = 'add';
  floorLayer.addChild(floorSpr, windowFloorLight);

  // 层 3：角色 + 家具（按伪深度重排）
  const actorLayer = new Container();
  // 编辑态网格
  const gridOverlay = new TilingSprite({
    texture: getGridTexture().texture,
    width: INTERIOR_WIDTH,
    height: INTERIOR_HEIGHT - FLOOR_Y,
  });
  gridOverlay.y = FLOOR_Y;
  gridOverlay.visible = editMode;
  gridOverlay.alpha = 0.5;

  // 层 4：前景暗角
  const vignette = new Sprite(getGlowTexture().texture);
  vignette.blendMode = 'multiply';
  vignette.tint = hexToNumber('#6a7fb5');
  vignette.alpha = 0.26;

  // 光源 1：天光（整体加色）
  const ambient = new Sprite(getGlowTexture().texture);
  ambient.blendMode = 'add';
  ambient.tint = theme.ambient;
  ambient.alpha = 0.18;

  // 光源 2：窗光（点光源，呼吸）
  const DOOR_X = Math.round(INTERIOR_WIDTH * 0.78);
  const DOOR_Y = FLOOR_Y - DOOR_H;

  wallLayer.addChild(wallSpr, windowGlow, windowSpr, doorGlow, doorSpr);
  root.addChild(wallLayer, ambient, floorLayer, gridOverlay, actorLayer, vignette);

  /* ------------------------------ 角色与宠物 ------------------------------ */
  const personShadow = new Sprite(getShadowTexture().texture);
  personShadow.anchor.set(0.5, 0.5);
  personShadow.alpha = 0.55;
  const personBody = new Sprite();
  personBody.roundPixels = true;

  const petShadow = new Sprite(getShadowTexture().texture);
  petShadow.anchor.set(0.5, 0.5);
  petShadow.alpha = 0.45;
  const petBody = new Sprite();
  petBody.roundPixels = true;

  // 名牌容器（像素暗牌 + 高光边 + 粗体名字）
  const nameTagC = new Container();
  const namePlateG = new Graphics();
  const nameTag = new Text({
    text: options.personName || '旅人',
    style: {
      fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      fontSize: 10,
      fill: 0xf8fafc,
      fontWeight: 'bold',
    },
  });
  nameTag.anchor.set(0.5, 0.5);
  nameTag.position.set(0, -8);
  nameTagC.addChild(namePlateG, nameTag);

  function updateNamePlate(): void {
    const tw = nameTag.width;
    namePlateG.clear();
    namePlateG.rect(-tw / 2 - 4, -14, tw + 8, 12).fill({ color: 0x0f172a, alpha: 0.72 });
    namePlateG.rect(-tw / 2 - 4, -14, tw + 8, 1).fill({ color: 0x94a3b8, alpha: 0.9 });
  }
  updateNamePlate();

  const personHolder = new Container();
  personHolder.addChild(personShadow, personBody, nameTagC);
  const petHolder = new Container();
  petHolder.addChild(petShadow, petBody);
  actorLayer.addChild(personHolder, petHolder);

  let personX = 6 * GRID;
  let personY = 5;
  let targetX = personX;
  let targetY = personY;
  let isMoving = false;
  let personDir = 1;
  let sleeping = false;
  let sleepUntil = 0;

  let headTopOffset = -26;
  let personBaseScaleX = 1;
  let personBaseScaleY = 1;
  let petBaseScale = 1;

  let characterMode: 'custom' | 'pawn' | 'fallback' = 'fallback';
  let petMode: 'sheep' | 'fallback' = 'fallback';

  let customWalkTextures: Texture[] = [];
  let customIdleTextures: Texture[] = [];
  let pawnWalkTextures: Texture[] = [];
  let pawnIdleTextures: Texture[] = [];
  let sheepTextures: Texture[] = [];
  let fallbackPersonWalkTextures: Texture[] = [];
  let fallbackPetWalkTextures: Texture[] = [];

  // W11 个性化小人矩阵优先（≥2 帧），用户有专属头像时渲染 24×48 高清微像素角色
  const customFrames = options.personWalkFrames;
  const useCustomPerson = Array.isArray(customFrames) && customFrames.length >= 2;
  const personPal = options.personPalette ?? PERSON_PALETTE;

  if (useCustomPerson) {
    characterMode = 'custom';
    headTopOffset = -50;
    personBaseScaleX = 1;
    personBaseScaleY = 1;
    personBody.anchor.set(0.5, 1);
    customWalkTextures = customFrames.map((m, idx) =>
      spriteFromMatrix(m, personPal, { label: `room-custom-walk-${idx}` }, `rcw:${idx}`).texture,
    );
    const idleList = Array.isArray(options.personIdleFrames) && options.personIdleFrames.length >= 2
      ? options.personIdleFrames
      : customFrames;
    customIdleTextures = idleList.map((m, idx) =>
      spriteFromMatrix(m, personPal, { label: `room-custom-idle-${idx}` }, `rci:${idx}`).texture,
    );
    personBody.texture = customIdleTextures[0]!;
  } else {
    // 默认回退 16×24 小人帧
    fallbackPersonWalkTextures = PERSON_WALK_FRAMES.map((m, idx) =>
      spriteFromMatrix(m, PERSON_PALETTE, { label: `room-person-${idx}` }, `rp:${idx}`).texture,
    );
    personBody.anchor.set(0.5, 1);
    personBody.texture = fallbackPersonWalkTextures[0]!;
  }

  // 宠物默认回退小宠
  const petPal = petPalette(hexToNumber('#7fc8e8'));
  fallbackPetWalkTextures = PET_WALK_FRAMES.map((m, idx) =>
    spriteFromMatrix(m, petPal, { label: `room-pet-${idx}` }, `rpet:${idx}`).texture,
  );
  petBody.anchor.set(0.5, 1);
  petBody.texture = fallbackPetWalkTextures[0]!;

  function syncTinySwords(assets: TinySwordsAssets): void {
    if (!useCustomPerson && assets.pawnYellow) {
      characterMode = 'pawn';
      const pawn = getPawnFrames(assets.pawnYellow);
      pawnIdleTextures = pawn.idle;
      pawnWalkTextures = pawn.walk;
      headTopOffset = -38;
      personBaseScaleX = 0.46;
      personBaseScaleY = 0.46;
      personBody.anchor.set(0.5, 0.72);
      personBody.scale.set(personBaseScaleX, personBaseScaleY);
      personBody.texture = pawnIdleTextures[0]!;
    }
    if (assets.sheep) {
      petMode = 'sheep';
      sheepTextures = getSheepFrames(assets.sheep);
      petBaseScale = 0.42;
      petBody.anchor.set(0.5, 0.68);
      petBody.scale.set(petBaseScale);
      petBody.texture = sheepTextures[0]!;
    }
    nameTagC.position.set(0, headTopOffset);
  }

  const loadedAssets = getLoadedTinySwordsAssets();
  if (loadedAssets) {
    syncTinySwords(loadedAssets);
  } else {
    nameTagC.position.set(0, headTopOffset);
    void loadTinySwordsAssets().then((assets) => {
      if (disposed || !assets) return;
      syncTinySwords(assets);
    });
  }

  /* ------------------------------ 气泡 ------------------------------ */
  const bubbleLayer = new Container();
  const bubbleRoot = new Container();
  const bubbleG = new Graphics();
  const bubbleText = new Text({
    text: '',
    style: { fontFamily: '"Microsoft YaHei", sans-serif', fontSize: 11, fill: 0x2b2440 },
  });
  bubbleRoot.addChild(bubbleG, bubbleText);
  bubbleRoot.visible = false;
  bubbleLayer.addChild(bubbleRoot);
  root.addChild(bubbleLayer);
  let bubbleUntil = 0;

  function showBubble(text: string): void {
    bubbleText.text = text;
    bubbleG.clear();
    const w = bubbleText.width + 10;
    const h = bubbleText.height + 8;
    bubbleG.rect(0, 0, w, h).fill(0xfdfaf3);
    bubbleG.rect(0, 0, w, h).stroke({ width: 1, color: 0x3a4a63 });
    bubbleG.rect(2, h, 4, 3).fill(0x3a4a63);
    bubbleRoot.visible = true;
    bubbleUntil = performance.now() + BUBBLE_MS;
  }

  /* ------------------------------ 家具节点 ------------------------------ */
  const furnitureNodes = new Map<string, FurnitureNode>();

  function buildNode(item: InteriorItem): FurnitureNode {
    const def = getFurniture(item.furnitureId);
    const tex = getFurnitureTexture(item.furnitureId, item.colorway, item.flipped);
    const spr = new Sprite(tex.texture);
    spr.roundPixels = true;
    const mount = def?.mount ?? 'floor';
    spr.anchor.set(0.5, mount === 'floor' ? 1 : 0);
    return { spr, w: tex.width, h: tex.height, fixedLayer: mount !== 'floor' };
  }

  function positionNode(node: FurnitureNode, item: InteriorItem): void {
    const def = getFurniture(item.furnitureId);
    const w = (def?.sizeCells.w ?? 1) * GRID;
    const h = (def?.sizeCells.h ?? 1) * GRID;
    const cx = item.x * GRID + Math.round(w / 2);
    node.spr.x = Math.round(cx);
    if ((def?.mount ?? 'floor') === 'floor') {
      node.spr.y = Math.round(FLOOR_Y + item.y * GRID + h);
    } else {
      // 挂墙/吊灯：贴墙带顶部
      node.spr.y = Math.round(FLOOR_Y + item.y * GRID);
    }
  }

  function applySelection(): void {
    for (const [id, node] of furnitureNodes.entries()) {
      const on = editMode && id === selectedId;
      node.spr.tint = on ? 0xfff4d6 : 0xffffff;
    }
  }

  /**
   * 按伪深度重排：数组顺序即绘制顺序（先画的被后画的压住）。
   * 用 `addChildAt` 精确插入（v8 API；v7 的 addChildBefore 不存在），
   * 角色永远插在「比自己更靠前」的第一件家具之下 → 家具正确遮挡小人。
   */
  function reorder(): void {
    const sorted = [...layout.items].sort((a, b) => depthKey(a) - depthKey(b));
    const personDepth = (personY + 1) * 1000;
    for (const item of sorted) {
      const node = furnitureNodes.get(item.id);
      if (!node) continue;
      if (depthKey(item) > personDepth && actorLayer.children.includes(personHolder)) {
        const idx = actorLayer.getChildIndex(personHolder);
        actorLayer.addChildAt(node.spr, idx);
        const petIdx = actorLayer.getChildIndex(petHolder);
        actorLayer.addChildAt(petHolder, petIdx);
      } else {
        actorLayer.addChild(node.spr);
      }
    }
    // 墙/顶家具永远压在最前
    for (const item of sorted) {
      const node = furnitureNodes.get(item.id);
      if (node?.fixedLayer) actorLayer.addChild(node.spr);
    }
    applySelection();
  }

  function syncFurnitureNodes(): void {
    const alive = new Set<string>();
    for (const item of layout.items) {
      alive.add(item.id);
      let node = furnitureNodes.get(item.id);
      if (!node) {
        node = buildNode(item);
        furnitureNodes.set(item.id, node);
        actorLayer.addChild(node.spr);
      }
      positionNode(node, item);
    }
    for (const [id, node] of [...furnitureNodes.entries()]) {
      if (alive.has(id)) continue;
      node.spr.destroy();
      furnitureNodes.delete(id);
    }
    reorder();
  }

  /* ------------------------------ 粒子 ------------------------------ */
  interface Mote {
    spr: Sprite;
    x: number;
    y: number;
    vx: number;
    vy: number;
    phase: number;
    base: number;
  }
  const motes: Mote[] = [];
  for (let i = 0; i < PARTICLE_POOL; i++) {
    const spr = new Sprite(getDustTexture().texture);
    spr.blendMode = 'add';
    const prng = mulberry32(1000 + i * 31);
    const mote: Mote = {
      spr,
      x: prng() * INTERIOR_WIDTH,
      y: prng() * INTERIOR_HEIGHT,
      vx: (prng() - 0.5) * 0.12,
      vy: -0.05 - prng() * 0.1,
      phase: prng() * Math.PI * 2,
      base: 0.18 + prng() * 0.35,
    };
    motes.push(mote);
    actorLayer.addChild(spr);
  }

  /* ------------------------------ 点亮演出 ------------------------------ */
  interface PlaceGlow {
    spr: Sprite;
    x: number;
    y: number;
    start: number;
  }
  const placeGlows: PlaceGlow[] = [];

  /* ------------------------------ 交互 ------------------------------ */
  let dragState: { itemId: string; startX: number; startY: number; moved: boolean } | null = null;

  function canvasPoint(ev: PointerEvent): { x: number; y: number } {
    const rect = options.canvas.getBoundingClientRect();
    const screenX = ev.clientX - rect.left;
    const screenY = ev.clientY - rect.top;
    const currentScale = root.scale.x || 1;
    return {
      x: (screenX - root.x) / currentScale,
      y: (screenY - root.y) / currentScale,
    };
  }

  function hitFurniture(px: number, py: number): InteriorItem | null {
    // 从视觉最上层往回命中
    const sorted = [...layout.items].sort((a, b) => depthKey(b) - depthKey(a));
    for (const item of sorted) {
      const node = furnitureNodes.get(item.id);
      if (!node) continue;
      const def = getFurniture(item.furnitureId);
      const w = (def?.sizeCells.w ?? 1) * GRID;
      const h = (def?.sizeCells.h ?? 1) * GRID;
      const left = node.spr.x - w / 2;
      const top =
        (def?.mount ?? 'floor') === 'floor' ? FLOOR_Y + item.y * GRID : node.spr.y;
      if (px >= left && px <= left + w && py >= top && py <= top + h) return item;
    }
    return null;
  }

  function onPointerDown(ev: PointerEvent): void {
    const p = canvasPoint(ev);
    if (p.x >= DOOR_X - 4 && p.x <= DOOR_X + DOOR_W + 4 && p.y >= DOOR_Y && p.y <= DOOR_Y + DOOR_H) {
      callbacks.onExit?.();
      return;
    }
    const hit = hitFurniture(p.x, p.y);
    if (!hit) {
      callbacks.onItemSelected?.(null);
      selectedId = null;
      applySelection();
      callbacks.onFloorTap?.(
        Math.floor(p.x / GRID),
        Math.max(0, Math.floor((p.y - FLOOR_Y) / GRID)),
      );
      return;
    }
    if (editMode) {
      selectedId = hit.id;
      callbacks.onItemSelected?.(hit.id);
      applySelection();
      dragState = { itemId: hit.id, startX: p.x, startY: p.y, moved: false };
    } else {
      callbacks.onFurnitureTap?.(hit);
    }
  }

  function onPointerMove(ev: PointerEvent): void {
    if (!dragState || !editMode) return;
    const p = canvasPoint(ev);
    const dx = p.x - dragState.startX;
    const dy = p.y - dragState.startY;
    if (!dragState.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    dragState.moved = true;
    const node = furnitureNodes.get(dragState.itemId);
    if (!node) return;
    node.spr.x = Math.round(p.x);
    node.spr.y = Math.round(p.y);
  }

  function onPointerUp(ev: PointerEvent): void {
    if (!dragState) return;
    const p = canvasPoint(ev);
    const state = dragState;
    dragState = null;
    if (!state.moved) return;
    // 拖拽落位：吸附到 16px 网格（可由 UI 关闭吸附做自由摆放）
    const gx = Math.round(p.x / GRID - 0.5);
    const gy = Math.round((p.y - FLOOR_Y) / GRID - 0.5);
    callbacks.onItemMoved?.(state.itemId, gx, gy);
  }

  options.canvas.addEventListener('pointerdown', onPointerDown);
  options.canvas.addEventListener('pointermove', onPointerMove);
  options.canvas.addEventListener('pointerup', onPointerUp);
  options.canvas.addEventListener('pointercancel', onPointerUp);

  /* ------------------------------ 尺寸 ------------------------------ */
  let viewW = window.innerWidth;
  let viewH = window.innerHeight;

  function applyScale(): void {
    const scale = Math.max(0.5, Math.min(viewW / INTERIOR_WIDTH, viewH / INTERIOR_HEIGHT));
    root.scale.set(scale);
    root.x = (viewW - INTERIOR_WIDTH * scale) / 2;
    root.y = (viewH - INTERIOR_HEIGHT * scale) / 2;
  }

  function layoutStatic(): void {
    wallSpr.texture = buildWallTexture(theme).texture;
    floorSpr.texture = buildFloorTexture(theme).texture;
    floorSpr.y = FLOOR_Y;
    windowSpr.texture = buildWindowTexture(theme).texture;
    windowSpr.x = Math.round(INTERIOR_WIDTH * 0.16);
    windowSpr.y = Math.round(FLOOR_Y * 0.22);
    windowGlow.tint = theme.windowLight;
    windowGlow.x = windowSpr.x + 14;
    windowGlow.y = windowSpr.y + 16;
    windowGlow.width = 96;
    windowGlow.height = 80;

    windowFloorLight.clear();
    windowFloorLight.poly([
      windowSpr.x + 8, FLOOR_Y,
      windowSpr.x + 52, FLOOR_Y,
      windowSpr.x + 80, FLOOR_Y + 72,
      windowSpr.x - 20, FLOOR_Y + 72,
    ]);
    windowFloorLight.fill({ color: 0xffd17a, alpha: 0.22 });

    doorSpr.texture = buildDoorTexture(theme).texture;
    doorSpr.x = DOOR_X;
    doorSpr.y = DOOR_Y;
    doorGlow.x = DOOR_X + DOOR_W / 2;
    doorGlow.y = DOOR_Y + DOOR_H / 2;
    doorGlow.width = 34;
    doorGlow.height = 34;
    ambient.tint = theme.ambient;
    vignette.tint = hexToNumber('#6a7fb5');
    // 墙/前景铺满（含暗角放大溢出）
    wallSpr.width = INTERIOR_WIDTH;
    ambient.width = INTERIOR_WIDTH * 1.6;
    ambient.height = INTERIOR_HEIGHT * 1.6;
    ambient.x = -INTERIOR_WIDTH * 0.3;
    ambient.y = -INTERIOR_HEIGHT * 0.3;
    vignette.width = INTERIOR_WIDTH * 1.5;
    vignette.height = INTERIOR_HEIGHT * 1.5;
    vignette.x = -INTERIOR_WIDTH * 0.25;
    vignette.y = -INTERIOR_HEIGHT * 0.25;
    applyScale();
  }

  /* ------------------------------ 主循环 ------------------------------ */
  let last = performance.now();

  app.ticker.add(() => {
    if (disposed) return;
    const now = performance.now();
    const dt = Math.min(64, now - last);
    last = now;
    const t = app.ticker.lastTime;

    // 角色与宠物动画及平滑移动
    if (!sleeping) {
      if (isMoving) {
        const dx = targetX - personX;
        const dy = targetY - personY;
        const dist = Math.hypot(dx, dy);
        if (dist < 1.2) {
          personX = targetX;
          personY = targetY;
          isMoving = false;
        } else {
          personDir = dx >= 0 ? 1 : -1;
          const stepX = (dx / dist) * 0.9 * (dt / 16);
          const stepY = (dy / dist) * 0.06 * (dt / 16);
          personX += Math.abs(dx) > Math.abs(stepX) ? stepX : dx;
          personY += Math.abs(dy) > Math.abs(stepY) ? stepY : dy;
        }
      }

      // 帧动画切换
      if (characterMode === 'custom') {
        const frames = isMoving ? customWalkTextures : customIdleTextures;
        const speed = isMoving ? 140 : 240;
        const idx = Math.floor(t / speed) % frames.length;
        personBody.texture = frames[idx]!;
        personBody.scale.set(personDir * personBaseScaleX, personBaseScaleY);
      } else if (characterMode === 'pawn') {
        const frames = isMoving ? pawnWalkTextures : pawnIdleTextures;
        const speed = isMoving ? 120 : 160;
        const idx = Math.floor(t / speed) % frames.length;
        personBody.texture = frames[idx]!;
        personBody.scale.set(personDir * personBaseScaleX, personBaseScaleY);
      } else {
        const idx = isMoving ? Math.floor(t / 220) % 2 : 0;
        personBody.texture = fallbackPersonWalkTextures[idx]!;
        personBody.scale.set(personDir * personBaseScaleX, personBaseScaleY);
      }

      // 宠物动画
      if (petMode === 'sheep') {
        const idx = Math.floor(t / 180) % sheepTextures.length;
        petBody.texture = sheepTextures[idx]!;
        petBody.scale.set(personDir * petBaseScale, petBaseScale);
      } else {
        const idx = Math.floor(t / 260) % 2;
        petBody.texture = fallbackPetWalkTextures[idx]!;
        petBody.scale.set(personDir * petBaseScale, petBaseScale);
      }
    }

    personHolder.x = Math.round(personX);
    personHolder.y = Math.round(FLOOR_Y + personY * GRID + GRID);
    petHolder.x = Math.round(personX - personDir * 16);
    petHolder.y = Math.round(FLOOR_Y + personY * GRID + GRID - 2);
    personShadow.y = 2;
    petShadow.y = 2;

    if (sleeping && now > sleepUntil) {
      sleeping = false;
      personBody.scale.set(personDir * personBaseScaleX, personBaseScaleY);
    }

    // 窗光呼吸与地面晨光漫射
    windowGlow.alpha = 0.42 + Math.sin(t / 900) * 0.12;
    windowFloorLight.alpha = 0.22 + Math.sin(t / 900) * 0.05;
    doorGlow.alpha = 0.16 + Math.sin(t / 1400 + 1.2) * 0.07;

    // 粒子：浮尘上飘 + 明灭
    for (const m of motes) {
      m.x += m.vx * (dt / 16);
      m.y += m.vy * (dt / 16);
      if (m.y < -2) {
        m.y = INTERIOR_HEIGHT + 2;
        m.x = (m.x + 37) % INTERIOR_WIDTH;
      }
      if (m.x < -2) m.x = INTERIOR_WIDTH + 2;
      if (m.x > INTERIOR_WIDTH + 2) m.x = -2;
      m.spr.x = Math.round(m.x);
      m.spr.y = Math.round(m.y);
      m.spr.alpha = m.base * (0.6 + Math.sin(t / 700 + m.phase) * 0.4);
      m.spr.tint = theme.windowLight;
    }

    // 点亮演出：1.1s 渐变 + 扩散
    for (let i = placeGlows.length - 1; i >= 0; i--) {
      const g = placeGlows[i]!;
      const p = (now - g.start) / GLOW_MS;
      if (p >= 1) {
        g.spr.destroy();
        placeGlows.splice(i, 1);
        continue;
      }
      const grow = 0.6 + p * 1.5;
      g.spr.width = Math.round(70 * grow);
      g.spr.height = Math.round(70 * grow);
      g.spr.alpha = (1 - p) * 0.85;
      g.spr.x = Math.round(g.x - g.spr.width / 2);
      g.spr.y = Math.round(g.y - g.spr.height / 2);
    }

    // 气泡跟随 + 到期（严格在名牌与头顶上方，永不遮挡面部）
    if (bubbleRoot.visible) {
      const halfW = bubbleG.width / 2 + 6;
      bubbleRoot.x = Math.round(
        Math.min(INTERIOR_WIDTH - halfW, Math.max(halfW, personHolder.x)),
      );
      bubbleRoot.y = Math.round(
        Math.max(bubbleG.height + 2, personHolder.y + headTopOffset - bubbleG.height - 12),
      );
      if (now > bubbleUntil) bubbleRoot.visible = false;
    }
  });

  /* ------------------------------ 初始化 ------------------------------ */
  layoutStatic();
  syncFurnitureNodes();

  // I3 · 双形态：**不再**自挂 window resize。
  // 视口完全由宿主层（InteriorStage）经 observeViewport 驱动 —— 内嵌形态下
  // 容器尺寸可能远小于窗口，若此处再按 window.innerWidth 自算，会把画面顶回整窗。
  // 宿主层在 scene 就绪后立即调用一次 resize() 完成首帧定标。

  return {
    resize(w, h) {
      viewW = w;
      viewH = h;
      app.renderer.resize(w, h);
      applyScale();
    },
    setLayout(next) {
      layout = next;
      syncFurnitureNodes();
    },
    setHouse(house) {
      houseId = house;
      theme = getRoomTheme(houseId);
      layoutStatic();
    },
    setEditMode(on) {
      editMode = on;
      gridOverlay.visible = on;
      if (!on) {
        selectedId = null;
        dragState = null;
      }
      applySelection();
    },
    setSelected(id) {
      selectedId = id;
      applySelection();
    },
    movePerson(gx, gy) {
      targetX = gx * GRID + GRID / 2;
      targetY = gy;
      isMoving = true;
      reorder();
    },
    playPlaceGlow(itemId) {
      const node = furnitureNodes.get(itemId);
      if (!node) return;
      const spr = new Sprite(getGlowTexture().texture);
      spr.blendMode = 'add';
      spr.tint = theme.windowLight;
      spr.width = 70;
      spr.height = 70;
      const g: PlaceGlow = {
        spr,
        x: node.spr.x,
        y: (node.spr.y - node.h / 2) * 1,
        start: performance.now(),
      };
      placeGlows.push(g);
      actorLayer.addChild(spr);
    },
    playSleep() {
      sleeping = true;
      sleepUntil = performance.now() + 2600;
      personBody.scale.set(personDir * personBaseScaleX, personBaseScaleY * 0.62);
      showBubble('晚安…');
    },
    say(text: string) {
      showBubble(text);
    },
    destroy() {
      disposed = true;
      options.canvas.removeEventListener('pointerdown', onPointerDown);
      options.canvas.removeEventListener('pointermove', onPointerMove);
      options.canvas.removeEventListener('pointerup', onPointerUp);
      options.canvas.removeEventListener('pointercancel', onPointerUp);
      app.destroy(true, { children: true });
    },
  };
}
