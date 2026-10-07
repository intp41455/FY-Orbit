/**
 * P9 前端单测 · geometry 纯函数（A-点哪评哪-06/07/08/09/10）。
 *
 * 几何与坐标归一化是「点哪评哪」的正确性基础——跨分辨率可复原全靠它。
 */
import { describe, it, expect } from 'vitest';
import {
  buildMarker,
  computeDomPath,
  dragToNormalizedRegion,
  isUsableRegion,
  normalizeStroke,
  previewShortCode,
  toNormalizedRegion,
} from './geometry';

const VP = { w: 1000, h: 500 };

describe('geometry · A-点哪评哪-06 归一化区域', () => {
  it('把像素矩形换算成 0~1 归一化矩形', () => {
    const r = toNormalizedRegion({ left: 100, top: 50, width: 200, height: 100 }, VP);
    expect(r).toEqual({ x: 0.1, y: 0.1, w: 0.2, h: 0.2 });
  });

  it('裁剪越界（矩形超出视口不产生 >1 的值）', () => {
    const r = toNormalizedRegion({ left: 900, top: 400, width: 400, height: 200 }, VP);
    expect(r.x).toBeLessThanOrEqual(1);
    expect(r.w).toBeLessThanOrEqual(1 - r.x + 1e-9);
    expect(r.h).toBeLessThanOrEqual(1 - r.y + 1e-9);
  });

  it('拖拽方向不影响结果（右下 / 左上拖出同一矩形）', () => {
    const a = dragToNormalizedRegion({ x: 100, y: 100 }, { x: 300, y: 200 }, VP);
    const b = dragToNormalizedRegion({ x: 300, y: 200 }, { x: 100, y: 100 }, VP);
    expect(a).toEqual(b);
  });

  it('归一化结果与视口大小无关（同一相对位置不同分辨率等值）', () => {
    const small = toNormalizedRegion({ left: 100, top: 50, width: 200, height: 100 }, { w: 1000, h: 500 });
    const large = toNormalizedRegion({ left: 200, top: 100, width: 400, height: 200 }, { w: 2000, h: 1000 });
    expect(small).toEqual(large);
  });

  it('视口为 0 时不炸（退化保护）', () => {
    const r = toNormalizedRegion({ left: 0, top: 0, width: 0, height: 0 }, { w: 0, h: 0 });
    expect(Number.isFinite(r.x)).toBe(true);
  });
});

describe('geometry · 区域可用性判定', () => {
  it('宽高 > 0 才算可用', () => {
    expect(isUsableRegion({ x: 0.1, y: 0.1, w: 0.2, h: 0.2 })).toBe(true);
    expect(isUsableRegion({ x: 0.1, y: 0.1, w: 0, h: 0.2 })).toBe(false);
    expect(isUsableRegion(null)).toBe(false);
  });
});

describe('geometry · A-点哪评哪-07 笔迹归一化', () => {
  it('把像素点集换算成归一化点集', () => {
    const s = normalizeStroke([{ x: 100, y: 50 }, { x: 300, y: 100 }], VP, '#f00', 4);
    expect(s.color).toBe('#f00');
    expect(s.width).toBe(4);
    expect(s.points).toEqual([{ x: 0.1, y: 0.1 }, { x: 0.3, y: 0.2 }]);
  });

  it('越界点被裁剪到 0~1', () => {
    const s = normalizeStroke([{ x: -50, y: 900 }], VP);
    expect(s.points[0].x).toBe(0);
    expect(s.points[0].y).toBe(1);
  });
});

describe('geometry · A-点哪评哪-08 真写标记', () => {
  it('产出二十来字符的短标记', () => {
    const m = buildMarker('button', 'save', 'primary large extra');
    expect(m).toBe('<button#save.primary.large>');
    expect(m.length).toBeLessThanOrEqual(30);
  });

  it('class 最多取两个（控制长度）', () => {
    const m = buildMarker('div', '', 'a b c d e');
    expect(m).toBe('<div.a.b>');
  });
});

describe('geometry · A-点哪评哪-09 短码预览', () => {
  it('序号从 1 起（与服务端分配一致）', () => {
    expect(previewShortCode(1)).toBe('r1');
    expect(previewShortCode(0)).toBe('r1'); // 归一到最小 1
  });
});

describe('geometry · A-点哪评哪-10 DOM 结构路径', () => {
  it('只收结构（标签 + 同标签序号），不收文本', () => {
    document.body.innerHTML = '<div><section><button>文本A</button></section></div>';
    const btn = document.querySelector('button')!;
    const p1 = computeDomPath(btn);
    document.body.innerHTML = '<div><section><button>文本B完全变了</button></section></div>';
    const p2 = computeDomPath(document.querySelector('button')!);
    expect(p1).toEqual(p2);
    expect(p1).toEqual(['div', 'section', 'button']);
  });

  it('同层多同类元素加 nth-of-type 消歧', () => {
    document.body.innerHTML = '<div><span>a</span><span>b</span></div>';
    const second = document.querySelectorAll('span')[1];
    const p = computeDomPath(second);
    expect(p[p.length - 1]).toContain('nth-of-type(2)');
  });

  it('结构不同则路径不同', () => {
    document.body.innerHTML = '<div><article><button>x</button></article></div>';
    const a = computeDomPath(document.querySelector('button')!);
    document.body.innerHTML = '<div><aside><button>x</button></aside></div>';
    const b = computeDomPath(document.querySelector('button')!);
    expect(a).not.toEqual(b);
  });
});
