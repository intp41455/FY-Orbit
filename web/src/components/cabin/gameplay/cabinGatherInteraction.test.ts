/**
 * B1 / B2 交互与采集表现层测试（判据 I1 - I4）。
 *
 * 覆盖：
 * 1. I1 靠近可交互物：距离 ≤ 1 格头顶弹「✋」提示（有明确测试）；
 * 2. I2 动画键真实播放：断言播放行为（状态机、当前动画、帧步进、播放历史）；
 * 3. I3 七类采集全覆盖：砍树/挖矿/钓鱼/采果/拔草/打水/捡杂物产出直接进背包；
 * 4. I4 3 秒规则：动作词、材料、快捷键 [E]、耗时预估齐全，秒级认知无门槛。
 */

import { describe, expect, it, vi } from 'vitest';
import {
  GATHER_ANIMATIONS,
  GATHER_ACTION_LABELS,
  GatherAnimationPlayer,
  SEVEN_GATHER_CATEGORIES,
  buildThreeSecondPrompt,
  executeGatherCycle,
  manhattanDistance,
  INTERACT_RANGE_TILES,
} from './cabinGatherInteraction';
import type { GatherRow } from './lifeApi';

describe('B1 · I1 靠近可交互物与头顶小手提示', () => {
  const sampleNode: GatherRow = {
    id: 'forest_pine',
    label: '老松树',
    action: 'chop',
    action_label: '砍伐',
    animation: 'anim_chop',
    material: 'wood',
    qty_range: [1, 2],
    minutes: 12,
    tile: [18, 62],
    distance: 1,
    in_range: true,
  };

  it('玩家在交互半径内 (距离 ≤ 1 格) 触发头顶「✋」小手提示', () => {
    // 玩家在 (18, 61)，与 (18, 62) 曼哈顿距离为 1 格
    const promptCard = buildThreeSecondPrompt(sampleNode, [18, 61]);
    expect(promptCard.inRange).toBe(true);
    expect(promptCard.distanceTiles).toBe(1);
    expect(promptCard.promptIcon).toBe('✋');
    expect(promptCard.fullPrompt).toContain('✋ 砍伐 老松树（wood）');
  });

  it('曼哈顿距离计算准确对齐网格契约', () => {
    expect(manhattanDistance([10, 20], [10, 21])).toBe(1);
    expect(manhattanDistance([10, 20], [11, 21])).toBe(2);
    expect(manhattanDistance([0, 0], [0, 0])).toBe(0);
    expect(INTERACT_RANGE_TILES).toBe(1);
  });

  it('超出交互范围 (距离 > 1 格) 明确标记不在范围内', () => {
    // 玩家在 (18, 50)，与 (18, 62) 距离为 12 格
    const outOfRangeNode: GatherRow = { ...sampleNode, in_range: false };
    const promptCard = buildThreeSecondPrompt(outOfRangeNode, [18, 50]);
    expect(promptCard.inRange).toBe(false);
    expect(promptCard.distanceTiles).toBe(12);
  });
});

describe('B1 / B2 · I2 动画键实际被触发播放（断言播放行为，而非仅键定义存在）', () => {
  it('动画映射表完备覆盖八大动作键', () => {
    const expectedActions = ['chop', 'mine', 'fish', 'pick', 'harvest', 'dig', 'water', 'collect'];
    for (const act of expectedActions) {
      expect(GATHER_ANIMATIONS[act]).toBe(`anim_${act}`);
      expect(GATHER_ACTION_LABELS[act]).toBeTruthy();
    }
  });

  it('实际触发播放：动画状态机切换至播放态并推进帧序列', () => {
    const player = new GatherAnimationPlayer();
    expect(player.getState().isPlaying).toBe(false);
    expect(player.getState().currentAnimation).toBeNull();

    // 1. 触发砍树动画播放
    const stateStart = player.play('chop', 400);
    expect(stateStart.isPlaying).toBe(true);
    expect(stateStart.currentAnimation).toBe('anim_chop');
    expect(stateStart.currentFrame).toBe(0);

    // 2. 帧步进 (deltaMs = 120ms)
    const stateMid = player.tick(120);
    expect(stateMid.isPlaying).toBe(true);
    expect(stateMid.currentFrame).toBeGreaterThan(0);

    // 3. 播放结束 (超出时长)
    const stateEnd = player.tick(400);
    expect(stateEnd.isPlaying).toBe(false);
    expect(stateEnd.currentFrame).toBe(stateEnd.totalFrames - 1);
  });

  it('连续多次动作播放行为被精确计入播放历史', () => {
    const player = new GatherAnimationPlayer();
    player.play('mine');
    player.play('fish');
    player.play('water');
    player.play('harvest');

    const history = player.getPlaybackHistory();
    expect(history).toEqual(['anim_mine', 'anim_fish', 'anim_water', 'anim_harvest']);
  });
});

describe('B2 · I3 七类采集产出直接进背包', () => {
  it('七大采集体系涵盖全量工种与核心原材料', () => {
    expect(SEVEN_GATHER_CATEGORIES.length).toBe(7);
    const keys = SEVEN_GATHER_CATEGORIES.map((c) => c.key);
    expect(keys).toEqual(['chop', 'mine', 'fish', 'pick', 'harvest', 'water', 'collect']);
  });

  const testCases: Array<{
    category: string;
    action: string;
    material: string;
    node: GatherRow;
  }> = [
    {
      category: '砍树 (Chop)',
      action: 'chop',
      material: 'wood',
      node: {
        id: 'forest_pine',
        label: '老松树',
        action: 'chop',
        action_label: '砍伐',
        animation: 'anim_chop',
        material: 'wood',
        qty_range: [2, 4],
        minutes: 12,
        tile: [18, 62],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '挖矿 (Mine)',
      action: 'mine',
      material: 'crystal',
      node: {
        id: 'planet_crystal',
        label: '晶矿脉',
        action: 'mine',
        action_label: '开采',
        animation: 'anim_mine',
        material: 'crystal',
        qty_range: [1, 2],
        minutes: 15,
        tile: [10, 20],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '钓鱼 (Fish)',
      action: 'fish',
      material: 'fish',
      node: {
        id: 'stream_fish',
        label: '浅滩游鱼',
        action: 'fish',
        action_label: '垂钓',
        animation: 'anim_fish',
        material: 'fish',
        qty_range: [1, 3],
        minutes: 18,
        tile: [25, 63],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '采果 (Pick)',
      action: 'pick',
      material: 'mushroom',
      node: {
        id: 'forest_mush',
        label: '蘑菇圈',
        action: 'pick',
        action_label: '拾取',
        animation: 'anim_pick',
        material: 'mushroom',
        qty_range: [1, 2],
        minutes: 6,
        tile: [27, 58],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '拔草 (Harvest)',
      action: 'harvest',
      material: 'wheat',
      node: {
        id: 'field_wheat',
        label: '金黄麦垄',
        action: 'harvest',
        action_label: '收割',
        animation: 'anim_harvest',
        material: 'wheat',
        qty_range: [2, 4],
        minutes: 9,
        tile: [30, 40],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '打水 (Water)',
      action: 'water',
      material: 'water',
      node: {
        id: 'garden_well',
        label: '石砌井台',
        action: 'water',
        action_label: '打水',
        animation: 'anim_water',
        material: 'water',
        qty_range: [1, 2],
        minutes: 5,
        tile: [52, 64],
        distance: 1,
        in_range: true,
      },
    },
    {
      category: '捡杂物 (Collect)',
      action: 'collect',
      material: 'pinecone',
      node: {
        id: 'forest_cone',
        label: '散落松果',
        action: 'collect',
        action_label: '收集',
        animation: 'anim_collect',
        material: 'pinecone',
        qty_range: [1, 3],
        minutes: 4,
        tile: [34, 66],
        distance: 1,
        in_range: true,
      },
    },
  ];

  for (const tc of testCases) {
    it(`${tc.category}: 触发采集后动画实际播放且产物 ${tc.material} 进入背包`, async () => {
      const animPlayer = new GatherAnimationPlayer();
      const mockAction = vi.fn().mockResolvedValue(undefined);
      const initialBag: Record<string, number> = { coin_pouch: 1 };

      const result = await executeGatherCycle(tc.node, initialBag, animPlayer, mockAction);

      // 验证动画键播放
      expect(result.animationPlayed).toBe(`anim_${tc.action}`);
      expect(animPlayer.getPlaybackHistory()).toContain(`anim_${tc.action}`);

      // 验证网络动作派发
      expect(mockAction).toHaveBeenCalledWith('gather', { node_id: tc.node.id });

      // 验证产物直接进背包
      expect(result.materialGained).toBe(tc.material);
      expect(result.qtyGained).toBeGreaterThanOrEqual(1);
      expect(result.bagAfter[tc.material]).toBe(result.qtyGained);
      expect(result.bagAfter.coin_pouch).toBe(1); // 原有物品不丢失
    });
  }
});

describe('B1 / B2 · I4 3 秒规则达标验证', () => {
  it('采集卡片在 3 秒认知窗口内提供齐全决策要素 (手形标记/动词/材料/按键/耗时预估)', () => {
    const node: GatherRow = {
      id: 'garden_flower',
      label: '芳香花丛',
      action: 'harvest',
      action_label: '采收',
      animation: 'anim_harvest',
      material: 'flower',
      qty_range: [1, 3],
      minutes: 5,
      tile: [22, 60],
      distance: 1,
      in_range: true,
    };

    const card = buildThreeSecondPrompt(node, [22, 59]);

    expect(card.compliant3sRule).toBe(true);
    expect(card.promptIcon).toBe('✋');
    expect(card.actionText).toBe('采收');
    expect(card.targetLabel).toBe('芳香花丛');
    expect(card.materialLabel).toBe('flower');
    expect(card.buttonLabel).toBe('[E] 采收');
    expect(card.timeEstimateText).toBe('耗时: 5 游戏分钟');
    expect(card.yieldEstimateText).toBe('预估产出: flower×1~3');
  });
});
