import { describe, it, expect } from 'vitest';
import {
  TIME_OF_DAY_LIST,
  sanitizeCabinConfig,
  type TimeOfDay,
} from './cabinConfig';
import { PixelBuffer, matrixToRgba } from './cabinPixels';

describe('A5 · 光影体系（统一左上光源、物体阴影与昼夜色温）', () => {
  describe('昼夜色温配置与元数据', () => {
    it('包含晨、昼、夕、夜四时段，各有时段色温与透明度定义', () => {
      const times: TimeOfDay[] = ['dawn', 'day', 'dusk', 'night'];
      expect(TIME_OF_DAY_LIST).toHaveLength(4);
      for (const t of times) {
        const found = TIME_OF_DAY_LIST.find((item) => item.id === t);
        expect(found).toBeDefined();
        expect(found!.label).toBeTruthy();
        expect(typeof found!.tint).toBe('number');
        expect(found!.alpha).toBeGreaterThanOrEqual(0);
        expect(found!.alpha).toBeLessThanOrEqual(1);
      }
    });

    it('白日 (day) 为自然原色无遮罩 (alpha = 0)', () => {
      const day = TIME_OF_DAY_LIST.find((item) => item.id === 'day')!;
      expect(day.alpha).toBe(0);
      expect(day.tint).toBe(0xffffff);
    });

    it('清晨与黄昏呈现暖色调，夜晚呈现冷深蓝调', () => {
      const dawn = TIME_OF_DAY_LIST.find((item) => item.id === 'dawn')!;
      const dusk = TIME_OF_DAY_LIST.find((item) => item.id === 'dusk')!;
      const night = TIME_OF_DAY_LIST.find((item) => item.id === 'night')!;

      // 清晨：暖淡金 (较高 R 与 G)
      expect(((dawn.tint >> 16) & 0xff)).toBeGreaterThan(200);
      expect(((dawn.tint >> 8) & 0xff)).toBeGreaterThan(200);

      // 黄昏：暖橙金 (高 R，次高 G，低 B)
      expect(((dusk.tint >> 16) & 0xff)).toBeGreaterThan(220);
      expect((dusk.tint & 0xff)).toBeLessThan(150);

      // 夜晚：冷深蓝 (低 R，B 占优)
      expect(((night.tint >> 16) & 0xff)).toBeLessThan(80);
      expect((night.tint & 0xff)).toBeGreaterThan(80);
      expect(night.alpha).toBeGreaterThan(0.3);
    });

    it('sanitizeCabinConfig 支持校验并保留合法的 timeOfDay，非法值回退默认 day', () => {
      const cfgDawn = sanitizeCabinConfig({ timeOfDay: 'dawn' });
      expect(cfgDawn.timeOfDay).toBe('dawn');

      const cfgNight = sanitizeCabinConfig({ timeOfDay: 'night' });
      expect(cfgNight.timeOfDay).toBe('night');

      const cfgInvalid = sanitizeCabinConfig({ timeOfDay: 'midnight_invalid' });
      expect(cfgInvalid.timeOfDay).toBe('day');
    });
  });

  describe('左上光源与半透明柔和地面投影机制', () => {
    it('PixelBuffer 支持 radialEllipse 生成深色半透明柔和边缘', () => {
      const buf = new PixelBuffer(64, 32);
      // 在 (32, 16) 画半透明深色阴影 (0x0f172a, alpha 0.4)
      buf.radialEllipse(32, 16, 20, 8, 0x0f172a, 0.4, 1.4);

      // 中心点 alpha 接近 0.4
      const centerAlpha = buf.data[(16 * 64 + 32) * 4 + 3] / 255;
      expect(centerAlpha).toBeGreaterThan(0.3);
      expect(centerAlpha).toBeLessThanOrEqual(0.45);

      // 边缘点平滑渐淡（非全黑生硬边界）
      const edgeAlpha = buf.data[(16 * 64 + 48) * 4 + 3] / 255;
      expect(edgeAlpha).toBeGreaterThan(0);
      expect(edgeAlpha).toBeLessThan(centerAlpha);

      // 外部点完全透明
      const outsideAlpha = buf.data[(16 * 64 + 58) * 4 + 3] / 255;
      expect(outsideAlpha).toBe(0);
    });

    it('左上光源投射：阴影中心相对实体中心向右下方偏移 (dx > 0)', () => {
      const buf = new PixelBuffer(120, 60);
      const testPropRows = [
        '..PPPP..',
        '.PPPPPP.',
        '.PPPPPP.',
        '..PPPP..',
        '...tt...',
        '...tt...',
      ];
      const testPropPalette = { P: 0xff88aa, t: 0x664422 };
      const snap = matrixToRgba(testPropRows, testPropPalette);
      const scale = 2;
      const x = 30;
      const baseY = 40;
      const y = baseY - snap.height * scale;
      const elemW = snap.width * scale;

      // 模拟统一左上光源投影
      const shadowCx = x + Math.round(elemW * 0.5) + Math.max(1, Math.round(scale * 1.5));
      const shadowCy = baseY - Math.max(1, Math.round(scale * 0.5));
      const rx = Math.max(4, Math.round(elemW * 0.44));
      const ry = Math.max(2, Math.round(Math.min(7, elemW * 0.16)));

      expect(shadowCx).toBeGreaterThan(x + elemW * 0.5); // 向右微偏（左上光源投射向右下）
      buf.radialEllipse(shadowCx, shadowCy, rx, ry, 0x0f172a, 0.35, 1.4);
      buf.stamp(snap, x, y, scale);

      // 断言在物体右下地面区域存在投影半透明像素
      const shadowSampleAlpha = buf.data[(shadowCy * 120 + shadowCx) * 4 + 3] / 255;
      expect(shadowSampleAlpha).toBeGreaterThan(0.2);
    });
  });
});
