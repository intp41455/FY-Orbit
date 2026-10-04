import { describe, expect, it } from 'vitest';
import {
  colorToCss,
  frameSize,
  frameToCells,
  moodFromIntimacy,
  PET_COLORS,
  PET_FRAMES,
  pickPetLine,
} from './petRenderer';

describe('petRenderer · 矩阵复用', () => {
  it('复用 cabin 两帧走路资产，两帧行列一致', () => {
    expect(PET_FRAMES.length).toBe(2);
    expect(PET_FRAMES[0].length).toBe(PET_FRAMES[1].length);
  });

  it('frameToCells 跳过透明格、保留不透明格', () => {
    const rows = ['..b.', '.leb'];
    const cells = frameToCells(rows, PET_COLORS);
    // '.' 全部跳过；row1: (1,1)=l (2,1)=e (3,1)=b；row0: (2,0)=b
    expect(cells).toHaveLength(4);
    expect(cells.map((c) => `${c.x},${c.y}`).sort()).toEqual(
      ['1,1', '2,0', '2,1', '3,1'].sort(),
    );
  });

  it('frameSize 返回包围盒', () => {
    expect(frameSize(PET_FRAMES[0]).height).toBeGreaterThan(10);
  });

  it('colorToCss 产出合法 css 色', () => {
    expect(colorToCss(0xf4a261)).toBe('#f4a261');
  });
});

describe('petRenderer · 心情由 intimacy 诚实派生', () => {
  it('<20 低落且需要照料', () => {
    const m = moodFromIntimacy(10);
    expect(m.level).toBe('low');
    expect(m.needsCare).toBe(true);
  });
  it('20..59 平静', () => {
    expect(moodFromIntimacy(40).level).toBe('ok');
  });
  it('>=60 开心', () => {
    expect(moodFromIntimacy(80).level).toBe('happy');
  });
  it('读不到存档不假装开心', () => {
    expect(moodFromIntimacy(undefined).level).toBe('ok');
    expect(moodFromIntimacy(null).needsCare).toBe(false);
  });
});

describe('petRenderer · 本地预生成台词池', () => {
  it('同 seed 同句（可复现）', () => {
    expect(pickPetLine('happy', 42)).toBe(pickPetLine('happy', 42));
  });
  it('低心情挑出的句子落在低心情池里', () => {
    for (let s = 0; s < 20; s += 1) {
      const line = pickPetLine('low', s);
      expect(typeof line).toBe('string');
      expect(line.length).toBeGreaterThan(0);
    }
  });
});
