/**
 * W10-A · 桌面宠物浮窗 —— 纯函数渲染/心情/台词层。
 *
 * 复用 cabin 的精灵资产（只 import，不改渲染层）：
 *   - `PET_WALK_FRAMES`：宠物两帧走路矩阵（20×14）
 *   - `petPalette(base)`：由基色派生明暗/眼/鼻调色板
 *
 * 本层刻意不引入 PixiJS——浮窗是个几十像素的透明小窗，一个 2D canvas 就够，
 * 这样 jsdom 可测、透明窗性能开销为零。矩阵 -> 像素单元格的逻辑抽成纯函数
 * :func:`frameToCells`，React 组件只负责把单元格画到 canvas 上。
 *
 * 诚实原则：
 *   - 心情不编造独立字段，由 W2 存档的 ``intimacy``(0..100) **派生**；
 *   - 台词来自本地「预生成台词池」，点击气泡里标注来源；butler 真模型通道
 *     未配置时绝不伪造一句"AI 生成"的话。
 */
import { PET_WALK_FRAMES, petPalette } from '../components/cabin/cabinPixelArt';
import type { PixelPalette } from '../components/cabin/cabinPixels';

/** 浮窗宠物固定基色（奶油橘猫色）。换色留给后续 W11 角色系统。 */
export const PET_BASE_COLOR = 0xf4a261;

export interface PixelCell {
  readonly x: number;
  readonly y: number;
  /** 0xRRGGBB，组件层再转成 css 颜色。 */
  readonly color: number;
}

/** 字符矩阵 -> 不透明像素单元格（'.' 与空格视为透明）。纯函数，可单测。 */
export function frameToCells(
  rows: readonly string[],
  palette: PixelPalette,
): PixelCell[] {
  const cells: PixelCell[] = [];
  for (let y = 0; y < rows.length; y += 1) {
    const row = rows[y];
    for (let x = 0; x < row.length; x += 1) {
      const ch = row[x];
      if (ch === '.' || ch === ' ') continue;
      const color = palette[ch];
      if (color === undefined) continue;
      cells.push({ x, y, color });
    }
  }
  return cells;
}

/** 两帧走路矩阵（直接复用 cabin 资产）。 */
export const PET_FRAMES: readonly (readonly string[])[] = PET_WALK_FRAMES;

/** 浮窗宠物调色板。 */
export const PET_COLORS: PixelPalette = petPalette(PET_BASE_COLOR);

/** 矩阵包围盒（组件据此定 canvas 逻辑尺寸）。 */
export function frameSize(rows: readonly string[]): { width: number; height: number } {
  const width = rows.reduce((m, r) => Math.max(m, r.length), 0);
  return { width, height: rows.length };
}

/* ------------------------------------------------------------------ */
/* 心情派生（由 intimacy，不编造）                                       */
/* ------------------------------------------------------------------ */

export type MoodLevel = 'low' | 'ok' | 'happy';

export interface Mood {
  readonly level: MoodLevel;
  /** 是否需要照料（低心情时气泡提示去 feed/clean）。 */
  readonly needsCare: boolean;
  readonly label: string;
}

/**
 * 由亲密度派生心情。阈值与 W2 的 MIN/MAX_INTIMACY(0..100) 对齐：
 *   <20 低落需照料；20..59 平静；>=60 开心。
 */
export function moodFromIntimacy(intimacy: number | null | undefined): Mood {
  if (intimacy === null || intimacy === undefined || Number.isNaN(intimacy)) {
    // 存档读不到时如实给「未知」，不假装开心。
    return { level: 'ok', needsCare: false, label: '心情未知' };
  }
  if (intimacy < 20) return { level: 'low', needsCare: true, label: '有点饿了…' };
  if (intimacy < 60) return { level: 'ok', needsCare: false, label: '在打发时间' };
  return { level: 'happy', needsCare: false, label: '心情很好' };
}

/* ------------------------------------------------------------------ */
/* 本地预生成台词池（butler 真模型未配置时的诚实回落）                    */
/* ------------------------------------------------------------------ */

export const PET_LINES: Record<MoodLevel, readonly string[]> = {
  low: [
    '肚子空空…能去小屋喂喂我吗？',
    '好久没打扫啦，毛都打结了。',
    '你今天还没来看我呢。',
  ],
  ok: [
    '在窗台上陪你干活哦。',
    '敲键盘的声音，我喜欢。',
    '要不要去小屋转一圈？',
  ],
  happy: [
    '今天也是元气满满的一天！',
    '被你摸摸头，超开心。',
    '小屋的花开了，记得去看。',
  ],
};

/** 纯函数级伪随机（mulberry32 风格），让「点哪句」可复现、可测。 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 按心情挑一句台词；seed 相同则结果相同。来源标注为「预生成台词池」。 */
export function pickPetLine(level: MoodLevel, seed: number): string {
  const pool = PET_LINES[level] ?? PET_LINES.ok;
  const idx = Math.floor(mulberry32(seed)() * pool.length);
  return pool[Math.min(idx, pool.length - 1)];
}

/** 0xRRGGBB -> '#rrggbb'（canvas/fillRect 用）。 */
export function colorToCss(color: number): string {
  const r = (color >> 16) & 0xff;
  const g = (color >> 8) & 0xff;
  const b = color & 0xff;
  const h = (n: number) => n.toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}
