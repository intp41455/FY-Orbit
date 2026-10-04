import {
  Application,
  Container,
  FederatedPointerEvent,
  Graphics,
  Sprite,
  Text,
  Texture,
  Ticker,
  TilingSprite,
} from 'pixi.js';
import {
  PET_COLORS,
  WORLD,
  cameraTargetX,
  clampCameraToPerson,
  clampCameraX,
  clampToWorld,
  edgeFadeAlpha,
  layerVirtualWidth,
  lerpCameraX,
  screenToWorldX,
  worldToScreenX,
  type CabinBackgroundId,
  type CabinConfig,
  type CabinHouseId,
  type DialogueSpeaker,
} from './cabinConfig';
import {
  PixelBuffer,
  bayer4,
  bufferToTexture,
  matrixToRgba,
  mulberry32,
  spriteFromMatrix,
  type PixelPalette,
  type PixelTexture,
  type RgbaSnapshot,
} from './cabinPixels';
import {
  CLOUD_ROWS,
  CABIN_HOUSE_ART,
  HOUSE_GLOWS,
  PERSON_PALETTE,
  PERSON_WALK_FRAMES,
  PET_WALK_FRAMES,
  PETAL_ROWS,
  SEED_ROWS,
  SNOWFLAKE_ROWS,
  SPARKLE_ROWS,
  STARDUST_ROWS,
  THEME_ART,
  PARTICLE_POOL,
  hexToNumber,
  houseScaleFactor,
  petPalette,
  PERSON_SCALE,
  shade,
  type ParticleKindDef,
  type ThemeArt,
} from './cabinPixelArt';

/**
 * 数码小屋 PixiJS 像素风渲染层（零外部素材）。
 *
 * 像素风架构（唯美精致向，参考 Eastward/Summerhouse，非 8-bit 复古）：
 *  - 虚拟分辨率 480×270，worldScale = 画布高 / 270，全部图层按 worldScale 统一缩放；
 *  - 背景 7 层全部「静态预渲染为纹理」（天空拉伸精灵 + 6 张可平铺 TilingSprite），
 *    逐帧不重绘背景，只改 tilePosition（视差 + 云漂移）；
 *  - 视差：云 0.1 / 星 0.08 / 星云 0.06 / 远景 0.3 / 中景 0.65 / 近景 1.15 / 地面 1.0；
 *  - **开放世界（G3）**：世界宽 WORLD.width（3840 虚拟 px ≈ 8 屏），cameraX 平滑跟随小人；
 *    人物/宠物/房屋都用**世界坐标**存储，再由 cameraX 投影到屏幕，因此房屋不随相机漂移；
 *    指针点击先反投影回世界坐标再钳制（clampToWorld），走满世界也不会被拽回屏幕内；
 *    世界左右缘用渐隐收束（edgeFade），不是硬弹回。
 *    注：人物/宠物的纵坐标用「地面带内比例 depth∈[0,1]」存储，改分辨率/窗口高度时不跳位；
 *  - 氛围光：白色径向辉光纹理 + tint + 'add' 加法混合（窗光/篝火/日光/星云），低频呼吸；
 *  - 环境粒子池 ≤60（萤火虫/花瓣/水光/飞絮/星尘/雪），仅位置与透明度逐帧更新；
 *  - 小人 16×24 两帧走路（换帧，不平滑摆腿），宠物 20×14 两帧 + 调色板换色；
 *  - 所有显示坐标 Math.round 到像素网格，纹理 nearest 采样，杜绝半像素模糊。
 *
 * 本文件不 import React；jsdom 下由 CabinPage.test.tsx mock 掉 CabinStage。
 */

/* ------------------------------ 常量 ------------------------------ */

/** 虚拟分辨率与各图层高度（虚拟像素）。 */
const VIRTUAL_H = 270;
const SKY_VH = 157; // 58% 地平线，与 groundY = height*0.58 精确对应
const FAR_VH = 40;
const MID_VH = 50;
const NEAR_VH = 26;
/**
 * 中景/近景**道具段的纹理宽度**（虚拟像素）。
 *
 * 旧实现：mid/near 只有一张 480 宽的贴图，整张 TilingSprite 无限平铺。
 * 在 G3 的 8 屏世界里，这等于**每屏看到的道具摆位完全一样**（周期 = 1 屏），
 * 玩家往右走 4 屏就能数出重复 —— 这是「空间感」最伤的一处。
 *
 * 新实现：每段 1920 宽（= 4 个 480 视口），每段用**不同种子**生成，
 * 且段与段之间按**屏幕位移**拼接（不靠 tilePosition 重置），于是：
 *   - 世界内任意一屏看到的是 1920 宽构图里的一个**不同窗口**；
 *   - 相邻屏的道具不再重置。
 * 仍用 stampTiled 按段宽环绕，段内依旧无缝。
 */
const PROP_TILE_VW = 1920;
/** 每层生成几段互不相同的道具纹理（不同种子 → 摆位不同）。 */
const MID_SEG_VARIANTS = 2;
const NEAR_SEG_VARIANTS = 3;
/**
 * 每层的精灵槽位数（≥ 可见段数；多出的槽位按需隐藏）。
 * 比变体数多一个是为了应对超宽屏（视口 > 1920 虚拟 px）需要更多段。
 */
const MID_SEG_SLOTS = 3;
const NEAR_SEG_SLOTS = 4;
const GROUND_VH = 113; // 270 - 157
const GROUND_TILE_VW = 128;
const TILE_VW = 480;
/** G2-1：天空渐变缓冲宽度（= 全宽）。曾为 1px 导致 bayer 抖动退化成每 4 行重复的水平亮带；导出供护栏测试锁定。 */
export const SKY_BUFFER_WIDTH = TILE_VW;
/** 视差位移余量（虚拟像素），保证 TilingSprite 平移时始终盖满画面。 */
const PARALLAX_MARGIN = 96;

/* ---- G3 · 摄像机与视差系数 ---- */

/** 各平铺层的视差系数（相对相机）。ground=1.0 与人物同速，near 略快产生前景掠过感。 */
const PARALLAX = {
  star: 0.08,
  nebula: 0.06,
  cloud: 0.1,
  far: 0.3,
  mid: 0.65,
  ground: 1.0,
  near: 1.15,
} as const;

/**
 * 人物/宠物纵向坐标改用**地面带比例 depth**（0 = 地平线，1 = 屏幕底）存储，
 * 而非屏幕像素 —— 这样 resize（高度变化）时人物保持在同一相对位置，不会漂到别处。
 *
 * 可走区间仍然严格复现旧的屏幕像素边界 `groundY+24 .. height-26`
 * （`depthLimits()` 按当前高度换算），**不借 G3 之名改玩法手感**。
 */
/** 旧边界：地平线下方 24 屏幕 px、屏幕底部上方 26 屏幕 px。 */
const WALK_MARGIN_TOP_PX = 24;
const WALK_MARGIN_BOTTOM_PX = 26;

/** 宠物相对小人的纵向偏移（虚拟 px，折算成 depth）。 */
const PET_DEPTH_OFFSET = 4 / 113;

/** 边界渐隐的虚拟宽度（人物靠近世界缘时两侧变暗，给出「到头了」的视觉收束）。 */
const EDGE_FADE_VW = 48;

const BUBBLE_MS = 3600;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/* ------------------------------ 主题纹理工厂 ------------------------------ */

interface ThemeTextures {
  sky: PixelTexture;
  clouds: PixelTexture | null;
  stars: PixelTexture | null;
  nebula: PixelTexture | null;
  far: PixelTexture;
  /** 中景道具段（多个变体，按段选用，避免 8 屏世界出现 1 屏周期的重复）。 */
  mid: PixelTexture[];
  /** 近景道具段（同上）。 */
  near: PixelTexture[];
  ground: PixelTexture;
}

const themeCache = new Map<CabinBackgroundId, ThemeTextures>();

/** 让纹理在 TilingSprite 中无缝重复（v8：TextureSource.addressMode）。 */
function tiled(tex: PixelTexture): PixelTexture {
  tex.texture.source.addressMode = 'repeat';
  return tex;
}

function toTexture(buf: PixelBuffer, label: string): PixelTexture {
  return bufferToTexture(buf, { label });
}

/** 高度数组 → 逐列填充剪影带。 */
function fillBand(buf: PixelBuffer, heights: readonly number[], color: number): void {
  for (let x = 0; x < buf.width; x++) {
    const h = heights[x];
    if (h > 0) buf.rect(x, buf.height - h, 1, h, color);
  }
}

function bandHeights(
  width: number,
  base: number,
  waves: readonly { amp: number; k: number; p: number }[],
): number[] {
  const out: number[] = [];
  for (let x = 0; x < width; x++) {
    let h = base;
    for (const w of waves) {
      h += w.amp * Math.sin((Math.PI * 2 * w.k * x) / width + w.p);
    }
    out.push(Math.max(2, Math.round(h)));
  }
  return out;
}

/** 剪影小树 / 尖岩（带水平环绕，保证平铺无缝）。 */
function stampSilhouetteWrapped(
  buf: PixelBuffer,
  x: number,
  baseY: number,
  color: number,
  kind: 'tree' | 'spire',
  prng: () => number,
): void {
  for (const dx of [-TILE_VW, 0, TILE_VW]) {
    if (kind === 'tree') {
      const half = 2 + Math.floor(prng() * 2);
      const h = 6 + Math.floor(prng() * 5);
      for (let i = -half; i <= half; i++) {
        const colH = Math.max(1, h - Math.abs(i) * 2);
        buf.rect(x + dx + i, baseY - colH, 1, colH, color);
      }
    } else {
      const h = 5 + Math.floor(prng() * 10);
      const w = prng() > 0.6 ? 2 : 1;
      buf.rect(x + dx, baseY - h, w, h, color);
      buf.setPx(x + dx, baseY - h - 1, color);
    }
  }
}

/** 环形行星（宇宙场景天空点缀，程序化像素：左上受光 + 暗斑 + 光环）。 */
function drawRingedPlanet(buf: PixelBuffer, cx: number, cy: number): void {
  const r = 14;
  for (let y = -r - 2; y <= r + 2; y++) {
    for (let x = -r - 2; x <= r + 2; x++) {
      const d = Math.sqrt(x * x + y * y);
      if (d > r) continue;
      let c = 0xf2b880;
      if (x - y > 6) c = 0xd99a5e;
      if (x - y > 12) c = 0xc0824a;
      if ((x + 6) * (x + 6) + (y + 4) * (y + 4) < 16) c = 0xe09a5e;
      if ((x - 7) * (x - 7) + (y - 6) * (y - 6) < 9) c = 0xe09a5e;
      buf.setPx(cx + x, cy + y, c);
    }
  }
  // 光环：椭圆环，行星背后的半段被遮住
  for (let a = 0; a < 360; a += 2) {
    const rad = (a * Math.PI) / 180;
    const x = Math.round(Math.cos(rad) * 24);
    const y = Math.round(Math.sin(rad) * 7);
    if (y > 0 && Math.abs(x) < r - 2) continue;
    buf.setPx(cx + x, cy + y, 0xc9a2e8);
    buf.setPx(cx + x, cy + y + 1, 0xa87fd0);
  }
}

interface ElementDefLike {
  rows: readonly string[];
  palette: Record<string, number>;
  count: number;
  scale: number;
  baseYMin: number;
  baseYMax: number;
}

/**
 * 按宽度缩放元素数量：**只新建对象，不改 THEME_ART 原数据**
 * （cabinPixelArt.ts 属 G2 所有权，且被主题缓存共享）。
 * 缓冲从 480 宽变 1920 宽时，count 必须 ×4，否则道具密度会降到 1/4。
 */
function scaleElementDefs(defs: readonly ElementDefLike[], factor: number): ElementDefLike[] {
  if (factor === 1) return defs.slice();
  return defs.map((d) => ({ ...d, count: Math.max(1, Math.round(d.count * factor)) }));
}

/** 元素盖章（按落地 y 排序保证前后遮挡），水平环绕无缝。 */
function stampElements(buf: PixelBuffer, defs: readonly ElementDefLike[], prng: () => number): void {
  interface Placement {
    snap: RgbaSnapshot;
    x: number;
    y: number;
    scale: number;
  }
  const placements: Placement[] = [];
  for (const def of defs) {
    const snap = matrixToRgba(def.rows, def.palette);
    for (let i = 0; i < def.count; i++) {
      const x = Math.floor(prng() * buf.width);
      const baseY = Math.floor(def.baseYMin + prng() * (def.baseYMax - def.baseYMin));
      placements.push({ snap, x, y: baseY - snap.height * def.scale, scale: def.scale });
    }
  }
  placements.sort((a, b) => a.y + a.snap.height * a.scale - (b.y + b.snap.height * b.scale));
  for (const p of placements) buf.stampTiled(p.snap, buf.width, p.x, p.y, p.scale);
}

function buildThemeTextures(bg: CabinBackgroundId): ThemeTextures {
  const art = THEME_ART[bg];
  const prng = mulberry32(0xcab1e + bg.length * 7919);

  // 天空：1px 宽渐变条横向拉伸（垂直渐变横向均匀，拉伸无损且无接缝）
  const skyBuf = new PixelBuffer(SKY_BUFFER_WIDTH, SKY_VH);
  skyBuf.vGradient(0, 0, SKY_BUFFER_WIDTH, SKY_VH, art.skyStops, 22);
  const sky = toTexture(skyBuf, `cabin-sky-${bg}`);

  // 云（透明平铺层）
  let clouds: PixelTexture | null = null;
  if (art.clouds) {
    const buf = new PixelBuffer(TILE_VW, SKY_VH);
    const snap = matrixToRgba(CLOUD_ROWS, art.clouds.palette);
    for (let i = 0; i < art.clouds.count; i++) {
      const x = Math.floor(prng() * TILE_VW);
      const y = 6 + Math.floor(prng() * 64);
      buf.stampTiled(snap, TILE_VW, x, y, art.clouds.scale);
    }
    clouds = tiled(toTexture(buf, `cabin-clouds-${bg}`));
  }

  // 星（静态星野，闪烁交给星尘粒子）
  let stars: PixelTexture | null = null;
  if (art.stars) {
    const buf = new PixelBuffer(TILE_VW, SKY_VH);
    for (let i = 0; i < art.stars; i++) {
      const x = Math.floor(prng() * TILE_VW);
      const y = Math.floor(prng() * (SKY_VH - 30));
      const bright = prng();
      for (const dx of [-TILE_VW, 0, TILE_VW]) {
        buf.setPx(x + dx, y, 0xffffff);
        if (bright > 0.75) {
          buf.setPx(x + dx - 1, y, 0xcfe3ff);
          buf.setPx(x + dx + 1, y, 0xcfe3ff);
          buf.setPx(x + dx, y - 1, 0xcfe3ff);
          buf.setPx(x + dx, y + 1, 0xcfe3ff);
        }
      }
    }
    stars = tiled(toTexture(buf, `cabin-stars-${bg}`));
  }

  // 星云（加法混合平铺层）+ 环形行星
  let nebula: PixelTexture | null = null;
  if (art.nebula) {
    const buf = new PixelBuffer(TILE_VW, SKY_VH);
    for (const blob of art.nebula.blobs) {
      for (const dx of [-TILE_VW, 0, TILE_VW]) {
        buf.radialEllipse(blob.x + dx, blob.y, blob.r, blob.r * 0.72, art.nebula.color, art.nebula.alpha, 1.8);
        buf.radialEllipse(blob.x + dx, blob.y, blob.r * 0.5, blob.r * 0.36, art.nebula.color, art.nebula.alpha * 0.7, 2.2);
      }
    }
    drawRingedPlanet(buf, 386, 36);
    nebula = tiled(toTexture(buf, `cabin-nebula-${bg}`));
  }

  // 远景剪影（两层色带 = 大气透视）
  const farBuf = new PixelBuffer(TILE_VW, FAR_VH);
  const farCfg = art.far;
  const backWaves =
    farCfg.kind === 'dunes'
      ? [{ amp: 9, k: 2, p: 0.8 }, { amp: 5, k: 5, p: 2.2 }]
      : farCfg.kind === 'ridge'
        ? [{ amp: 12, k: 6, p: 0.4 }, { amp: 6, k: 13, p: 4.1 }]
        : [{ amp: 7, k: 3, p: 1.3 }, { amp: 4, k: 7, p: 0.5 }];
  const frontWaves =
    farCfg.kind === 'dunes'
      ? [{ amp: 7, k: 3, p: 2.6 }, { amp: 4, k: 8, p: 1.1 }]
      : farCfg.kind === 'ridge'
        ? [{ amp: 9, k: 9, p: 2.0 }, { amp: 4, k: 17, p: 0.9 }]
        : [{ amp: 6, k: 5, p: 2.1 }, { amp: 3, k: 11, p: 4.0 }];
  const backH = bandHeights(TILE_VW, farCfg.kind === 'dunes' ? 22 : 20, backWaves);
  const frontH = bandHeights(TILE_VW, farCfg.kind === 'dunes' ? 14 : 12, frontWaves);
  fillBand(farBuf, backH, farCfg.back);
  fillBand(farBuf, frontH, farCfg.front);
  if (farCfg.treeColor && (farCfg.trees || farCfg.spires)) {
    const n = farCfg.trees ?? farCfg.spires ?? 0;
    for (let i = 0; i < n; i++) {
      const x = Math.floor(prng() * TILE_VW);
      stampSilhouetteWrapped(
        farBuf,
        x,
        FAR_VH - backH[x] + 2,
        farCfg.treeColor,
        farCfg.trees ? 'tree' : 'spire',
        prng,
      );
    }
  }
  const far = tiled(toTexture(farBuf, `cabin-far-${bg}`));

  // 中景主体：**分段拼接**。每段 4 屏宽、种子不同 → 8 屏世界不出现 1 屏周期的重复。
  const midCountFactor = PROP_TILE_VW / TILE_VW;
  const midDefs = scaleElementDefs(art.mid, midCountFactor);
  const mid: PixelTexture[] = [];
  for (let v = 0; v < MID_SEG_VARIANTS; v++) {
    const buf = new PixelBuffer(PROP_TILE_VW, MID_VH);
    stampElements(buf, midDefs, mulberry32(0x51d3 + bg.length * 7919 + v * 104729));
    mid.push(tiled(toTexture(buf, `cabin-mid-${bg}-${v}`)));
  }

  // 地面（色带渐变 + 斑点 + 田垄/溪水）
  const groundBuf = new PixelBuffer(GROUND_TILE_VW, GROUND_VH);
  const g = art.ground;
  groundBuf.vGradient(
    0,
    0,
    GROUND_TILE_VW,
    GROUND_VH,
    [
      { t: 0, color: g.top },
      { t: 0.18, color: g.base },
      { t: 1, color: g.bottom },
    ],
    18,
  );
  if (g.mow) {
    for (let y = 14; y < GROUND_VH; y += 14) {
      groundBuf.rect(0, y, GROUND_TILE_VW, 2, shade(g.base, 0.9));
    }
  }
  if (g.water) {
    groundBuf.rect(0, g.water.from, GROUND_TILE_VW, g.water.to - g.water.from, g.water.color);
    groundBuf.rect(0, g.water.to - 5, GROUND_TILE_VW, 5, g.water.deep);
    for (let x = 0; x < GROUND_TILE_VW; x++) {
      if (bayer4(x, g.water.from) > 0.55) groundBuf.setPx(x, g.water.from - 1, g.water.color);
    }
    for (let i = 0; i < 26; i++) {
      const x = Math.floor(prng() * GROUND_TILE_VW);
      const y = g.water.from + 2 + Math.floor(prng() * (g.water.to - g.water.from - 6));
      const w = 3 + Math.floor(prng() * 4);
      for (const dx of [-GROUND_TILE_VW, 0, GROUND_TILE_VW]) {
        groundBuf.rect(x + dx, y, w, 1, g.water.ripple);
      }
    }
  }
  for (let i = 0; i < 110; i++) {
    const x = Math.floor(prng() * GROUND_TILE_VW);
    const y = Math.floor(prng() * GROUND_VH);
    if (g.water && y >= g.water.from - 1 && y <= g.water.to) continue;
    groundBuf.setPx(x, y, g.speckles[i % g.speckles.length]);
  }
  const ground = tiled(toTexture(groundBuf, `cabin-ground-${bg}`));

  // 近景细节（花草小石子贴地条）：同样分段，近景 parallax 最大、行程最长，段数取 3
  const nearDefs = scaleElementDefs(art.near, midCountFactor);
  const near: PixelTexture[] = [];
  for (let v = 0; v < NEAR_SEG_VARIANTS; v++) {
    const buf = new PixelBuffer(PROP_TILE_VW, NEAR_VH);
    stampElements(buf, nearDefs, mulberry32(0x9a7c + bg.length * 7919 + v * 104729));
    near.push(tiled(toTexture(buf, `cabin-near-${bg}-${v}`)));
  }

  return { sky, clouds, stars, nebula, far, mid, near, ground };
}

function getThemeTextures(bg: CabinBackgroundId): ThemeTextures {
  let texs = themeCache.get(bg);
  if (!texs) {
    texs = buildThemeTextures(bg);
    themeCache.set(bg, texs);
  }
  return texs;
}

/* ------------------------------ 共享纹理 ------------------------------ */

let glowTex: PixelTexture | null = null;
function getGlowTexture(): PixelTexture {
  if (!glowTex) {
    const buf = new PixelBuffer(64, 64);
    buf.radialEllipse(32, 32, 31, 31, 0xffffff, 1, 2.2);
    glowTex = toTexture(buf, 'cabin-glow');
  }
  return glowTex;
}

let shadowTex: PixelTexture | null = null;
function getShadowTexture(): PixelTexture {
  if (!shadowTex) {
    const buf = new PixelBuffer(48, 14);
    buf.radialEllipse(24, 7, 23, 6.5, 0x0f172a, 0.55, 1.4);
    shadowTex = toTexture(buf, 'cabin-shadow');
  }
  return shadowTex;
}

/**
 * G3-6 边界渐隐纹理：左→右 由深蓝不透明渐变到全透明（8x64，横向拉伸使用）。
 * 程序化生成，与其他像素资源一致（零外部素材）。
 */
let edgeFadeTex: PixelTexture | null = null;
function getEdgeFadeTexture(): PixelTexture {
  if (!edgeFadeTex) {
    const w = 8;
    const h = 64;
    const buf = new PixelBuffer(w, h);
    for (let x = 0; x < w; x++) {
      const f = 1 - x / (w - 1);
      for (let y = 0; y < h; y++) buf.setPx(x, y, 0x0b1030, f * f);
    }
    edgeFadeTex = toTexture(buf, 'cabin-edge-fade');
  }
  return edgeFadeTex;
}

interface ParticleArtDef {
  rows: readonly string[];
  palette: Record<string, number>;
}

function particleArt(k: ParticleKindDef): ParticleArtDef {
  switch (k.kind) {
    case 'firefly':
      return { rows: STARDUST_ROWS, palette: { w: k.color, W: k.centerColor ?? 0xffffff } };
    case 'petal':
      return { rows: PETAL_ROWS, palette: { P: k.color } };
    case 'sparkle':
      return { rows: SPARKLE_ROWS, palette: { w: k.color, W: k.centerColor ?? 0xffffff } };
    case 'seed':
      return { rows: SEED_ROWS, palette: { w: k.color, W: 0xffffff, t: 0xd0b268 } };
    case 'stardust':
      return { rows: STARDUST_ROWS, palette: { w: k.color, W: k.centerColor ?? 0xffffff } };
    case 'snow':
      return { rows: SNOWFLAKE_ROWS, palette: { w: 0xffffff } };
    default:
      return { rows: STARDUST_ROWS, palette: { w: 0xffffff, W: 0xffffff } };
  }
}

/* ------------------------------ 场景 ------------------------------ */

export interface CabinSceneCallbacks {
  /** 点击小人/宠物时回调（台词由 React 层经 resolveDialogue 决定后调 speak()）。 */
  onSpeak?: (speaker: DialogueSpeaker) => void;
}

export interface CabinScene {
  resize(width: number, height: number): void;
  setConfig(config: CabinConfig): void;
  /** 在指定角色头顶弹出气泡（含「虚拟演绎」角标），约 3.6s 自动消失。 */
  speak(speaker: DialogueSpeaker, text: string): void;
  destroy(): void;
}

export interface CreateCabinSceneOptions {
  canvas: HTMLCanvasElement;
  config: CabinConfig;
  callbacks?: CabinSceneCallbacks;
  /**
   * W11 衔接（个性化像素角色）：自定义小人行走矩阵 + 调色板。
   *
   * 契约（务必保持）：
   *  - **可选**，不传时逐像素回退到 `PERSON_WALK_FRAMES` / `PERSON_PALETTE`，
   *    既有行为完全不变（本参数是纯追加，不改任何既有函数签名语义）。
   *  - 传入时需至少 2 帧（两帧行走机制）；缺帧或空数组会**明确回退默认**并保持画面可用。
   *  - 像素矩阵/调色板的生成规则属于 W11（`components/avatar/`），
   *    本渲染层只负责「认矩阵」，不参与角色设计。
   */
  personWalkFrames?: readonly (readonly string[])[];
  personPalette?: PixelPalette;
}

interface ParticleState {
  spr: Sprite;
  kind: string;
  phase: number;
  speed: number;
  fx: number;
  fy: number;
}

interface GlowState {
  spr: Sprite;
  base: number;
  phase: number;
}

// PARTICLE_POOL 由 cabinPixelArt 单一出处导出（纯数据、无 pixi 依赖），此处复用。

export async function createCabinScene(options: CreateCabinSceneOptions): Promise<CabinScene> {
  const app = new Application();
  await app.init({
    canvas: options.canvas,
    width: window.innerWidth,
    height: window.innerHeight,
    background: 0x0b1030,
    antialias: false, // 像素风：关抗锯齿
    resolution: 1, // 像素风：1x 渲染 + CSS image-rendering: pixelated
    autoDensity: false,
  });

  let width = window.innerWidth;
  let height = window.innerHeight;
  let groundY = Math.round(height * 0.58);
  let worldScale = height / VIRTUAL_H;
  let config: CabinConfig = { ...options.config };
  let destroyed = false;
  let timeMs = 0;
  let bubbleUntil = -1;
  let bubbleSpeaker: DialogueSpeaker = 'person';
  /** 行走目标：**世界 x**（虚拟像素）+ 地面带内比例 depth∈[0,1]。 */
  const walkTarget = { x: 0, y: 0 };
  let walking = false;
  /** 相机 x（虚拟像素，世界坐标系）：场景的横向视口原点。G3 新增。 */
  let cameraX = 0;
  /** 角色是否已完成首次落位（只落一次，后续 resize 不重置位置）。G3 新增。 */
  let actorsSeeded = false;

  /* ---- 主题纹理（先于图层创建；模块级缓存，切背景零重建） ---- */

  let currentThemeId: CabinBackgroundId = config.background;
  let currentTheme: ThemeArt = THEME_ART[config.background];
  let currentTextures: ThemeTextures = getThemeTextures(config.background);

  /* ---- 背景图层（静态预渲染纹理 + 视差） ---- */

  const bgLayer = new Container();
  const skySpr = new Sprite(currentTextures.sky.texture);
  const starLayer = new TilingSprite({
    texture: (currentTextures.stars ?? currentTextures.sky).texture,
    width: 8,
    height: 8,
  });
  starLayer.visible = !!currentTextures.stars;
  const nebulaLayer = new TilingSprite({
    texture: (currentTextures.nebula ?? currentTextures.sky).texture,
    width: 8,
    height: 8,
  });
  nebulaLayer.visible = !!currentTextures.nebula;
  nebulaLayer.blendMode = 'add';
  const cloudLayer = new TilingSprite({
    texture: (currentTextures.clouds ?? currentTextures.sky).texture,
    width: 8,
    height: 8,
  });
  cloudLayer.visible = !!currentTextures.clouds;
  const farLayer = new TilingSprite({ texture: currentTextures.far.texture, width: 8, height: 8 });
  const groundLayer = new TilingSprite({ texture: currentTextures.ground.texture, width: 8, height: 8 });
  /**
   * 中景/近景**分段精灵池**：每层多个槽位，每槽一张不同种子的道具段纹理。
   * 相机移动时把槽位按屏幕偏移摆开（而不是靠 tilePosition 重置），
   * 于是相邻屏看到的道具不重置，8 屏世界不再有「每屏一样」的重复感。
   *
   * 旧的单张 TilingSprite 已删除 —— 它就是重复感的根源。
   */
  const makeSegPool = (slots: number, first: PixelTexture): TilingSprite[] => {
    const pool: TilingSprite[] = [];
    for (let i = 0; i < slots; i++) {
      const s = new TilingSprite({ texture: first.texture, width: 8, height: 8 });
      s.visible = false;
      pool.push(s);
    }
    return pool;
  };
  const midSegs = makeSegPool(MID_SEG_SLOTS, currentTextures.mid[0]);
  const nearSegs = makeSegPool(NEAR_SEG_SLOTS, currentTextures.near[0]);
  /** 中景/近景当前需要的可见段数（resize 时重算）。 */
  let midSegNeed = 1;
  let nearSegNeed = 1;
  const ambientGlow = new Sprite(getGlowTexture().texture);
  ambientGlow.anchor.set(0.5, 0.5);
  ambientGlow.blendMode = 'add';
  bgLayer.addChild(
    skySpr,
    starLayer,
    nebulaLayer,
    cloudLayer,
    farLayer,
    groundLayer,
    ...midSegs,
    ...nearSegs,
    ambientGlow,
  );

  /* ---- 边界渐隐（G3-6：世界左右缘的视觉收束，叠在场景最上层） ---- */

  const edgeLayer = new Container();
  const edgeL = new Sprite(getEdgeFadeTexture().texture);
  edgeL.anchor.set(0, 0);
  const edgeR = new Sprite(getEdgeFadeTexture().texture);
  edgeR.anchor.set(0, 0);
  edgeR.scale.x = -1; // 镜像：纹理不透明端落在屏幕右缘（见 layoutLayers 里的 width 赋值）
  // 初始隐藏：alpha 由 tick 里的 edgeFadeAlpha 每帧驱动。出生点在世界中部，
  // 若默认 alpha=1 则第一帧会看到两条满不透明的黑边。
  edgeLayer.visible = false;
  edgeL.alpha = 0;
  edgeR.alpha = 0;
  edgeLayer.addChild(edgeL, edgeR);

  /* ---- 房屋（矩阵精灵 + 窗光 + 落地阴影） ---- */

  const houseC = new Container();
  const houseInner = new Container();
  houseC.addChild(houseInner);
  const houseGlows: GlowState[] = [];

  /* ---- 小人与宠物 ---- */

  const actorLayer = new Container();

  const person = new Container();
  const personShadow = new Sprite(getShadowTexture().texture);
  personShadow.anchor.set(0.5, 0.5);
  personShadow.alpha = 0.28;
  const personBody = new Sprite();
  personBody.anchor.set(0.5, 1);
  personBody.roundPixels = true;
  const nameTagC = new Container();
  const namePlateG = new Graphics();
  const nameTag = new Text({
    text: options.config.personName,
    style: {
      fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      fontSize: 11,
      fill: 0xf8fafc,
      fontWeight: 'bold',
    },
  });
  nameTag.anchor.set(0.5, 0.5);
  nameTag.position.set(0, -9.5);
  nameTagC.addChild(namePlateG, nameTag);
  nameTagC.position.set(0, -28);
  person.addChild(personShadow, personBody, nameTagC);

  const pet = new Container();
  const petShadow = new Sprite(getShadowTexture().texture);
  petShadow.anchor.set(0.5, 0.5);
  petShadow.alpha = 0.24;
  const petBody = new Sprite();
  petBody.anchor.set(0.5, 1);
  petBody.roundPixels = true;
  pet.addChild(petShadow, petBody);
  actorLayer.addChild(pet, person);

  /* ---- 粒子层 ---- */

  const particleLayer = new Container();
  const particles: ParticleState[] = [];
  const particlePrng = mulberry32(0xfeed);
  for (let i = 0; i < PARTICLE_POOL; i++) {
    const spr = new Sprite();
    spr.visible = false;
    particleLayer.addChild(spr);
    particles.push({
      spr,
      kind: '',
      phase: particlePrng() * Math.PI * 2,
      speed: 0.7 + particlePrng() * 0.6,
      fx: particlePrng(),
      fy: particlePrng(),
    });
  }

  /* ---- 气泡（像素直角框 + 1px 深色描边 + 阶梯尾巴，含「虚拟演绎」角标） ---- */

  const bubbleLayer = new Container();
  const bubbleRoot = new Container();
  const bubbleG = new Graphics();
  const bubbleText = new Text({
    text: '',
    style: {
      fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
      fontSize: 13,
      fill: 0x1f2937,
      wordWrap: true,
      wordWrapWidth: 190,
      breakWords: true,
      lineHeight: 19,
    },
  });
  const bubbleBadge = new Text({
    text: '虚拟演绎',
    style: { fontFamily: '"Microsoft YaHei", sans-serif', fontSize: 9, fill: 0x64748b },
  });
  bubbleRoot.addChild(bubbleG, bubbleText, bubbleBadge);
  bubbleRoot.visible = false;
  bubbleLayer.addChild(bubbleRoot);

  app.stage.addChild(bgLayer, houseC, actorLayer, particleLayer, edgeLayer, bubbleLayer);
  app.stage.eventMode = 'static';
  app.stage.hitArea = app.screen;

  /* ---- 精灵纹理 ---- */

  const houseTexture = (house: CabinHouseId): PixelTexture => {
    const art = CABIN_HOUSE_ART[house];
    return spriteFromMatrix(
      art.rows,
      art.palette,
      { autoOutline: art.outline, label: `house-${house}` },
      `house:${house}`,
    );
  };

  // W11 注入：外部传入的个性化小人矩阵优先；不传（或帧数不足）时逐像素回退默认。
  const customFrames = options.personWalkFrames;
  const useCustomPerson = Array.isArray(customFrames) && customFrames.length >= 2;
  const personFrames = useCustomPerson ? customFrames : PERSON_WALK_FRAMES;
  const personPalette = useCustomPerson ? (options.personPalette ?? PERSON_PALETTE) : PERSON_PALETTE;
  // 缓存 key 带来源标记：自定义角色与默认小人各走各的缓存，避免切换角色后取到旧纹理。
  const personTag = useCustomPerson ? 'person-custom' : 'person';

  const walkFrames: Texture[] = personFrames.map((rows, i) =>
    spriteFromMatrix(
      rows,
      personPalette,
      { autoOutline: 0x24344d, label: `${personTag}-${i}` },
      `${personTag}:${i}`,
    ).texture,
  );

  const petFrame = (colorId: string, base: number, frame: number): Texture =>
    spriteFromMatrix(
      PET_WALK_FRAMES[frame],
      petPalette(base),
      { autoOutline: shade(base, 0.4), label: `pet-${colorId}-${frame}` },
      `pet:${colorId}:${frame}`,
    ).texture;

  const petColorMeta = () => PET_COLORS.find((c) => c.id === config.petColor) ?? PET_COLORS[0];

  /* ---- 布局 ---- */

  /**
   * 视口宽度（虚拟像素）。相机钳制、图层宽度、点击反投影全部用它。
   * G3：这是相机与世界比较的唯一尺度，避免屏幕像素与世界像素混用。
   */
  const viewWidth = () => Math.ceil(width / worldScale);

  /**
   * 相机走满世界时，某视差层需要扫过的虚拟距离。
   * 背景层（云/星/远景）只需要盖住视口 —— 它们本来就是无缝平铺的远景剪影，
   * 重复不刺眼；但中景/近景**道具**的重复非常刺眼，所以改成「分段拼接」。
   */
  const layerTravel = (parallax: number) => WORLD.width * parallax;

  /** 需要多少个道具段才能盖住「相机全程行程 + 当前视口」。 */
  const segCountFor = (parallax: number) =>
    Math.ceil((layerTravel(parallax) + viewWidth() + PROP_TILE_VW * 0.5) / PROP_TILE_VW);

  /**
   * 按相机位移摆开道具段。
   * 段 i 的屏幕 x = (i*PROP_TILE_VW - cameraX*parallax) * worldScale。
   * 只摆**可见**的段（need 个），多余的槽位隐藏 —— 既省绘制也免去
   * 超宽屏下把 4 个变体循环用完导致的重复。
   */
  const syncSegments = (
    pool: TilingSprite[],
    need: number,
    parallax: number,
    localH: number,
    y: number,
  ) => {
    for (let i = 0; i < pool.length; i++) {
      const s = pool[i];
      const on = i < need;
      s.visible = on;
      if (!on) continue;
      s.scale.set(worldScale);
      s.width = PROP_TILE_VW;
      s.height = localH;
      s.position.set(Math.round((i * PROP_TILE_VW - cameraX * parallax) * worldScale), y);
    }
  };

  const layoutLayers = () => {
    skySpr.position.set(0, 0);
    skySpr.width = width;
    skySpr.height = groundY;
    const vw = viewWidth();
    const lw = layerVirtualWidth(vw, PARALLAX_MARGIN / 2);
    const lwTight = layerVirtualWidth(vw, 1);
    const tileLayers: Array<[TilingSprite, number, number, number, number]> = [
      // [layer, localWidth, localHeight, y(屏幕 px), xMargin]
      [starLayer, lw, SKY_VH, 0, PARALLAX_MARGIN / 2],
      [nebulaLayer, lw, SKY_VH, 0, PARALLAX_MARGIN / 2],
      [cloudLayer, lw, SKY_VH, 0, PARALLAX_MARGIN / 2],
      [farLayer, lw, FAR_VH, groundY - FAR_VH * worldScale, PARALLAX_MARGIN / 2],
      [groundLayer, lwTight, GROUND_VH, groundY, 1],
    ];
    for (const [layer, localW, localH, y, margin] of tileLayers) {
      layer.scale.set(worldScale);
      layer.width = localW;
      layer.height = localH;
      layer.position.set(-margin, y);
    }

    // 中景/近景道具段（分段拼接，取代单张平铺）
    midSegNeed = Math.min(midSegs.length, segCountFor(PARALLAX.mid));
    nearSegNeed = Math.min(nearSegs.length, segCountFor(PARALLAX.near));
    syncSegments(midSegs, midSegNeed, PARALLAX.mid, MID_VH, groundY - MID_VH * worldScale);
    syncSegments(nearSegs, nearSegNeed, PARALLAX.near, NEAR_VH, groundY + 4 * worldScale);
    const glow = currentTheme.ambientGlow;
    ambientGlow.visible = !!glow;
    if (glow) {
      ambientGlow.position.set(glow.fx * width, glow.fy * height);
      ambientGlow.scale.set((glow.rx * 2 * width) / 64, (glow.ry * 2 * height) / 64);
      ambientGlow.alpha = glow.alpha;
      ambientGlow.tint = glow.color;
    }
    // G3-6：边界渐隐条铺在屏幕两侧，宽度固定为虚拟像素（不随相机缩放变化）。
    const fadeW = EDGE_FADE_VW * worldScale;
    edgeL.position.set(0, 0);
    edgeL.width = fadeW;
    edgeL.height = height;
    edgeR.position.set(width, 0);
    edgeR.width = fadeW; // Pixi `_setWidth` 保留 scale.x 的符号，这里镜像仍为负
    edgeR.height = height;
  };

  const layoutHouse = () => {
    // G2-3：按房屋矩阵高度归一化缩放，使房屋屏显高度稳定为小人的 HOUSE_PERSON_RATIO 倍（约 2.0）。
    houseC.scale.set(worldScale * houseScaleFactor(config.house));
    syncHouseToCamera();
  };

  /**
   * G3-4：房屋跟随相机投影。**每帧都要重算** —— 旧实现只在 redraw() 里写一次
   * `width*0.36`，相机一动房屋就与人物脱节（“房屋漂移”）。
   * 房屋的世界坐标恒为 WORLD.houseX，所以它不漂移，只是随镜头平移。
   */
  const syncHouseToCamera = () => {
    houseC.position.set(
      Math.round(worldToScreenX(WORLD.houseX, cameraX) * worldScale),
      Math.round(groundY + (height - groundY) * 0.5),
    );
  };

  /* ---- 人物/宠物：世界坐标（x）+ 地面带比例（depth） ---- */

  let personWorldX = WORLD.spawnX;
  let personDepth = 0.55;
  let personDir = 1;
  let petWorldX = WORLD.spawnX - 48;
  let petDepth = 0.55 + PET_DEPTH_OFFSET;
  let petDir = 1;

  /** 可走 depth 区间：由当前高度换算，严格复现旧的 groundY+24 .. height-26。 */
  const depthLimits = () => {
    const band = Math.max(1, height - groundY);
    return {
      min: WALK_MARGIN_TOP_PX / band,
      max: 1 - WALK_MARGIN_BOTTOM_PX / band,
    };
  };

  /** depth（地面带比例）→ 屏幕 y。 */
  const depthToScreenY = (depth: number) => groundY + (height - groundY) * depth;

  /** G3-2：人物钳制到**世界**范围（旧版是钳到屏幕内 30px..width-30px）。 */
  const clampWalk = () => {
    personWorldX = clampToWorld(personWorldX);
    const { min, max } = depthLimits();
    personDepth = clamp(personDepth, min, max);
  };

  /**
   * 初始化/重算角色位置。
   * G3-2：**不再**在 `!walking` 时把人物重置到 `width*0.52`（那正是「停下就瞬移回中点」
   * 的根因）。首次调用落到 WORLD.spawnX；之后 resize / setConfig 只做钳制，不改坐标。
   */
  const layoutActors = () => {
    const ps = worldScale * PERSON_SCALE;
    if (!actorsSeeded) {
      actorsSeeded = true;
      personWorldX = clampToWorld(WORLD.spawnX);
      petWorldX = clampToWorld(personWorldX - 48);
      const { min, max } = depthLimits();
      personDepth = clamp(0.55, min, max);
      petDepth = clamp(personDepth + PET_DEPTH_OFFSET, min, max);
      walkTarget.x = personWorldX;
      walkTarget.y = personDepth;
      cameraX = clampCameraX(cameraTargetX(personWorldX, viewWidth()), viewWidth());
    }
    clampWalk();
    petWorldX = clampToWorld(petWorldX);
    person.scale.set(personDir * ps, ps);
    pet.scale.set(petDir * ps * 0.95, ps * 0.95);
    personShadow.scale.set(30 / 48, 7 / 14);
    petShadow.scale.set(26 / 48, 5.5 / 14);
  };

  const buildHouse = (house: CabinHouseId) => {
    houseInner.removeChildren();
    houseGlows.length = 0;
    const tex = houseTexture(house);
    const W = tex.width;
    const H = tex.height;

    const shadow = new Sprite(getShadowTexture().texture);
    shadow.anchor.set(0.5, 0.5);
    shadow.position.set(0, 1);
    shadow.scale.set((W * 0.92) / 48, 11 / 14);
    shadow.alpha = 0.22;

    const body = new Sprite(tex.texture);
    body.anchor.set(0.5, 1);
    body.roundPixels = true;

    houseInner.addChild(shadow, body);
    const glowDefs = HOUSE_GLOWS[house];
    glowDefs.forEach((def, i) => {
      const glow = new Sprite(getGlowTexture().texture);
      glow.anchor.set(0.5, 0.5);
      glow.blendMode = 'add';
      glow.tint = def.tint;
      glow.alpha = def.alpha;
      glow.scale.set(def.scale, def.scale);
      // 矩阵坐标（含 1px 描边偏移）→ 以房屋底部中心为原点
      glow.position.set(def.mx + 1 - W / 2, def.my + 1 - H);
      houseInner.addChild(glow);
      houseGlows.push({ spr: glow, base: def.alpha, phase: i * 1.37 });
    });
  };

  /* ---- 主题 / 粒子绑定 ---- */

  const bindParticles = () => {
    // G2-4：两类粒子从【同一共享池】取，count_a + count_b ≤ PARTICLE_POOL(60)，不是各 60。
    const sets: readonly ParticleKindDef[] =
      config.house === 'snowcave'
        ? [
            { kind: 'snow', count: 40, color: 0xffffff },
            { kind: 'stardust', count: 20, color: 0xcfe3ff, centerColor: 0xffffff, additive: true },
          ]
        : currentTheme.particles.kinds;
    // 每类预建一张共享纹理 + 统一 blendMode / 缩放。
    const drawn = sets.map((k) => {
      const def = particleArt(k);
      const tex = spriteFromMatrix(def.rows, def.palette, { label: `pt-${k.kind}` }, `pt:${k.kind}:${k.color}`).texture;
      const additive = k.kind === 'snow' ? false : (k.additive ?? false);
      const scale = k.kind === 'petal' || k.kind === 'seed' ? worldScale * 1.1 : worldScale * 0.9;
      return { tex, additive, scale };
    });
    let idx = 0;
    for (let si = 0; si < sets.length; si++) {
      const k = sets[si];
      for (let n = 0; n < k.count && idx < particles.length; n++, idx++) {
        const st = particles[idx];
        st.kind = k.kind;
        st.spr.visible = true;
        st.spr.texture = drawn[si].tex;
        st.spr.blendMode = drawn[si].additive ? 'add' : 'normal';
        st.spr.scale.set(drawn[si].scale);
        st.spr.alpha = 1;
      }
    }
    // 池内剩余槽位（合计 < PARTICLE_POOL 时）保持隐藏，避免上一主题粒子残留。
    for (; idx < particles.length; idx++) {
      particles[idx].kind = '';
      particles[idx].spr.visible = false;
    }
  };

  const applyTheme = () => {
    if (currentThemeId !== config.background) {
      currentThemeId = config.background;
      currentTheme = THEME_ART[config.background];
      currentTextures = getThemeTextures(config.background);
      skySpr.texture = currentTextures.sky.texture;
      starLayer.texture = (currentTextures.stars ?? currentTextures.sky).texture;
      starLayer.visible = !!currentTextures.stars;
      nebulaLayer.texture = (currentTextures.nebula ?? currentTextures.sky).texture;
      nebulaLayer.visible = !!currentTextures.nebula;
      cloudLayer.texture = (currentTextures.clouds ?? currentTextures.sky).texture;
      cloudLayer.visible = !!currentTextures.clouds;
      farLayer.texture = currentTextures.far.texture;
      groundLayer.texture = currentTextures.ground.texture;
      // 中景/近景分段：每槽一张不同变体（变体数 < 槽位数时才循环）
      midSegs.forEach((s, i) => {
        s.texture = currentTextures.mid[i % currentTextures.mid.length].texture;
      });
      nearSegs.forEach((s, i) => {
        s.texture = currentTextures.near[i % currentTextures.near.length].texture;
      });
    }
    bindParticles();
  };

  /* ---- 名牌（像素暗牌 + 1px 高光边） ---- */

  const updateNamePlate = () => {
    const tw = nameTag.width;
    namePlateG.clear();
    namePlateG.rect(-tw / 2 - 4, -16, tw + 8, 13).fill({ color: 0x0f172a, alpha: 0.72 });
    namePlateG.rect(-tw / 2 - 4, -16, tw + 8, 1).fill({ color: 0x94a3b8, alpha: 0.9 });
  };

  const redraw = () => {
    groundY = Math.round(height * 0.58);
    worldScale = height / VIRTUAL_H;
    applyTheme();
    // resize 后视口宽变了，相机必须重新钳制（否则可能停在世界之外）。
    cameraX = clampCameraX(cameraX, viewWidth());
    layoutLayers();
    layoutHouse();
    layoutActors();
    updateNamePlate();
  };

  redraw();
  buildHouse(config.house);
  {
    const meta = petColorMeta();
    petBody.texture = petFrame(meta.id, hexToNumber(meta.hex), 0);
  }

  /* ---- 交互 ---- */

  app.stage.on('pointertap', (e: FederatedPointerEvent) => {
    // G3-2 第 2 处 clamp（旧版把点击钳在屏幕内，屏幕外点不到）：
    // 先把屏幕坐标按 worldScale 换成虚拟像素，再反投影为世界坐标，最后钳到世界范围。
    const { min, max } = depthLimits();
    const screenVirtualX = e.global.x / worldScale;
    walkTarget.x = clampToWorld(screenToWorldX(screenVirtualX, cameraX));
    // Y 直接用屏幕 px → depth（groundY/height 是屏幕量，转 depth 后与 depthToScreenY 互逆）
    walkTarget.y = clamp((e.global.y - groundY) / Math.max(1, height - groundY), min, max);
    walking = true;
  });

  person.eventMode = 'static';
  person.cursor = 'pointer';
  person.on('pointertap', (e: FederatedPointerEvent) => {
    e.stopPropagation();
    options.callbacks?.onSpeak?.('person');
  });

  pet.eventMode = 'static';
  pet.cursor = 'pointer';
  pet.on('pointertap', (e: FederatedPointerEvent) => {
    e.stopPropagation();
    options.callbacks?.onSpeak?.('pet');
  });

  /* ---- 气泡显示 ---- */

  const positionBubble = () => {
    if (!bubbleRoot.visible) return;
    const anchorY = bubbleSpeaker === 'person'
      ? person.y - 27 * person.scale.y - 6
      : pet.y - 18 * pet.scale.y - 2;
    const halfW = bubbleG.width / 2 + 8;
    bubbleRoot.position.set(
      clamp(bubbleSpeaker === 'person' ? person.x : pet.x, halfW, Math.max(width - halfW, halfW)),
      Math.max(bubbleG.height + 18, anchorY),
    );
  };

  const showBubble = (speaker: DialogueSpeaker, text: string) => {
    bubbleSpeaker = speaker;
    bubbleText.text = text;
    const estW = Math.round(clamp(text.length * 14 + 36, 96, 214));
    const estLines = Math.max(1, Math.ceil((text.length * 14) / 178));
    const estH = estLines * 20 + 26;
    const w = estW;
    const h = estH;
    const x0 = -Math.round(w / 2);
    const y0 = -h - 6;
    bubbleG.clear();
    // 直角像素框：外层 1px 深色描边 + 白底（无圆角、无抗锯齿）
    bubbleG.rect(x0 - 1, y0 - 1, w + 2, h + 2).fill(0x3a4a63);
    bubbleG.rect(x0, y0, w, h).fill({ color: 0xffffff, alpha: 0.97 });
    // 阶梯像素尾巴（指向说话者）
    bubbleG.rect(-7, y0 + h - 1, 14, 2).fill(0x3a4a63);
    bubbleG.rect(-6, y0 + h - 1, 12, 1).fill({ color: 0xffffff, alpha: 0.97 });
    bubbleG.rect(-4, y0 + h + 1, 8, 2).fill(0x3a4a63);
    bubbleG.rect(-3, y0 + h + 1, 6, 1).fill({ color: 0xffffff, alpha: 0.97 });
    bubbleG.rect(-1, y0 + h + 3, 3, 2).fill(0x3a4a63);
    bubbleG.rect(0, y0 + h + 3, 1, 1).fill({ color: 0xffffff, alpha: 0.97 });
    bubbleText.position.set(x0 + 10, y0 + 6);
    bubbleBadge.position.set(x0 + w - bubbleBadge.width - 8, y0 + h - 13);
    bubbleRoot.visible = true;
    bubbleUntil = timeMs + BUBBLE_MS;
    positionBubble();
  };

  /* ---- 粒子更新 ---- */

  const updateParticles = () => {
    for (const st of particles) {
      if (!st.spr.visible) continue;
      const t = timeMs;
      const spr = st.spr;
      switch (st.kind) {
        case 'firefly': {
          const bx = st.fx * width;
          const by = groundY + 14 + st.fy * (height - groundY - 44);
          spr.position.set(
            Math.round(bx + Math.sin(t * 0.00035 * st.speed + st.phase) * 42),
            Math.round(by + Math.cos(t * 0.00027 + st.phase * 1.7) * 20),
          );
          spr.alpha = 0.2 + 0.8 * Math.max(0, Math.sin(t * 0.0012 + st.phase * 3));
          break;
        }
        case 'petal':
        case 'seed': {
          const fall = st.kind === 'petal' ? 0.02 : 0.013;
          spr.position.set(
            Math.round(st.fx * width + Math.sin(t * 0.0006 + st.phase) * 36),
            Math.round(((st.fy * height + t * fall * st.speed) % (height + 20)) - 10),
          );
          spr.alpha = 0.85;
          break;
        }
        case 'sparkle': {
          const waterY = groundY + (height - groundY) * 0.36;
          spr.position.set(Math.round(st.fx * width), Math.round(waterY - 6 + st.fy * 14));
          spr.alpha = 0.1 + 0.9 * Math.abs(Math.sin(t * 0.002 + st.phase * 2));
          break;
        }
        case 'stardust': {
          spr.position.set(Math.round(st.fx * width), Math.round(st.fy * groundY * 0.96));
          spr.alpha = 0.15 + 0.85 * Math.abs(Math.sin(t * 0.0015 + st.phase * 2));
          break;
        }
        case 'snow': {
          spr.position.set(
            Math.round(st.fx * width + Math.sin(t * 0.0008 + st.phase) * 18),
            Math.round(((st.fy * height + t * 0.03 * st.speed) % (height + 10)) - 5),
          );
          spr.alpha = 0.9;
          break;
        }
        default:
          break;
      }
    }
  };

  /* ---- 主循环 ---- */

  const tick = (ticker: Ticker) => {
    const dt = Math.min(ticker.deltaMS, 60);
    timeMs += dt;
    const ps = worldScale * PERSON_SCALE;

    // G3-1/2：行走在**世界坐标**里，dx 单位是虚拟像素（不再受屏幕宽度钳制）。
    if (walking) {
      const ease = 1 - Math.pow(0.0018, dt / 1000);
      const dx = walkTarget.x - personWorldX;
      const dDepth = walkTarget.y - personDepth;
      if (Math.abs(dx) * worldScale < 4 && Math.abs(dDepth) * (height - groundY) < 4) {
        walking = false;
      } else {
        personWorldX += dx * ease;
        personDepth += dDepth * ease;
        if (Math.abs(dx) * worldScale > 1) personDir = Math.sign(dx) || 1;
        clampWalk(); // 撞到世界边缘就停住（不反弹，边界靠渐隐收束）
      }
    }

    // G3-5：相机 lerp 平滑跟随（帧率无关指数插值）。
    // 再钳两道：① 世界范围内 ② 人物必在视口内（否则快走时相机会滞后把人甩出画面）。
    const vw = viewWidth();
    const camTarget = cameraTargetX(personWorldX, vw);
    cameraX = clampCameraToPerson(lerpCameraX(cameraX, camTarget, dt), personWorldX, vw);
    cameraX = clampCameraX(cameraX, vw);
    syncHouseToCamera();

    // 走路两帧动画（换帧）；显示位置取整到像素网格
    const frame = walking ? Math.floor(timeMs / 150) % 2 : 0;
    personBody.texture = walkFrames[frame];
    const bob = walking && frame === 1 ? Math.max(1, Math.round(worldScale * 0.5)) : 0;
    person.scale.set(personDir * ps, ps);
    person.position.set(
      Math.round(worldToScreenX(personWorldX, cameraX) * worldScale),
      Math.round(depthToScreenY(personDepth) - bob),
    );
    nameTagC.scale.x = personDir; // 名牌不随身体翻面镜像

    // 宠物跟随（世界坐标 + 相机投影）+ 小跳 + 两帧动画（换色 = 调色板重生成纹理）
    const followX = personWorldX - personDir * 48;
    const followDepth = personDepth + PET_DEPTH_OFFSET;
    const petEase = 1 - Math.pow(0.004, dt / 1000);
    const pdx = followX - petWorldX;
    petWorldX = clampToWorld(petWorldX + pdx * petEase);
    // 小跳幅度、跟随偏移阈值均以**屏幕 px** 为参照（与旧实现一致），
    // 因此换算到世界/深度单位时必须乘/除 worldScale，否则 1080p 下小跳会变成 1/4。
    const hopDepth = walking ? (Math.abs(Math.sin(timeMs * 0.03)) * 5 * worldScale) / (height - groundY) : 0;
    petDepth += (followDepth - hopDepth - petDepth) * petEase;
    if (Math.abs(pdx) * worldScale > 6) petDir = Math.sign(pdx) || 1;
    const petMoving = walking || Math.abs(pdx) * worldScale > 2;
    const petFrameIdx = petMoving ? Math.floor(timeMs / 220) % 2 : 0;
    const meta = petColorMeta();
    petBody.texture = petFrame(meta.id, hexToNumber(meta.hex), petFrameIdx);
    pet.scale.set(petDir * ps * 0.95, ps * 0.95);
    pet.position.set(
      Math.round(worldToScreenX(petWorldX, cameraX) * worldScale),
      Math.round(depthToScreenY(petDepth)),
    );

    // G3-3：视差。相机位移直接驱动各层 tilePosition（repeat 无限平铺）；
    // ground=1.0 与人物同速（“站在地面上移动”的实感来源），near=1.15 略快。
    const drift = cloudLayer.visible ? timeMs * 0.003 : 0;
    cloudLayer.tilePosition.x = drift - cameraX * PARALLAX.cloud;
    starLayer.tilePosition.x = -cameraX * PARALLAX.star;
    nebulaLayer.tilePosition.x = -cameraX * PARALLAX.nebula;
    farLayer.tilePosition.x = -cameraX * PARALLAX.far;
    groundLayer.tilePosition.x = -cameraX * PARALLAX.ground;
    // 中景/近景不走 tilePosition —— 按段位移拼接（tilePosition 重置正是重复感的来源）
    syncSegments(midSegs, midSegNeed, PARALLAX.mid, MID_VH, groundY - MID_VH * worldScale);
    syncSegments(nearSegs, nearSegNeed, PARALLAX.near, NEAR_VH, groundY + 4 * worldScale);

    // G3-6：边界渐隐强度（相机距世界左右缘越近越强）。
    const fade = edgeFadeAlpha(cameraX, vw);
    edgeL.alpha = fade;
    edgeR.alpha = fade;
    edgeLayer.visible = fade > 0.002;

    // 窗光/篝火低频呼吸（火光类 flicker 全部轻微起伏）
    for (const g of houseGlows) {
      g.spr.alpha = g.base * (0.86 + 0.14 * Math.sin(timeMs * 0.005 + g.phase));
    }

    updateParticles();

    if (bubbleRoot.visible) {
      if (timeMs > bubbleUntil) {
        bubbleRoot.visible = false;
      } else {
        positionBubble();
      }
    }
  };
  app.ticker.add(tick);

  /* ---- 对外 API ---- */

  return {
    resize(w: number, h: number) {
      if (destroyed) return;
      width = w;
      height = h;
      app.renderer.resize(w, h);
      app.stage.hitArea = app.screen;
      redraw();
      bindParticles();
    },
    setConfig(next: CabinConfig) {
      if (destroyed) return;
      const prev = config;
      config = { ...next };
      const houseChanged = prev.house !== next.house;
      if (prev.background !== next.background || houseChanged) {
        redraw();
      } else {
        layoutActors();
      }
      if (houseChanged) buildHouse(next.house);
      if (prev.personName !== next.personName) {
        nameTag.text = next.personName;
        updateNamePlate();
      }
      if (prev.petColor !== next.petColor) {
        const m = petColorMeta();
        petBody.texture = petFrame(m.id, hexToNumber(m.hex), 0);
      }
      if (houseChanged) bindParticles(); // 雪洞小屋切换飘雪
    },
    speak(speaker: DialogueSpeaker, text: string) {
      if (destroyed) return;
      showBubble(speaker, text);
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      app.ticker.remove(tick);
      // 注意：不销毁模块级缓存纹理（themeCache / spriteCache / glow / shadow），
      // React StrictMode 双挂载时新场景直接复用；缓存总量有界（约 40 张小纹理）。
      app.destroy(true, { children: true });
    },
  };
}
