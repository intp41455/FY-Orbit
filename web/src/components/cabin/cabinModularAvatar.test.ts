import { describe, it, expect } from 'vitest';
import {
  MODULAR_HAIRSTYLES,
  MODULAR_HAIR_TONES,
  MODULAR_OUTFITS,
  MODULAR_EXPRESSIONS,
  MODULAR_ACCESSORIES,
  MODULAR_COMBINATIONS_COUNT,
  generateRandomModularAvatar,
  exportAvatarFrames,
  AVATAR_WIDTH,
  AVATAR_HEIGHT,
} from './cabinModularAvatar';
import { assertPaletteCoversMatrix } from '../avatar/avatarPixels';

describe('微像素高精角色部件库 (cabinModularAvatar)', () => {
  it('部件库完整性与排列组合容量远超 18,432 种', () => {
    // 8+ 款发型
    expect(MODULAR_HAIRSTYLES.length).toBeGreaterThanOrEqual(8);
    // 8+ 款发色
    expect(MODULAR_HAIR_TONES.length).toBeGreaterThanOrEqual(8);
    // 8+ 款服装
    expect(MODULAR_OUTFITS.length).toBeGreaterThanOrEqual(8);
    // 6+ 款面部神态
    expect(MODULAR_EXPRESSIONS.length).toBeGreaterThanOrEqual(6);
    // 6+ 款随身饰品
    expect(MODULAR_ACCESSORIES.length).toBeGreaterThanOrEqual(6);

    // 组合数至少 18,432+
    expect(MODULAR_COMBINATIONS_COUNT).toBeGreaterThanOrEqual(18432);
    expect(MODULAR_COMBINATIONS_COUNT).toBe(64000);
  });

  it('随机生成微像素角色结构与矩阵尺寸合法', () => {
    const avatar = generateRandomModularAvatar();

    expect(avatar.id).toBeDefined();
    expect(avatar.name).toBeTruthy();
    expect(avatar.layers).toBeDefined();
    expect(avatar.matrix).toBeDefined();
    expect(avatar.matrix.length).toBe(AVATAR_HEIGHT);
    expect(avatar.matrix[0].length).toBe(AVATAR_WIDTH);

    // 校验每个图层的尺寸
    const layerNames = ['shadow', 'body', 'hair', 'face', 'outfit', 'accessory', 'hand_item', 'outline'];
    for (const name of layerNames) {
      expect(avatar.layers[name]).toBeDefined();
      expect(avatar.layers[name].length).toBe(AVATAR_HEIGHT);
      expect(avatar.layers[name][0].length).toBe(AVATAR_WIDTH);
    }

    // 调色板全覆盖，不可有未定义的漏色字符
    expect(() => {
      assertPaletteCoversMatrix(avatar.matrix, avatar.charPalette);
    }).not.toThrow();

    // 附带规范的 HouseAvatar
    expect(avatar.houseAvatar.fingerprint).toBe(avatar.id);
    expect(avatar.houseAvatar.width).toBe(24);
    expect(avatar.houseAvatar.height).toBe(48);
  });

  it('根据个人画像（MBTI/五行/心境）能够生成具有个性特质的独特角色', () => {
    const customProfile = {
      mbti: 'INTJ',
      bazi_element: '金',
      mood: 'focused',
      name: '星枢',
    };

    const avatarA = generateRandomModularAvatar('seed_intj_gold_1', customProfile as any);
    const avatarB = generateRandomModularAvatar('seed_intj_gold_1', customProfile as any);

    // 相同 seed 具有确定性
    expect(avatarA.id).toBe(avatarB.id);
    expect(avatarA.traits.hairStyle).toBe(avatarB.traits.hairStyle);
    expect(avatarA.traits.outfit).toBe(avatarB.traits.outfit);
    expect(avatarA.matrix).toEqual(avatarB.matrix);

    // 不同 seed 具有独特性
    const avatarC = generateRandomModularAvatar('seed_enfp_fire_2', {
      mbti: 'ENFP',
      bazi_element: '火',
      mood: 'sunny',
      name: '暖阳',
    } as any);

    expect(avatarC.element).toBe('火');
    expect(avatarC.mbti).toBe('ENFP');
    expect(avatarC.matrix).not.toEqual(avatarA.matrix);
  });

  it('exportAvatarFrames 正确导出待机与行走动画帧与色板', () => {
    const avatar = generateRandomModularAvatar('test_export_seed');
    const exported = exportAvatarFrames(avatar);

    expect(exported.idleMatrixFrames).toHaveLength(4);
    expect(exported.walkMatrixFrames).toHaveLength(2);
    expect(exported.charPalette).toEqual(avatar.charPalette);
    expect(exported.pixelPalette).toBeDefined();

    // 验证待机动画的 4 帧都符合 24×48 规范
    for (const frame of exported.idleMatrixFrames) {
      expect(frame.length).toBe(AVATAR_HEIGHT);
      expect(frame[0].length).toBe(AVATAR_WIDTH);
      expect(() => {
        assertPaletteCoversMatrix(frame, exported.charPalette);
      }).not.toThrow();
    }

    // 验证行走动画的 2 帧
    for (const frame of exported.walkMatrixFrames) {
      expect(frame.length).toBe(AVATAR_HEIGHT);
      expect(frame[0].length).toBe(AVATAR_WIDTH);
      expect(() => {
        assertPaletteCoversMatrix(frame, exported.charPalette);
      }).not.toThrow();
    }
  });
});
