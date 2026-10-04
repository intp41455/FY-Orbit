import { beforeEach, describe, expect, it, vi } from 'vitest';

// jsdom 无 Canvas 2D / WebGL：mock pixi.js 的 Texture.from 捕获生成过程，
// 用假 canvas deps 捕获 putImageData 的 RGBA 数据来验证矩阵→纹理正确性。
const { textureFrom } = vi.hoisted(() => ({
  textureFrom: vi.fn(() => ({ source: { scaleMode: 'linear' }, destroy: vi.fn() })),
}));

vi.mock('pixi.js', () => ({
  Texture: { from: textureFrom },
}));

import {
  PixelBuffer,
  bayer4,
  bufferToTexture,
  clearSpriteCache,
  matrixToRgba,
  spriteFromMatrix,
  type TextureDeps,
} from './cabinPixels';

function fakeDeps() {
  const put = vi.fn();
  const deps: TextureDeps = {
    createCanvas: (width, height) => {
      const canvas = { width, height } as unknown as HTMLCanvasElement;
      const ctx = {
        createImageData: (w: number, h: number) => ({
          data: new Uint8ClampedArray(w * h * 4),
          width: w,
          height: h,
        }),
        putImageData: put,
      } as unknown as CanvasRenderingContext2D;
      return { canvas, ctx };
    },
  };
  return { deps, put };
}

beforeEach(() => {
  textureFrom.mockClear();
  clearSpriteCache();
});

describe('cabinPixels matrixToRgba：矩阵 → RGBA 快照', () => {
  it('尺寸 = 最长行 × 行数；短行按透明补齐', () => {
    const snap = matrixToRgba(['ab', 'a', ''], { a: 0xff0000, b: 0x00ff00 });
    expect(snap.width).toBe(2);
    expect(snap.height).toBe(3);
    // 第二行 (1,1) 越界字符缺失 → 透明
    expect(snap.data[(1 * 2 + 1) * 4 + 3]).toBe(0);
  });

  it('调色板映射正确：字符 → RGB，alpha=255', () => {
    const snap = matrixToRgba(['ab'], { a: 0x102030, b: 0xfefedc });
    expect(Array.from(snap.data.slice(0, 4))).toEqual([0x10, 0x20, 0x30, 255]);
    expect(Array.from(snap.data.slice(4, 8))).toEqual([0xfe, 0xfe, 0xdc, 255]);
  });

  it("'.' 与空格透明；未知字符宽容处理为透明", () => {
    const snap = matrixToRgba(['.a x'], { a: 0x112233 });
    expect(snap.data[3]).toBe(0); // '.'
    expect(snap.data[7]).toBe(255); // 'a'
    expect(snap.data[11]).toBe(0); // ' '
    expect(snap.data[15]).toBe(0); // 未知 'x'
  });

  it('autoOutline：画布宽高各 +2，边框像素为描边色，内部镂空也被描边', () => {
    const snap = matrixToRgba(['aa', 'aa'], { a: 0xffffff }, { autoOutline: 0x000000 });
    expect(snap.width).toBe(4);
    expect(snap.height).toBe(4);
    // 角 (0,0) 为描边
    expect(Array.from(snap.data.slice(0, 4))).toEqual([0, 0, 0, 255]);
    // 内部 (1,1) 仍是本体色
    expect(snap.data[(1 * 4 + 1) * 4]).toBe(0xff);
    // 镂空：环形矩阵中心透明像素与本体 4-邻接 → 描边色
    const ring = matrixToRgba(['aaa', 'a.a', 'aaa'], { a: 0xffffff }, { autoOutline: 0x0f0f0f });
    const center = (2 * 5 + 2) * 4; // 5 = 3+2 描边后宽度
    expect(Array.from(ring.data.slice(center, center + 4))).toEqual([0x0f, 0x0f, 0x0f, 255]);
  });
});

describe('cabinPixels bufferToTexture：RGBA → PIXI.Texture（mock 源）', () => {
  it('putImageData 收到正确尺寸与像素数据；scaleMode = nearest', () => {
    const { deps, put } = fakeDeps();
    const snap = matrixToRgba(['ab', 'ab'], { a: 0x010203, b: 0x040506 });
    const made = bufferToTexture(snap, { label: 'test-sprite' }, deps);
    expect(made.width).toBe(2);
    expect(made.height).toBe(2);
    expect(put).toHaveBeenCalledTimes(1);
    const img = put.mock.calls[0][0] as { data: Uint8ClampedArray; width: number; height: number };
    expect(img.width).toBe(2);
    expect(img.height).toBe(2);
    expect(Array.from(img.data.slice(0, 4))).toEqual([1, 2, 3, 255]);
    expect(Array.from(img.data.slice(12, 16))).toEqual([4, 5, 6, 255]);
    expect(textureFrom).toHaveBeenCalledTimes(1);
    // 关键像素风参数：nearest 采样，放大不糊
    const tex = made.texture as unknown as { source: { scaleMode: string; label: string } };
    expect(tex.source.scaleMode).toBe('nearest');
  });

  it('无 2D 上下文时明确抛错（jsdom 场景由上层初始化失败兜底）', () => {
    const deps: TextureDeps = {
      createCanvas: (w, h) => ({
        canvas: { width: w, height: h } as unknown as HTMLCanvasElement,
        ctx: null,
      }),
    };
    expect(() => bufferToTexture(matrixToRgba(['a'], { a: 1 }), {}, deps)).toThrow();
  });
});

describe('cabinPixels spriteFromMatrix：缓存', () => {
  it('同矩阵+同调色板只生成一次纹理；不同调色板重新生成', () => {
    const { deps } = fakeDeps();
    const rows = ['ab', 'ba'];
    const pal1 = { a: 1, b: 2 };
    const pal2 = { a: 3, b: 4 };
    const t1 = spriteFromMatrix(rows, pal1, { label: 'x' }, undefined, deps);
    const t2 = spriteFromMatrix(rows, pal1, { label: 'x' }, undefined, deps);
    const t3 = spriteFromMatrix(rows, pal2, { label: 'x' }, undefined, deps);
    expect(t2.texture).toBe(t1.texture);
    expect(t3.texture).not.toBe(t1.texture);
    expect(textureFrom).toHaveBeenCalledTimes(2);
  });

  it('宠物换色场景：同矩阵不同 autoOutline 生成不同纹理', () => {
    const { deps } = fakeDeps();
    const rows = ['bb', 'bb'];
    const t1 = spriteFromMatrix(rows, { b: 0xef5350 }, { autoOutline: 0x111111 }, undefined, deps);
    const t2 = spriteFromMatrix(rows, { b: 0xef5350 }, { autoOutline: 0x222222 }, undefined, deps);
    expect(t2.texture).not.toBe(t1.texture);
  });
});

describe('cabinPixels PixelBuffer：程序化图层', () => {
  it('vGradient 首尾色标正确、中间单调（多段渐变 + 抖动量化）', () => {
    const buf = new PixelBuffer(4, 16);
    buf.vGradient(0, 0, 4, 16, [{ t: 0, color: 0xff0000 }, { t: 1, color: 0x0000ff }], 16);
    const topR = buf.data[0];
    const bottomR = buf.data[(15 * 4 + 0) * 4];
    const bottomB = buf.data[(15 * 4 + 0) * 4 + 2];
    expect(topR).toBeGreaterThan(230); // 顶部接近纯红
    expect(bottomR).toBeLessThan(25); // 底部红色衰减
    expect(bottomB).toBeGreaterThan(230); // 底部接近纯蓝
    // 抖动只在量化噪声级别内扰动
    expect(bayer4(0, 0)).toBeCloseTo(0.03125, 5);
  });

  it('stamp：透明像素不覆盖底色，scale=2 时按块复制', () => {
    const buf = new PixelBuffer(6, 2);
    buf.rect(0, 0, 6, 2, 0x111111);
    const snap = matrixToRgba(['.a'], { a: 0xff0000 });
    buf.stamp(snap, 0, 0, 2);
    const px = (x: number, y: number) => buf.data[(y * 6 + x) * 4];
    expect(buf.data[3]).toBe(255); // (0,0) 原底色仍在（透明像素不覆盖）
    expect(px(0, 0)).toBe(0x11);
    // (1,0) 的字符 a 以 2x2 块盖到 (2,0)-(3,1)
    expect(px(2, 0)).toBe(0xff);
    expect(px(3, 1)).toBe(0xff);
  });

  it('stampTiled：越界部分在 ±tileWidth 环绕副本中补齐（无缝平铺）', () => {
    const buf = new PixelBuffer(8, 1);
    const snap = matrixToRgba(['aaa'], { a: 0x00ff00 });
    // 盖在 x=7：本体占 7,8,9 —— 8,9 越界；x-8 副本占 -1,0,1 —— 0,1 落在画布内
    buf.stampTiled(snap, 8, 7, 0, 1);
    const px = (x: number) => buf.data[x * 4 + 1];
    expect(px(7)).toBe(0xff); // 本体
    expect(px(0)).toBe(0xff); // 左环绕补齐
    expect(px(1)).toBe(0xff);
    expect(buf.data[4 * 4 + 3]).toBe(0); // x=4 未被波及（透明）
  });

  it('radialEllipse：中心 alpha 最高，边缘淡出', () => {
    const buf = new PixelBuffer(9, 9);
    buf.radialEllipse(4, 4, 4, 4, 0xabcdee, 1, 1.5);
    const centerA = buf.data[(4 * 9 + 4) * 4 + 3];
    const edgeA = buf.data[(4 * 9 + 8) * 4 + 3];
    // 像素中心偏移 (±0.5) 使中心点 d≈0.177，alpha≈190；边缘像素 d>1 未被绘制
    expect(centerA).toBeGreaterThan(150);
    expect(edgeA ?? 0).toBeLessThan(centerA);
  });
});

describe('cabinPixels vGradient：三通道各自 clamp255（G1-4 回归守卫）', () => {
  it('极端 stops + 抖动下，R/G/B 恒在 [0,255]', () => {
    const buf = new PixelBuffer(8, 8);
    // 顶端纯白、底端纯黑，中间抖动 step 会把边缘像素推向 <0 或 >255；
    // 若任一通道漏写 clamp255 会溢出（G1-4 历史 bug：蓝通道括号错误未 clamp）。
    buf.vGradient(0, 0, 8, 8, [
      { t: 0, color: 0x000000 },
      { t: 1, color: 0xffffff },
    ], 16);
    for (let i = 0; i < buf.data.length; i += 4) {
      expect(buf.data[i]).toBeGreaterThanOrEqual(0);
      expect(buf.data[i]).toBeLessThanOrEqual(255);
      expect(buf.data[i + 1]).toBeGreaterThanOrEqual(0);
      expect(buf.data[i + 1]).toBeLessThanOrEqual(255);
      expect(buf.data[i + 2]).toBeGreaterThanOrEqual(0);
      expect(buf.data[i + 2]).toBeLessThanOrEqual(255);
    }
  });

  it('三通道形态一致（纯灰渐变下 R==G==B，蓝通道不被区别对待）', () => {
    const buf = new PixelBuffer(4, 4);
    buf.vGradient(0, 0, 4, 4, [
      { t: 0, color: 0x808080 },
      { t: 1, color: 0x808080 },
    ], 16);
    for (let y = 0; y < 4; y++) {
      for (let x = 0; x < 4; x++) {
        const i = (y * 4 + x) * 4;
        expect(buf.data[i]).toBe(buf.data[i + 1]);
        expect(buf.data[i + 1]).toBe(buf.data[i + 2]);
      }
    }
  });
});
