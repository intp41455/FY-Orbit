import { describe, expect, it } from 'vitest';
import {
  FURNITURE_COLORWAYS,
  FURNITURE_ROWS,
  assertFurnitureArtComplete,
  getColorway,
  getColorwayCount,
  getFurnitureRows,
} from './furnitureArt';
import { FURNITURE_CATALOG, FURNITURE_IDS, getFurniture, isFurnitureId } from './furnitureCatalog';

/**
 * 家具注册表 + 像素美术的契约测试。
 *
 * 这里守的是「诚实原则」的前置条件：如果注册表宣称有某件家具、美术却画不出来，
 * 渲染层会画出空白精灵 —— 那就是「假装成功」。所以完整性自检必须是硬断言。
 */
describe('W1 家具注册表', () => {
  it('目录 ≥12 种（任务书硬要求），且 id 唯一', () => {
    expect(FURNITURE_CATALOG.length).toBeGreaterThanOrEqual(12);
    expect(new Set(FURNITURE_IDS).size).toBe(FURNITURE_CATALOG.length);
  });

  it('≥4 件家具由小屋等级解锁（11 号文档 / 任务书增补第 3 条）', () => {
    const levelUnlocked = FURNITURE_CATALOG.filter((f) => f.unlockedBy === 'level');
    expect(levelUnlocked.length).toBeGreaterThanOrEqual(4);
    for (const f of levelUnlocked) {
      expect(f.unlockLevel).toBeGreaterThanOrEqual(2);
    }
  });

  it('每件家具尺寸格数 ≥1×1，且墙面/天花板家具不占地板格', () => {
    for (const f of FURNITURE_CATALOG) {
      expect(f.sizeCells.w).toBeGreaterThanOrEqual(1);
      expect(f.sizeCells.h).toBeGreaterThanOrEqual(1);
      if (f.mount !== 'floor') {
        expect(f.sizeCells.w * f.sizeCells.h).toBeLessThanOrEqual(4);
      }
    }
  });

  it('id 查询：命中/未命中', () => {
    expect(getFurniture('bed')?.label).toBe('床');
    expect(getFurniture('__nope__')).toBeUndefined();
    expect(isFurnitureId('bookshelf')).toBe(true);
    expect(isFurnitureId(42)).toBe(false);
  });
});

describe('W1 家具像素美术', () => {
  it('完整性自检通过：每个 id 都有矩阵 + ≥2 档配色 + 调色板覆盖全部字符', () => {
    expect(() => assertFurnitureArtComplete()).not.toThrow();
  });

  it('每件家具至少 2 档配色（任务书第 2 条）', () => {
    for (const id of FURNITURE_IDS) {
      expect(getColorwayCount(id)).toBeGreaterThanOrEqual(2);
    }
  });

  it('矩阵尺寸：每行等宽、末行不透明（贴地接触面）', () => {
    for (const [id, rows] of Object.entries(FURNITURE_ROWS)) {
      const width = rows[0]!.length;
      for (const row of rows) {
        expect(row.length, `${id} 行宽不一致`).toBe(width);
      }
      const last = rows[rows.length - 1]!;
      // 末行不能整行透明，否则家具会「悬空」
      expect([...last].some((c) => c !== '.' && c !== ' '), `${id} 末行全透明`).toBe(true);
    }
  });

  it('getFurnitureRows 对未知 id 返回 undefined（不抛错，由调用方降级）', () => {
    expect(getFurnitureRows('bed')).toBeDefined();
    expect(getFurnitureRows('not-a-furniture')).toBeUndefined();
  });

  it('getColorway 越界索引夹取到合法档位；未知家具 id 明确抛错', () => {
    expect(getColorway('bed', -5).id).toBe(FURNITURE_COLORWAYS.bed![0]!.id);
    expect(getColorway('bed', 999).id).toBe(
      FURNITURE_COLORWAYS.bed![FURNITURE_COLORWAYS.bed!.length - 1]!.id,
    );
    expect(() => getColorway('__nope__', 0)).toThrow(/未知家具/);
  });

  it('调色板颜色均为合法 0xRRGGBB（避免 hex 解析回退成默认蓝）', () => {
    for (const list of Object.values(FURNITURE_COLORWAYS)) {
      for (const cw of list ?? []) {
        for (const color of Object.values(cw.palette)) {
          expect(Number.isInteger(color)).toBe(true);
          expect(color).toBeGreaterThanOrEqual(0);
          expect(color).toBeLessThanOrEqual(0xffffff);
        }
      }
    }
  });
});
