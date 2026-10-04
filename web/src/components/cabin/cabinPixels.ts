import { Texture } from 'pixi.js';

/**
 * 像素精灵生成器（零外部素材）：字符矩阵 / 程序化像素画布 → PIXI.Texture。
 *
 * 像素风要点：
 *  - 每个字符一格像素，'.' 或 ' ' 为透明；调色板 char -> 0xRRGGBB。
 *  - 纹理 source.scaleMode = 'nearest'（PixiJS v8 写法），放大不糊。
 *  - 场景侧配合 roundPixels + 整数坐标取整，避免半像素模糊。
 *  - 大面积背景层（天空/视差带/地面）用 PixelBuffer 程序化逐像素绘制：
 *    多段渐变 + Bayer 4×4 有序抖动，是「唯美像素风」的细腻色阶来源。
 *
 * jsdom 下无 Canvas 2D，bufferToTexture 会抛错；单测通过注入 TextureDeps
 * 捕获 putImageData 数据来验证（见 cabinPixels.test.ts）。
 */

export type PixelPalette = Record<string, number>;

export interface RgbaSnapshot {
  readonly width: number;
  readonly height: number;
  /** RGBA（非预乘），每像素 4 字节。固定 ArrayBuffer 以兼容 ImageData 构造。 */
  readonly data: Uint8ClampedArray<ArrayBuffer>;
}

export interface MatrixSpriteOptions {
  /** 自动 1px 描边色：矩阵外扩 1px 的空隙里，凡与不透明像素 4-邻接处填该色。 */
  autoOutline?: number;
  label?: string;
  /**
   * G1-8b：矩阵出现调色板里没有的字符时是否直接抛错（默认 false = 只告警）。
   * 像素矩阵是静态资产，未知字符=打错字/漏色键；静默透明白会制造
   * 「改完画面全没、但测试全绿」的假成功，因此至少 console.warn。
   */
  strict?: boolean;
}

/** Bayer 4×4 有序抖动表（0..15），像素风细腻渐变的基础。 */
const BAYER4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5] as const;

/** 归一化抖动阈值 (0..1)，参数取任意整数像素坐标。 */
export function bayer4(x: number, y: number): number {
  return (BAYER4[(x & 3) + (y & 3) * 4] + 0.5) / 16;
}

function clamp255(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : Math.round(v);
}

/** 确定性 PRNG（mulberry32）：背景元素摆放可复现，无外部素材依赖。 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ------------------------------------------------------------------ */
/* 字符矩阵 → RGBA（纯函数，可直接单测）                                */
/* ------------------------------------------------------------------ */

export function matrixToRgba(
  rows: readonly string[],
  palette: PixelPalette,
  options: MatrixSpriteOptions = {},
): RgbaSnapshot {
  if (rows.length === 0) throw new Error('cabinPixels: 矩阵不能为空');
  const w = Math.max(1, ...rows.map((r) => r.length));
  const h = rows.length;
  const pad = options.autoOutline === undefined ? 0 : 1;
  const width = w + pad * 2;
  const height = h + pad * 2;
  const data = new Uint8ClampedArray(width * height * 4);

  const write = (x: number, y: number, color: number): void => {
    const i = (y * width + x) * 4;
    data[i] = (color >> 16) & 0xff;
    data[i + 1] = (color >> 8) & 0xff;
    data[i + 2] = color & 0xff;
    data[i + 3] = 255;
  };

  const unknown = new Set<string>();
  for (let my = 0; my < h; my++) {
    const row = rows[my];
    for (let mx = 0; mx < row.length; mx++) {
      const ch = row[mx];
      if (ch === '.' || ch === ' ') continue;
      const color = palette[ch];
      if (color === undefined) {
        // G1-8b：不再静默 —— 记录未知色键，循环后统一告警/抛错
        unknown.add(ch);
        continue;
      }
      write(mx + pad, my + pad, color);
    }
  }
  if (unknown.size > 0) {
    const msg =
      `cabinPixels.matrixToRgba: 矩阵用了调色板没有的色键 ` +
      `[${[...unknown].map((c) => JSON.stringify(c)).join(', ')}]` +
      (options.label ? `（精灵 ${options.label}）` : '') +
      '，这些像素会被画成透明（画面缺块）。';
    if (options.strict) throw new Error(msg);
    console.warn(msg);
  }

  if (pad === 1 && options.autoOutline !== undefined) {
    const outline = options.autoOutline;
    // 快照填充结果，避免描边互相串联；8-邻接保证凸角处描边连续无缺口
    const filled: boolean[] = [];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) filled[y * width + x] = data[(y * width + x) * 4 + 3] > 0;
    }
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (filled[y * width + x]) continue;
        let near = false;
        for (let dy = -1; dy <= 1 && !near; dy++) {
          for (let dx = -1; dx <= 1 && !near; dx++) {
            if (dx === 0 && dy === 0) continue;
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
            near = filled[ny * width + nx];
          }
        }
        if (near) write(x, y, outline);
      }
    }
  }

  return { width, height, data };
}

/* ------------------------------------------------------------------ */
/* 程序化像素画布（背景层用）                                          */
/* ------------------------------------------------------------------ */

export interface GradientStop {
  t: number;
  color: number;
}

function mixColor(a: number, b: number, f: number): number {
  const r = Math.round(((a >> 16) & 0xff) + (((b >> 16) & 0xff) - ((a >> 16) & 0xff)) * f);
  const g = Math.round(((a >> 8) & 0xff) + (((b >> 8) & 0xff) - ((a >> 8) & 0xff)) * f);
  const bl = Math.round((a & 0xff) + ((b & 0xff) - (a & 0xff)) * f);
  return (r << 16) | (g << 8) | bl;
}

export class PixelBuffer implements RgbaSnapshot {
  readonly width: number;
  readonly height: number;
  readonly data: Uint8ClampedArray<ArrayBuffer>;

  constructor(width: number, height: number) {
    if (width < 1 || height < 1) throw new Error('cabinPixels: PixelBuffer 尺寸必须 >= 1');
    this.width = width;
    this.height = height;
    this.data = new Uint8ClampedArray(width * height * 4);
  }

  /** 单像素写入（src-over 合成，alpha < 1 时与底色混合）。 */
  setPx(x: number, y: number, color: number, alpha = 1): void {
    if (x < 0 || y < 0 || x >= this.width || y >= this.height || alpha <= 0) return;
    const i = (y * this.width + x) * 4;
    const d = this.data;
    const sa = alpha >= 1 ? 1 : alpha;
    if (sa >= 1 || d[i + 3] === 0) {
      d[i] = (color >> 16) & 0xff;
      d[i + 1] = (color >> 8) & 0xff;
      d[i + 2] = color & 0xff;
      d[i + 3] = Math.round(sa * 255);
      return;
    }
    const da = d[i + 3] / 255;
    const oa = sa + da * (1 - sa);
    d[i] = clamp255((((color >> 16) & 0xff) * sa + d[i] * da * (1 - sa)) / oa);
    d[i + 1] = clamp255((((color >> 8) & 0xff) * sa + d[i + 1] * da * (1 - sa)) / oa);
    d[i + 2] = clamp255(((color & 0xff) * sa + d[i + 2] * da * (1 - sa)) / oa);
    d[i + 3] = clamp255(oa * 255);
  }

  rect(x: number, y: number, w: number, h: number, color: number, alpha = 1): void {
    for (let iy = 0; iy < h; iy++) {
      for (let ix = 0; ix < w; ix++) this.setPx(x + ix, y + iy, color, alpha);
    }
  }

  /**
   * 垂直多段渐变 + Bayer 有序抖动量化：唯美像素风的「细腻色阶」来源。
   * levels 控制亮度分层细腻度（16 档 ≈ 现代像素风的平滑过渡）。
   */
  vGradient(x: number, y: number, w: number, h: number, stops: readonly GradientStop[], levels = 16): void {
    if (stops.length < 2) throw new Error('cabinPixels: 渐变至少需要 2 个色标');
    const sorted = [...stops].sort((a, b) => a.t - b.t);
    const step = 255 / (levels - 1);
    for (let iy = 0; iy < h; iy++) {
      const t = h <= 1 ? 0 : iy / (h - 1);
      let i = 0;
      while (i < sorted.length - 2 && t > sorted[i + 1].t) i++;
      const s0 = sorted[i];
      const s1 = sorted[i + 1];
      const f = s1.t <= s0.t ? 0 : Math.min(1, Math.max(0, (t - s0.t) / (s1.t - s0.t)));
      const c = mixColor(s0.color, s1.color, f);
      for (let ix = 0; ix < w; ix++) {
        const jitter = (bayer4(x + ix, y + iy) - 0.5) * step;
        this.setPx(
          x + ix,
          y + iy,
          (clamp255(((c >> 16) & 0xff) + jitter) << 16) |
            (clamp255(((c >> 8) & 0xff) + jitter) << 8) |
            clamp255((c & 0xff) + jitter),
        );
      }
    }
  }

  /** 椭圆径向渐淡（辉光 / 柔和阴影共用）：alpha 随椭圆距离衰减。 */
  radialEllipse(
    cx: number,
    cy: number,
    rx: number,
    ry: number,
    color: number,
    maxAlpha: number,
    falloff = 1.6,
  ): void {
    const x0 = Math.max(0, Math.floor(cx - rx));
    const x1 = Math.min(this.width - 1, Math.ceil(cx + rx));
    const y0 = Math.max(0, Math.floor(cy - ry));
    const y1 = Math.min(this.height - 1, Math.ceil(cy + ry));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const dx = (x + 0.5 - cx) / rx;
        const dy = (y + 0.5 - cy) / ry;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d >= 1) continue;
        this.setPx(x, y, color, maxAlpha * Math.pow(1 - d, falloff));
      }
    }
  }

  /** 盖章：把矩阵快照画进来（scale=2 表示每格 2×2 像素块，保持锐利）。 */
  stamp(snap: RgbaSnapshot, x: number, y: number, scale = 1): void {
    for (let sy = 0; sy < snap.height; sy++) {
      for (let sx = 0; sx < snap.width; sx++) {
        const i = (sy * snap.width + sx) * 4;
        if (snap.data[i + 3] === 0) continue;
        const color = (snap.data[i] << 16) | (snap.data[i + 1] << 8) | snap.data[i + 2];
        for (let by = 0; by < scale; by++) {
          for (let bx = 0; bx < scale; bx++) {
            this.setPx(x + sx * scale + bx, y + sy * scale + by, color);
          }
        }
      }
    }
  }

  /** 水平平铺无缝盖章：在 x 及 x±tileWidth 各盖一次，保证 TilingSprite 无缝。 */
  stampTiled(snap: RgbaSnapshot, tileWidth: number, x: number, y: number, scale = 1): void {
    this.stamp(snap, x, y, scale);
    this.stamp(snap, x - tileWidth, y, scale);
    this.stamp(snap, x + tileWidth, y, scale);
  }
}

/* ------------------------------------------------------------------ */
/* RGBA → PIXI.Texture（canvas 2d 路径，nearest 采样）                  */
/* ------------------------------------------------------------------ */

export interface TextureDeps {
  createCanvas: (width: number, height: number) => {
    canvas: HTMLCanvasElement;
    ctx: CanvasRenderingContext2D | null;
  };
}

export const defaultTextureDeps: TextureDeps = {
  createCanvas: (width, height) => {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    return { canvas, ctx: canvas.getContext('2d') };
  },
};

export interface PixelTexture {
  texture: Texture;
  width: number;
  height: number;
}

export function bufferToTexture(
  buf: RgbaSnapshot,
  options: MatrixSpriteOptions = {},
  deps: TextureDeps = defaultTextureDeps,
): PixelTexture {
  const { canvas, ctx } = deps.createCanvas(buf.width, buf.height);
  if (!ctx) throw new Error('cabinPixels: Canvas 2D 不可用，无法生成像素纹理');
  // createImageData 代替 new ImageData（不依赖全局 ImageData，jsdom 单测可 mock）
  const img = ctx.createImageData(buf.width, buf.height);
  img.data.set(buf.data);
  ctx.putImageData(img, 0, 0);
  const texture = Texture.from(canvas);
  // 关键：nearest 采样，放大不糊（PixiJS v8 写法）
  texture.source.scaleMode = 'nearest';
  texture.label = options.label ?? 'cabin-pixel';
  return { texture, width: buf.width, height: buf.height };
}

/* ------------------------------------------------------------------ */
/* 精灵缓存：同一矩阵+调色板只生成一次纹理                             */
/* （缓存与场景解耦：场景销毁不销毁缓存纹理，StrictMode 双挂载安全）      */
/* ------------------------------------------------------------------ */

const spriteCache = new Map<string, PixelTexture>();

export function spriteFromMatrix(
  rows: readonly string[],
  palette: PixelPalette,
  options: MatrixSpriteOptions = {},
  cacheKey?: string,
  deps: TextureDeps = defaultTextureDeps,
): PixelTexture {
  const key =
    cacheKey ??
    `m:${options.label ?? ''}:${options.autoOutline ?? ''}:${rows.join('\n')}:${JSON.stringify(palette)}`;
  const hit = spriteCache.get(key);
  if (hit) return hit;
  const made = bufferToTexture(matrixToRgba(rows, palette, options), options, deps);
  spriteCache.set(key, made);
  return made;
}

/** 仅供单测：清空并销毁缓存纹理。 */
export function clearSpriteCache(): void {
  for (const item of spriteCache.values()) item.texture.destroy(true);
  spriteCache.clear();
}
