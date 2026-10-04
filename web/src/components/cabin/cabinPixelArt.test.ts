import { describe, it, expect } from 'vitest';
import type { CabinHouseId } from './cabinConfig';
import {
  CABIN_HOUSE_ART,
  CRYSTAL_ROWS,
  HOUSE_PERSON_RATIO,
  PERSON_SCALE,
  PERSON_WALK_FRAMES,
  houseScaleFactor,
  luma,
  planetCrystal,
} from './cabinPixelArt';

describe('G2-2 · PLANET_CRYSTAL 明度顺序（无倒挂）', () => {
  it('最亮阶 l 比标称 c 更亮，且逐级递减 c > C > d', () => {
    expect(luma(planetCrystal.l)).toBeGreaterThan(luma(planetCrystal.c));
    expect(luma(planetCrystal.c)).toBeGreaterThan(luma(planetCrystal.C));
    expect(luma(planetCrystal.C)).toBeGreaterThan(luma(planetCrystal.d));
  });

  it('crystal 矩阵用到了最亮高光 l（否则 l 是死键，修复无意义）', () => {
    expect(CRYSTAL_ROWS.join('').includes('l')).toBe(true);
  });
});

describe('G2-3 · 房屋:小人屏显高度比 ≈ 2.0（落在 1.8-2.2）', () => {
  const ids = Object.keys(CABIN_HOUSE_ART) as CabinHouseId[];
  const personWorldH = PERSON_WALK_FRAMES[0].length * PERSON_SCALE;

  for (const id of ids) {
    it(`${id} 的屏显高度比达标`, () => {
      // 屏显高度比 = (房屋矩阵高 × 房屋缩放) / (小人矩阵高 × 小人缩放)，worldScale 在比值中约去。
      const ratio = (CABIN_HOUSE_ART[id].rows.length * houseScaleFactor(id)) / personWorldH;
      expect(ratio).toBeGreaterThanOrEqual(1.8);
      expect(ratio).toBeLessThanOrEqual(2.2);
      // 归一化缩放保证比值恰为目标值，与分辨率 / 窗口无关。
      expect(ratio).toBeCloseTo(HOUSE_PERSON_RATIO, 6);
    });
  }
});
