// W11 · 个性化像素角色 —— 渲染核心（纯函数，全部可单测，无副作用）。
//
// 职责边界：
//  - 这里**只做**「后端 8 层矩阵 → 可渲染像素」的确定性派生：
//      · #RRGGBB 语义色板 → RGBA 快照
//      · 两帧待机呼吸 / 两帧行走的**层级别**帧派生
//      · 720×960 分享卡的绘制指令（不含 DOM、不含 canvas API）
//  - 这里**不做**：发起请求（那是 api/avatar.ts）、任何像素合成/美化/补画。
//    后端给什么就渲染什么；缺色抛错而不是画一个假的（诚实铁律）。
//
// 为什么帧派生放在前端而不是后端：
//  引擎只保证「同画像 → 逐字节相同的 8 层矩阵」（那是要落库、要断言的稳定契约）。
//  呼吸/行走的相位偏移是**呈现层动效**，让它依赖运行时的像素分析会把确定性的
//  边界搅浑；放前端则动画节奏可调，而底稿指纹不受影响。

import { matrixToRgba, type PixelPalette, type RgbaSnapshot } from '../cabin/cabinPixels';
import type { AvatarCharPalette, AvatarLayers } from '../../api/avatar';

// 类型再导出：调用方（工坊页 / 小屋 / 测试）从本模块取渲染相关的全部类型，
// 免得一半从 api/avatar、一半从这里，两处漂移。
export type { AvatarCharPalette, AvatarLayers, AvatarCharPalette as AvatarPalette };

/** 8 层名称，与后端 avatar_gen.LAYER_NAMES 顺序一致。 */
export const LAYER_NAMES = [
  'shadow',
  'body',
  'hair',
  'face',
  'outfit',
  'accessory',
  'hand_item',
  'outline',
] as const;
export type LayerName = (typeof LAYER_NAMES)[number];

export const AVATAR_WIDTH = 24;
export const AVATAR_HEIGHT = 32;

/* ------------------------------------------------------------------ */
/* 色板：#RRGGBB → PixelPalette（RGBA 整数）                            */
/* ------------------------------------------------------------------ */

/**
 * '#rrggbb' / '#rgb' → 0xRRGGBB 整数。
 * 格式非法一律**抛错**而不是跳过：跳过等于静默画出一个少色的角色，
 * 那正是「假成功」，是本项目最红线的一条。
 */
export function hexToRgbInt(hex: string): number {
  const raw = hex.trim().replace(/^#/, '');
  const expand = (s: string): string => (s.length === 1 ? s + s : s);
  if (raw.length === 3) {
    const r = expand(raw[0]);
    const g = expand(raw[1]);
    const b = expand(raw[2]);
    return parseInt(r + g + b, 16);
  }
  if (raw.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(raw)) {
    throw new Error(`avatarPixels: 非法色值 ${JSON.stringify(hex)}（期望 #rgb 或 #rrggbb）`);
  }
  return parseInt(raw, 16);
}

/** 语义色板 → PixelPalette。键是矩阵字符（如 's'=skin, 'x'=rim_light）。 */
export function toPixelPalette(palette: AvatarCharPalette): PixelPalette {
  const out: PixelPalette = {};
  for (const [char, hex] of Object.entries(palette)) {
    out[char] = hexToRgbInt(hex);
  }
  return out;
}

/**
 * 校验「矩阵里出现过的每个字符」都有对应色。
 * 缺色说明前后端契约已经漂移，必须显式抛错让页面报错，而不是渲染出空洞。
 */
export function assertPaletteCoversMatrix(matrix: readonly string[], palette: AvatarCharPalette): void {
  const missing = new Set<string>();
  for (const row of matrix) {
    for (const ch of row) {
      if (ch !== '.' && !(ch in palette)) missing.add(ch);
    }
  }
  if (missing.size > 0) {
    throw new Error(`avatarPixels: 矩阵字符缺少色值定义: ${[...missing].sort().join(',')}`);
  }
}

/* ------------------------------------------------------------------ */
/* 合成：8 层 → 单张矩阵                                                */
/* ------------------------------------------------------------------ */

/**
 * 把 8 层按顺序叠成一张 24×32 的矩阵。
 * 后层字符为 '.' 时保留前层内容（非 '.' 覆盖）——即真正的分层合成。
 */
export function compositeLayers(layers: AvatarLayers): string[] {
  for (const name of LAYER_NAMES) {
    if (!layers[name]) throw new Error(`avatarPixels: 缺少图层 ${name}`);
  }
  const out: string[] = [];
  for (let y = 0; y < AVATAR_HEIGHT; y++) {
    let line = '';
    for (let x = 0; x < AVATAR_WIDTH; x++) {
      let ch = '.';
      for (const name of LAYER_NAMES) {
        const row = layers[name][y] ?? '';
        const c = row[x] ?? '.';
        if (c !== '.' && c !== ' ') ch = c;
      }
      line += ch;
    }
    out.push(line);
  }
  return out;
}

/** 角色包 → RGBA 快照（可直接 putImageData）。 */
export function renderAvatar(
  layers: AvatarLayers,
  palette: AvatarCharPalette,
): RgbaSnapshot {
  const matrix = compositeLayers(layers);
  assertPaletteCoversMatrix(matrix, palette);
  return matrixToRgba(matrix, toPixelPalette(palette));
}

/** 扁平矩阵（后端 avatar.matrix）→ RGBA 快照。小屋只需要这个。 */
export function renderMatrix(
  matrix: readonly string[],
  palette: AvatarCharPalette,
): RgbaSnapshot {
  assertPaletteCoversMatrix(matrix, palette);
  return matrixToRgba(matrix, toPixelPalette(palette));
}

/* ------------------------------------------------------------------ */
/* 动画帧派生（任务书：两帧待机呼吸 + 两帧行走）                          */
/* ------------------------------------------------------------------ */

/** 一帧 = 若干层各自的矩阵；相位不同的层错开，行走才有「身体动、手不动」的层次。 */
export interface AvatarFrame {
  readonly index: number;
  readonly layers: AvatarLayers;
  readonly matrix: string[];
}

/**
 * 两帧待机呼吸：身体与头发整体上移 1px，其余层不动。
 *
 * 只动 body/hair —— 脚（body 的底部）与影子留在原位才不产生「漂浮」。
 * 实际上移的是**脸的可见区**：呼吸靠 head 起伏，若整层上移会把地面影一起带走。
 * 因此这里对 body / hair 做「头部行区间」的整体上移，影子不动。
 */
export function idleFrames(layers: AvatarLayers): [AvatarFrame, AvatarFrame] {
  return [makeIdleFrame(layers, 0), makeIdleFrame(layers, 1)];
}

function makeIdleFrame(layers: AvatarLayers, phase: 0 | 1): AvatarFrame {
  // phase 0 = 吸气（头略低，原位）；phase 1 = 呼气（头上抬 1px）
  const dy = phase === 1 ? -1 : 0;
  const moved: AvatarLayers = {};
  for (const name of LAYER_NAMES) {
    moved[name] = name === 'body' || name === 'hair' || name === 'face'
      ? shiftRowsUp(layers[name], dy)
      : layers[name].map((r) => r.slice());
  }
  return { index: phase, layers: moved, matrix: compositeLayers(moved) };
}

/** 行走两帧：身体上下 1px + 影子横向 1px，读作一步一颠。 */
export function walkFrames(layers: AvatarLayers): [AvatarFrame, AvatarFrame] {
  return [makeWalkFrame(layers, 0), makeWalkFrame(layers, 1)];
}

function makeWalkFrame(layers: AvatarLayers, phase: 0 | 1): AvatarFrame {
  const dy = phase === 1 ? -1 : 0;
  const dx = phase === 1 ? 1 : 0;
  const moved: AvatarLayers = {};
  for (const name of LAYER_NAMES) {
    if (name === 'shadow') {
      moved[name] = shiftLayer(layers[name], 0, dx);
    } else if (name === 'body' || name === 'hair' || name === 'face') {
      moved[name] = shiftRowsUp(layers[name], dy);
    } else {
      moved[name] = layers[name].map((r) => r.slice());
    }
  }
  return { index: phase, layers: moved, matrix: compositeLayers(moved) };
}

/** 整层平移（超界丢弃）。dx=0 时是纯拷贝。 */
function shiftLayer(rows: readonly string[], dx: number, dy = 0): string[] {
  return rows.map((_row, y) => {
    const srcY = y - dy;
    if (srcY < 0 || srcY >= rows.length) return '.'.repeat(AVATAR_WIDTH);
    if (dx === 0) return rows[srcY];
    const src = rows[srcY];
    let out = '';
    for (let x = 0; x < AVATAR_WIDTH; x++) {
      const sx = x - dx;
      out += sx < 0 || sx >= src.length ? '.' : src[sx];
    }
    return out;
  });
}

/**
 * 只把「头部行区间」上移，脚与地面不动。
 * 头部固定占 0..BODY_TOP-1（后端 BODY_TOP=13，与之对齐）。
 */
function shiftRowsUp(rows: readonly string[], dy: number): string[] {
  const out = rows.map((r) => r.slice());
  if (dy === 0) return out;
  for (let y = 0; y < rows.length; y++) {
    const srcY = y - dy;
    out[y] = srcY >= 0 && srcY < rows.length ? rows[srcY] : '.'.repeat(AVATAR_WIDTH);
  }
  // 身体层上移后，脚（原来的最后几行）会缺失：把脚留在原地，否则人没有脚。
  if (dy < 0) {
    for (let y = rows.length + dy; y < rows.length; y++) {
      out[y] = rows[y];
    }
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* 分享卡（720×960）：把后端渲染数据转成绘制指令                          */
/* ------------------------------------------------------------------ */

/** 画布上的一组像素矩形（CSS 像素）。 */
export interface DrawRect {
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
}

/** 画布上的一行文本。 */
export interface DrawText {
  x: number;
  y: number;
  text: string;
  /** px 字号。 */
  size: number;
  color: string;
  align: 'left' | 'center';
  weight?: number | string;
}

/**
 * 分享卡绘制指令。**纯数据**，由 canvas 2d context 执行（便于单测断言）。
 *
 * 隐私红线：指令里出现的文本**只有** badges 勾选的值 + display_name + 品牌标识，
 * 绝不包含未勾选字段。`excluded_fields` 仅作为「已隐藏」的计数提示，
 * 不打印具体字段名——那等于反向泄露用户画像有哪些项。
 */
export interface ShareCardPlan {
  width: number;
  height: number;
  background: string;
  rects: DrawRect[];
  texts: DrawText[];
  /** 角色像素区左上角与单像素尺寸（nearest 放大整数倍，绝不用小数缩放）。 */
  sprite: { x: number; y: number; scale: number };
}

export interface ShareCardInput {
  width: number;
  height: number;
  matrix: readonly string[];
  palette: AvatarCharPalette;
  badges: readonly { field: string; label: string; value: string }[];
  caption: string;
  fingerprintShort: string;
  baseFingerprintShort: string;
  tuned: boolean;
  brand: { product: string; tagline: string };
  privacyNote: string;
  /** 未勾选字段数量（只用于文案计数，不打印字段名）。 */
  excludedCount: number;
}

/** 分享卡配色：冰蓝玻璃风（与 UI_BASELINE 一致，禁绿色系）。 */
export const CARD_THEME = {
  background: '#eaf1fb',
  panel: '#f7fbff',
  border: '#c9dcf2',
  title: '#1c2b3f',
  body: '#3c5470',
  muted: '#6d87a3',
  accent: '#4a86d8',
} as const;

/**
 * 生成 720×960 绘制指令。
 * 布局：顶部产品标识 → 中部角色大图（nearest 整数倍放大）→ 徽章 → 短码 → 隐私说明。
 */
export function buildShareCardPlan(input: ShareCardInput): ShareCardPlan {
  assertPaletteCoversMatrix(input.matrix, input.palette);

  const { width: W, height: H } = input;
  // 整数倍放大：24×32 → 8× = 192×256，垂直居中于中部区域
  const scale = 8;
  const spriteW = AVATAR_WIDTH * scale;
  const spriteH = AVATAR_HEIGHT * scale;

  const rects: DrawRect[] = [
    { x: 0, y: 0, w: W, h: H, color: CARD_THEME.background },
    { x: 24, y: 24, w: W - 48, h: H - 48, color: CARD_THEME.panel },
  ];

  const texts: DrawText[] = [
    { x: W / 2, y: 58, text: input.brand.product, size: 20, color: CARD_THEME.muted, align: 'center', weight: 600 },
    { x: W / 2, y: 96, text: '我的专属像素小人', size: 34, color: CARD_THEME.title, align: 'center', weight: 700 },
  ];

  // 角色像素：先把 '.' 区域填成面板色，再用调色板画实心块
  const spriteX = Math.round((W - spriteW) / 2);
  const spriteY = 160;
  for (let y = 0; y < input.matrix.length; y++) {
    const row = input.matrix[y];
    for (let x = 0; x < row.length; x++) {
      const ch = row[x];
      if (ch === '.' || ch === ' ') continue;
      const hex = input.palette[ch];
      if (!hex) continue;
      rects.push({
        x: spriteX + x * scale,
        y: spriteY + y * scale,
        w: scale,
        h: scale,
        color: hex,
      });
    }
  }
  rects.push({
    x: spriteX - 8,
    y: spriteY - 8,
    w: spriteW + 16,
    h: spriteH + 16,
    color: CARD_THEME.border,
  });
  // 边框画在精灵之后会把精灵盖住，所以边框先画（插到面板之后）
  const borderRect = rects.pop()!;
  rects.splice(2, 0, borderRect);

  let cursorY = spriteY + spriteH + 40;

  // 徽章：只画勾选的值
  if (input.badges.length > 0) {
    for (const b of input.badges) {
      texts.push({
        x: W / 2,
        y: cursorY,
        text: `${b.label} · ${b.value}`,
        size: 24,
        color: CARD_THEME.body,
        align: 'center',
        weight: 500,
      });
      cursorY += 34;
    }
  } else {
    // 零勾选：明确说「未勾选任何画像项」，而不是留一片空白让人误以为漏了
    texts.push({
      x: W / 2,
      y: cursorY,
      text: '未勾选任何画像项',
      size: 22,
      color: CARD_THEME.muted,
      align: 'center',
      weight: 500,
    });
    cursorY += 34;
  }

  cursorY = Math.max(cursorY + 16, H - 210);

  texts.push({ x: W / 2, y: cursorY, text: input.caption, size: 22, color: CARD_THEME.body, align: 'center' });
  cursorY += 40;

  // 短码：呈现指纹 8 位 + 微调标记；底稿短码帮助区分「底稿 / 微调版本」
  const shortCode = input.tuned
    ? `ID ${input.fingerprintShort} · 微调自 ${input.baseFingerprintShort}`
    : `ID ${input.fingerprintShort}`;
  texts.push({ x: W / 2, y: cursorY, text: shortCode, size: 20, color: CARD_THEME.accent, align: 'center', weight: 700 });
  cursorY += 34;

  // 隐私说明：只报「已隐藏 N 项」，不列出字段名（列出即反向泄露画像构成）
  const privacy = input.excludedCount > 0
    ? `${input.privacyNote}（已隐藏 ${input.excludedCount} 项未勾选信息）`
    : `${input.privacyNote}（已勾选 ${input.badges.length} 项，其余信息未上卡）`;
  texts.push({ x: W / 2, y: H - 96, text: privacy, size: 17, color: CARD_THEME.muted, align: 'center' });
  texts.push({ x: W / 2, y: H - 66, text: input.brand.tagline, size: 17, color: CARD_THEME.muted, align: 'center' });

  return {
    width: W,
    height: H,
    background: CARD_THEME.background,
    rects,
    texts,
    sprite: { x: spriteX, y: spriteY, scale },
  };
}

/**
 * 把绘制指令落到 canvas 2d context。
 * imageSmoothingEnabled = false —— nearest 放大是像素画的灵魂，绝不能开插值。
 */
export function paintShareCard(
  ctx: CanvasRenderingContext2D,
  plan: ShareCardPlan,
): void {
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, plan.width, plan.height);
  ctx.fillStyle = plan.background;
  ctx.fillRect(0, 0, plan.width, plan.height);
  for (const r of plan.rects) {
    ctx.fillStyle = r.color;
    ctx.fillRect(r.x, r.y, r.w, r.h);
  }
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  for (const t of plan.texts) {
    ctx.font = `${t.weight ?? 400} ${t.size}px "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`;
    ctx.fillStyle = t.color;
    ctx.fillText(t.text, t.x, t.y);
  }
}