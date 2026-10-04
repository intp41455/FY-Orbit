/**
 * G2-4 性能护栏：粒子共享池「两类相加 ≤ 60，不是各 60」。
 *
 * 注意：本测试只依赖【纯数据模块】cabinPixelArt（零 pixi / 零 DOM），
 * 以便在任意 vitest 环境下稳定加载。渲染层 cabinScene.ts 的 PARTICLE_POOL 直接复用
 * 此处导出的同一常量（单一出处），故对数据契约的断言等价于对渲染层的锁定。
 * 天空全宽（G2-1）的抖动机制断言在 cabinPixels.test.ts。
 */
import { describe, it, expect } from 'vitest';
import type { CabinBackgroundId } from './cabinConfig';
import { PARTICLE_POOL, THEME_ART } from './cabinPixelArt';

describe('G2-4 · 粒子共享池护栏（两类粒子相加 ≤ 60，不是各 60）', () => {
  it('PARTICLE_POOL 上限 = 60', () => {
    expect(PARTICLE_POOL).toBe(60);
  });

  const ids = Object.keys(THEME_ART) as CabinBackgroundId[];
  for (const id of ids) {
    it(`${id}: ≥2 类粒子且合计 count ≤ 60`, () => {
      const kinds = THEME_ART[id].particles.kinds;
      expect(kinds.length).toBeGreaterThanOrEqual(2);
      const total = kinds.reduce((s, k) => s + k.count, 0);
      expect(total).toBeLessThanOrEqual(PARTICLE_POOL);
    });
  }

  it('snowcave 覆盖集（40 + 20）也遵守共享池', () => {
    const snowSet = [
      { kind: 'snow' as const, count: 40, color: 0xffffff },
      { kind: 'stardust' as const, count: 20, color: 0xcfe3ff, centerColor: 0xffffff, additive: true },
    ];
    const total = snowSet.reduce((s, k) => s + k.count, 0);
    expect(total).toBeLessThanOrEqual(PARTICLE_POOL);
  });
});
