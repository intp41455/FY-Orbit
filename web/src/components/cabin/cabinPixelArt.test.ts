import { describe, it, expect } from 'vitest';
import type { CabinHouseId } from './cabinConfig';
import {
  BUSH_ROWS,
  CABIN_HOUSE_ART,
  CABIN_ROWS,
  CASTLE_ROWS,
  CAVE_ROWS,
  CLOUD_ROWS,
  CRYSTAL_ROWS,
  FLOWER_ROWS,
  HOUSE_PERSON_RATIO,
  PEBBLE_ROWS,
  PERSON_SCALE,
  PERSON_WALK_FRAMES,
  PINE_ROWS,
  ROCK_ROWS,
  TREE_ROUND_ROWS,
  TUFT_ROWS,
  VILLA_ROWS,
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

describe('A3 · 场景元素精细重绘（×2 尺寸与非背景像素占比）', () => {
  const natureElements = [
    { name: 'CLOUD_ROWS', rows: CLOUD_ROWS, h: 16, w: 44 },
    { name: 'PINE_ROWS', rows: PINE_ROWS, h: 44, w: 28 },
    { name: 'TREE_ROUND_ROWS', rows: TREE_ROUND_ROWS, h: 36, w: 32 },
    { name: 'BUSH_ROWS', rows: BUSH_ROWS, h: 14, w: 24 },
    { name: 'TUFT_ROWS', rows: TUFT_ROWS, h: 10, w: 16 },
    { name: 'FLOWER_ROWS', rows: FLOWER_ROWS, h: 16, w: 10 },
    { name: 'ROCK_ROWS', rows: ROCK_ROWS, h: 12, w: 20 },
    { name: 'PEBBLE_ROWS', rows: PEBBLE_ROWS, h: 6, w: 10 },
  ];

  for (const item of natureElements) {
    it(`${item.name} 尺寸精确达标 (${item.h}×${item.w})`, () => {
      expect(item.rows).toHaveLength(item.h);
      for (const row of item.rows) {
        expect(row).toHaveLength(item.w);
      }
    });

    it(`${item.name} 非背景像素占比不低于 0.22`, () => {
      const total = item.h * item.w;
      let solid = 0;
      for (const row of item.rows) {
        for (const ch of row) {
          if (ch !== '.' && ch !== ' ') solid++;
        }
      }
      expect(solid / total).toBeGreaterThanOrEqual(0.22);
    });
  }

  const houses = [
    { id: 'villa', rows: VILLA_ROWS },
    { id: 'cabin', rows: CABIN_ROWS },
    { id: 'cave', rows: CAVE_ROWS },
    { id: 'bunker', rows: CABIN_HOUSE_ART.bunker.rows },
    { id: 'castle', rows: CASTLE_ROWS },
  ];

  for (const house of houses) {
    it(`房屋 ${house.id} 尺寸落在 44-64 × 78-80 范围`, () => {
      expect(house.rows.length).toBeGreaterThanOrEqual(44);
      expect(house.rows.length).toBeLessThanOrEqual(64);
      for (const row of house.rows) {
        expect(row.length).toBeGreaterThanOrEqual(78);
        expect(row.length).toBeLessThanOrEqual(80);
      }
    });

    it(`房屋 ${house.id} 非背景像素占比不低于 0.22`, () => {
      const h = house.rows.length;
      const w = house.rows[0].length;
      const total = h * w;
      let solid = 0;
      for (const row of house.rows) {
        for (const ch of row) {
          if (ch !== '.' && ch !== ' ') solid++;
        }
      }
      expect(solid / total).toBeGreaterThanOrEqual(0.22);
    });
  }

  it('所有房屋矩阵中的字符均在对应调色板中已定义（无未映射键）', () => {
    const ids = Object.keys(CABIN_HOUSE_ART) as CabinHouseId[];
    for (const id of ids) {
      const art = CABIN_HOUSE_ART[id];
      const paletteKeys = new Set(Object.keys(art.palette));
      for (const row of art.rows) {
        for (const ch of row) {
          if (ch === '.' || ch === ' ') continue;
          expect(paletteKeys.has(ch), `房屋 ${id} 包含未定义色键 '${ch}'`).toBe(true);
        }
      }
    }
  });
});
