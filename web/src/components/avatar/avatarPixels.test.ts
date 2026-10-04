// W11 前端单测 —— 渲染核心（纯函数层）。
//
// fixture 说明：`__fixtures__/w11.json` 是**后端引擎真实产物**的快照
// （由 scripts 导出，非手写），因此本文件断言的是真实契约而不是自造数据。
//
// 覆盖任务书验收点：
//  - 矩阵 → RGBA 像素（每个非 '.' 字符都有颜色，绝不留空洞）
//  - 两帧待机呼吸 + 两帧行走（帧数、差异性、尺寸恒定）
//  - nearest 整数倍放大（分享卡 sprite scale 必须是整数）
//  - 分享卡零隐私泄露（未勾选字段的**值**绝不出现）
//  - 缺色 / 缺层显式抛错（不静默画假图）

import { describe, it, expect } from 'vitest';
import fixture from './__fixtures__/w11.json';
import {
  AVATAR_HEIGHT,
  AVATAR_WIDTH,
  LAYER_NAMES,
  assertPaletteCoversMatrix,
  buildShareCardPlan,
  compositeLayers,
  hexToRgbInt,
  idleFrames,
  renderAvatar,
  renderMatrix,
  toPixelPalette,
  walkFrames,
  type AvatarLayers,
} from './avatarPixels';

const layers = fixture.layers as unknown as AvatarLayers;
// 用 char_palette（字符 → hex）渲染：这是后端为前端合成的色板形状。
const palette = fixture.char_palette as Record<string, string>;
const matrix = fixture.matrix as string[];

describe('W11 · 色板转换', () => {
  it('#rrggbb 转 RGBA 整数正确', () => {
    expect(hexToRgbInt('#000000')).toBe(0x000000);
    expect(hexToRgbInt('#ffffff')).toBe(0xffffff);
    expect(hexToRgbInt('#4a86d8')).toBe(0x4a86d8);
  });

  it('#rgb 短写展开为等值 6 位', () => {
    expect(hexToRgbInt('#fff')).toBe(0xffffff);
    expect(hexToRgbInt('#f00')).toBe(0xff0000);
  });

  it('非法色值显式抛错，绝不静默跳过（否则画面少色也不报错 = 假成功）', () => {
    expect(() => hexToRgbInt('not-a-color')).toThrow(/非法色值/);
    expect(() => hexToRgbInt('#12345')).toThrow(/非法色值/);
  });

  it('字符色板整体转换为 PixelPalette（20 个语义色 = 20 个字符键）', () => {
    const p = toPixelPalette(palette);
    expect(Object.keys(p)).toHaveLength(20);
    expect(p.s).toBe(hexToRgbInt(palette.s));
    // 矩阵里真的用到的每个字符都必须有颜色
    for (const ch of new Set(matrix.join(''))) {
      if (ch === '.') continue;
      expect(p[ch], `字符 ${ch} 缺色`).toBeDefined();
    }
  });
});

describe('W11 · 8 层矩阵契约', () => {
  it('恰好 8 层且顺序与后端一致', () => {
    expect(LAYER_NAMES).toHaveLength(8);
    expect(Object.keys(layers)).toEqual([...LAYER_NAMES]);
  });

  it('每层都是 48 行 × 24 列', () => {
    for (const name of LAYER_NAMES) {
      const rows = layers[name];
      expect(rows, name).toHaveLength(AVATAR_HEIGHT);
      for (const r of rows) expect(r, name).toHaveLength(AVATAR_WIDTH);
    }
  });

  it('合成结果与后端扁平矩阵逐字节一致（前端不能改画面）', () => {
    expect(compositeLayers(layers)).toEqual(matrix);
  });

  it('缺层显式抛错', () => {
    const broken = { ...layers } as Record<string, string[]>;
    delete broken.hair;
    expect(() => compositeLayers(broken as AvatarLayers)).toThrow(/缺少图层 hair/);
  });

  it('矩阵字符全部有色（缺色抛错）', () => {
    expect(() => assertPaletteCoversMatrix(matrix, palette)).not.toThrow();
    const partial: Record<string, string> = { s: '#ffffff' };
    expect(() => assertPaletteCoversMatrix(matrix, partial)).toThrow(/缺少色值定义/);
  });
});

describe('W11 · 渲染成像素', () => {
  it('矩阵 → RGBA 快照尺寸 = 24×48（未放大）', () => {
    const snap = renderAvatar(layers, palette);
    expect(snap.width).toBe(AVATAR_WIDTH);
    expect(snap.height).toBe(AVATAR_HEIGHT);
    expect(snap.data).toHaveLength(AVATAR_WIDTH * AVATAR_HEIGHT * 4);
  });

  it('扁平矩阵与分层渲染得到同一快照（两条渲染路径等价）', () => {
    const a = renderAvatar(layers, palette);
    const b = renderMatrix(matrix, palette);
    expect(Array.from(a.data)).toEqual(Array.from(b.data));
  });

  it('至少一个不透明像素（真的画出了东西，不是全空）', () => {
    const snap = renderAvatar(layers, palette);
    let opaque = 0;
    for (let i = 3; i < snap.data.length; i += 4) if (snap.data[i] > 0) opaque += 1;
    expect(opaque).toBeGreaterThan(200);
  });
});

describe('W11 · 四帧待机生命感', () => {
  it('产出恰好 4 帧，尺寸恒定 24×48', () => {
    const frames = idleFrames(layers);
    expect(frames).toHaveLength(4);
    for (const f of frames) {
      expect(f.matrix).toHaveLength(AVATAR_HEIGHT);
      for (const r of f.matrix) expect(r).toHaveLength(AVATAR_WIDTH);
    }
  });

  it('四帧各不相同（呼吸/发摆/眨眼/中性，真的在动）', () => {
    const frames = idleFrames(layers);
    for (let i = 1; i < frames.length; i++) {
      expect(frames[i].matrix, `帧 ${i} 与中性帧不同`).not.toEqual(frames[0].matrix);
    }
  });

  it('呼吸帧影子与中性帧不同（G5-1 影子联动：身体上抬时接触面收窄）', () => {
    const frames = idleFrames(layers);
    // frames[0]=neutral，frames[1]=breathe
    expect(frames[1].layers.shadow).not.toEqual(frames[0].layers.shadow);
  });

  it('结果确定：同样输入两次派生逐字节相同', () => {
    expect(idleFrames(layers)).toEqual(idleFrames(layers));
  });
});

describe('W11 · 两帧行走', () => {
  it('产出恰好 2 帧', () => {
    expect(walkFrames(layers)).toHaveLength(2);
  });

  it('两帧不同', () => {
    const [a, b] = walkFrames(layers);
    expect(a.matrix).not.toEqual(b.matrix);
  });

  it('行走时影子在两帧间横移（一步一颠的层次感）', () => {
    const [a, b] = walkFrames(layers);
    expect(a.layers.shadow).not.toEqual(b.layers.shadow);
  });

  it('手持有物不随呼吸/行走上下跳（挂在手上，不该飘）', () => {
    const [a, b] = walkFrames(layers);
    expect(a.layers.hand_item).toEqual(layers.hand_item);
    expect(b.layers.hand_item).toEqual(layers.hand_item);
  });
});

describe('W11 · 720×960 分享卡', () => {
  const sc = fixture.share_card as unknown as {
    width: number;
    height: number;
    badges: { field: string; label: string; value: string }[];
    caption: string;
    fingerprint_short: string;
    base_fingerprint_short: string;
    tuned: boolean;
    brand: { product: string; tagline: string };
    privacy_note: string;
    excluded_fields: string[];
  };
  const scAvatar = fixture.share_card_avatar as unknown as {
    matrix: string[];
    char_palette: Record<string, string>;
  };

  const planFor = (badges: { field: string; label: string; value: string }[]) =>
    buildShareCardPlan({
      width: sc.width,
      height: sc.height,
      matrix: scAvatar.matrix,
      palette: scAvatar.char_palette,
      badges,
      caption: sc.caption,
      fingerprintShort: sc.fingerprint_short,
      baseFingerprintShort: sc.base_fingerprint_short,
      tuned: sc.tuned,
      brand: sc.brand,
      privacyNote: sc.privacy_note,
      excludedCount: badges.length === sc.badges.length ? sc.excluded_fields.length : 0,
    });

  it('画布固定 720×960', () => {
    const plan = planFor(sc.badges);
    expect(plan.width).toBe(720);
    expect(plan.height).toBe(960);
  });

  it('sprite 放大倍数是整数（nearest，绝不插值）', () => {
    const plan = planFor(sc.badges);
    expect(Number.isInteger(plan.sprite.scale)).toBe(true);
    expect(plan.sprite.scale).toBeGreaterThanOrEqual(4);
  });

  it('默认零隐私泄露：一个徽章都不勾时，卡上不出现任何画像值', () => {
    const plan = planFor([]);
    const allText = plan.texts.map((t) => t.text).join('|');
    // 全部 7 个白名单字段的真实值，一个都不许出现
    for (const secret of ['ESTJ', '金', '庚', 'aries', 'libra', 'sagittarius', '李雷']) {
      expect(allText, `零勾选时不该出现 ${secret}`).not.toContain(secret);
    }
    // 但要明确写「未勾选任何画像项」，不留空白让人误以为漏了
    expect(allText).toContain('未勾选任何画像项');
  });

  it('勾选的徽章值会正确渲染上卡', () => {
    const plan = planFor(sc.badges);
    const allText = plan.texts.map((t) => t.text).join('|');
    expect(allText).toContain('ESTJ');
    expect(allText).toContain('金');
  });

  it('未勾选字段的名字也不许出现在卡上（列出字段名等于反向泄露画像构成）', () => {
    const plan = planFor(sc.badges);
    const allText = plan.texts.map((t) => t.text).join('|');
    for (const excluded of sc.excluded_fields) {
      expect(allText, `不该打印未勾选字段名 ${excluded}`).not.toContain(excluded);
    }
  });

  it('短码上卡；微调版额外标注底稿短码', () => {
    expect(planFor(sc.badges).texts.map((t) => t.text).join('|')).toContain(sc.fingerprint_short);
    const tunedPlan = buildShareCardPlan({
      width: sc.width,
      height: sc.height,
      matrix: scAvatar.matrix,
      palette: scAvatar.char_palette,
      badges: sc.badges,
      caption: sc.caption,
      fingerprintShort: 'AAAA1111',
      baseFingerprintShort: 'BBBB2222',
      tuned: true,
      brand: sc.brand,
      privacyNote: sc.privacy_note,
      excludedCount: 0,
    });
    const text = tunedPlan.texts.map((t) => t.text).join('|');
    expect(text).toContain('AAAA1111');
    expect(text).toContain('BBBB2222');
    expect(text).toContain('微调');
  });

  it('产品标识上卡（营销与溯源都需要）', () => {
    const text = planFor(sc.badges).texts.map((t) => t.text).join('|');
    expect(text).toContain(sc.brand.product);
    expect(text).toContain(sc.brand.tagline);
  });

  it('像素全部落在画布内（不裁切、不越界）', () => {
    const plan = planFor(sc.badges);
    for (const r of plan.rects) {
      expect(r.x).toBeGreaterThanOrEqual(0);
      expect(r.y).toBeGreaterThanOrEqual(0);
      expect(r.x + r.w).toBeLessThanOrEqual(plan.width);
      expect(r.y + r.h).toBeLessThanOrEqual(plan.height);
    }
  });

  it('所有文本的基线落在画布内', () => {
    const plan = planFor(sc.badges);
    for (const t of plan.texts) {
      expect(t.y).toBeGreaterThan(0);
      expect(t.y).toBeLessThan(plan.height);
    }
  });
});