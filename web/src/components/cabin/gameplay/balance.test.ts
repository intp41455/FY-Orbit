import { describe, expect, it } from 'vitest';
import {
  MAX_MATERIAL_QTY,
  blueprintMissing,
  currentTutorialStepIndex,
  formatCooldown,
  formatEventReward,
  formatNumber,
  groupMaterialsByTheme,
  intimacyTierLabel,
  isNothingEvent,
  mergeDailyQuests,
  nextLevelProgress,
  preferenceHint,
  resolveUnlock,
  sortSpotsForDisplay,
  spotsForMaterial,
  visibleMaterials,
  type CabinSaveView,
  type EventView,
  type QuestView,
  type SpotStatus,
} from './balance';

/**
 * W2 · 玩法纯函数层测试
 *
 * 重点覆盖**诚实原则的可验证面**：
 *   - 未知/异常输入必须显示「未知」而不是编造数字；
 *   - 「一无所获」事件不得被包装成有收益；
 *   - 家具解锁只认服务端白名单，不做前端乐观推演；
 *   - 性格未识别时不得声称「命中喜好」。
 */

function spot(over: Partial<SpotStatus> = {}): SpotStatus {
  return {
    id: 'forest_pine',
    theme: 'forest',
    label: '老松树',
    material: 'pinecone',
    material_label: '松果',
    qty: 1,
    coins: 8,
    intimacy: 2,
    cooldown_hours: 2,
    fx: 0.5,
    fy: 0.5,
    available: true,
    ready_at: null,
    remaining_seconds: 0,
    collect_count: 0,
    dust: false,
    ...over,
  };
}

/* ---------------- 格式化 ---------------- */

describe('formatNumber', () => {
  it('格式化正常数字', () => {
    expect(formatNumber(1234)).toBe('1,234');
  });

  it('未知值显示破折号而非 0（诚实：不编造数字）', () => {
    expect(formatNumber(undefined)).toBe('—');
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(NaN)).toBe('—');
    expect(formatNumber('12')).toBe('—');
    expect(formatNumber(Infinity)).toBe('—');
  });

  it('0 是合法值，必须如实显示 0', () => {
    expect(formatNumber(0)).toBe('0');
  });
});

describe('formatCooldown', () => {
  it('秒级 / 分级 / 时级分别格式化', () => {
    expect(formatCooldown(45)).toBe('45 秒');
    expect(formatCooldown(125)).toBe('2 分 5 秒');
    expect(formatCooldown(7500)).toBe('2 小时 5 分');
  });

  it('剩余 <= 0 返回 null（调用方显示「可采集」）', () => {
    expect(formatCooldown(0)).toBeNull();
    expect(formatCooldown(-10)).toBeNull();
  });

  it('非法输入不假装有倒计时', () => {
    expect(formatCooldown(NaN)).toBeNull();
  });
});

describe('亲密度展示', () => {
  it('按区间给分档文案', () => {
    expect(intimacyTierLabel(0)).toBe('还在观察');
    expect(intimacyTierLabel(30)).toBe('熟悉');
    expect(intimacyTierLabel(60)).toBe('很亲近');
    expect(intimacyTierLabel(95)).toBe('亲密无间');
  });

  it('未知亲密度诚实标「未知」', () => {
    expect(intimacyTierLabel(Number.NaN)).toBe('未知');
  });
});

/* ---------------- 小屋等级 ---------------- */

describe('nextLevelProgress', () => {
  it('按 W1 阈值 3/7/11/16 给出下一级所需件数', () => {
    expect(nextLevelProgress(0, 1)).toEqual({ nextLevel: 2, need: 3, have: 0 });
    expect(nextLevelProgress(3, 1)).toEqual({ nextLevel: 2, need: 3, have: 3 });
    expect(nextLevelProgress(4, 2)).toEqual({ nextLevel: 3, need: 7, have: 4 });
  });

  it('满级后不再声称还能升级', () => {
    const r = nextLevelProgress(99, 5);
    expect(r.nextLevel).toBeNull();
  });
});

/* ---------------- 背包 ---------------- */

describe('visibleMaterials', () => {
  const save = {
    materials: { pinecone: 3, flower: 0, moonstone: 99 },
    material_catalog: [
      { id: 'pinecone', label: '松果', theme: 'forest' as const, tier: 1 },
      { id: 'flower', label: '花', theme: 'garden' as const, tier: 1 },
      { id: 'moonstone', label: '月石', theme: 'planet' as const, tier: 3 },
      { id: 'never', label: '未拥有', theme: 'stream' as const, tier: 1 },
    ],
  };

  it('过滤掉数量为 0 的材料（不显示空格子）', () => {
    const ids = visibleMaterials(save).map((m) => m.id);
    expect(ids).toEqual(['pinecone', 'moonstone']);
  });

  it('达到上限时标记 capped，供 UI 如实提示', () => {
    const moon = visibleMaterials(save).find((m) => m.id === 'moonstone');
    expect(moon?.capped).toBe(true);
    expect(moon?.qty).toBe(MAX_MATERIAL_QTY);
  });

  it('按 catalog 顺序输出（服务端顺序即真源）', () => {
    expect(visibleMaterials(save)[0]?.id).toBe('pinecone');
  });
});

describe('groupMaterialsByTheme', () => {
  it('按主题分组并丢弃空组', () => {
    const groups = groupMaterialsByTheme([
      { theme: 'forest' as const, qty: 2 },
      { theme: 'planet' as const, qty: 1 },
      { theme: 'garden' as const, qty: 0 },
    ]);
    expect(groups.map((g) => g.theme)).toEqual(['forest', 'planet']);
  });
});

/* ---------------- 家具解锁（诚实：不前端推演） ---------------- */

describe('resolveUnlock', () => {
  const base = { houseLevel: 1, unlockedFurniture: [] as string[] };

  it('default 类开局解锁', () => {
    const r = resolveUnlock(
      { id: 'bed', label: '床', unlockedBy: 'default', unlockLevel: 0, themes: [] },
      base,
    );
    expect(r.unlocked).toBe(true);
    expect(r.reason).toBeNull();
  });

  it('level 类按小屋等级判定，并说明还差多少', () => {
    const rule = { id: 'ceiling_lamp', label: '吊灯', unlockedBy: 'level' as const, unlockLevel: 2, themes: [] };
    expect(resolveUnlock(rule, base).unlocked).toBe(false);
    const locked = resolveUnlock(rule, base);
    expect(locked.unlocked).toBe(false);
    if (!locked.unlocked) expect(locked.reason).toContain('Lv2');
    expect(resolveUnlock(rule, { ...base, houseLevel: 2 }).unlocked).toBe(true);
  });

  it('quest/craft 类只认服务端白名单——不因「看起来能做」就解锁', () => {
    const rule = { id: 'crystal_tree', label: '水晶树', unlockedBy: 'quest' as const, unlockLevel: 0, themes: [] };
    // 材料全满也不解锁：解锁权只在服务端
    const rich = { ...base, unlockedFurniture: [], hasMaterials: () => 99 };
    const r = resolveUnlock(rule, rich);
    expect(r.unlocked).toBe(false);
    if (!r.unlocked) expect(r.reason).toContain('尚未解锁');

    const granted = resolveUnlock(rule, { ...base, unlockedFurniture: ['crystal_tree'] });
    expect(granted.unlocked).toBe(true);
  });

  it('craft 类缺料时如实列出还差什么', () => {
    const rule = { id: 'herb_shelf', label: '草药架', unlockedBy: 'craft' as const, unlockLevel: 0, themes: [] };
    const r = resolveUnlock(rule, { ...base, hasMaterials: () => 0 });
    if (!r.unlocked) expect(r.hint).toContain('还差');
  });
});

describe('blueprintMissing', () => {
  it('逐项列出缺口数量', () => {
    const hint = blueprintMissing('herb_shelf', (id) => (id === 'wood' ? 1 : 0));
    expect(hint).toContain('wood×2');
    expect(hint).toContain('flower×2');
    expect(hint).toContain('moss×1');
  });

  it('材料齐了则说明可在制造面板打造', () => {
    expect(blueprintMissing('herb_shelf', () => 99)).toContain('材料已齐');
  });

  it('未知图纸不编造价目（返回 null 而非假提示）', () => {
    expect(blueprintMissing('not_a_blueprint', () => 99)).toBeNull();
  });

  it('没给 hasMaterials 时不假装知道缺什么', () => {
    expect(blueprintMissing('herb_shelf')).toBe('在制造面板查看所需材料');
  });
});

/* ---------------- 任务 ---------------- */

describe('currentTutorialStepIndex', () => {
  it('未完成时返回当前步序号', () => {
    expect(currentTutorialStepIndex({ step: 2, progress: {}, claimed: [], completed: false, baseline_items: 0 })).toBe(2);
  });

  it('已完成 / 无数据时返回 null（不硬造步骤）', () => {
    expect(currentTutorialStepIndex({ step: 5, progress: {}, claimed: [], completed: true, baseline_items: 0 })).toBeNull();
    expect(currentTutorialStepIndex(null)).toBeNull();
  });
});

describe('mergeDailyQuests', () => {
  const pool: QuestView[] = [
    { id: 'd_gather3', title: '采集 3 次', event: 'explore', target: 3, reward_label: '鹅卵石×2' },
    { id: 'd_feed2', title: '喂食 2 次', event: 'feed', target: 2, reward_label: '鱼×1' },
  ];

  it('合并服务端进度并算出可领状态', () => {
    const merged = mergeDailyQuests(
      { date: '2026-10-04', ids: ['d_gather3', 'd_feed2'], progress: { d_gather3: 3, d_feed2: 1 }, claimed: [] },
      pool,
    );
    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({ progress: 3, ready: true, claimed: false });
    expect(merged[1]).toMatchObject({ progress: 1, ready: false });
  });

  it('已领取的任务不再标 ready（避免重复领）', () => {
    const merged = mergeDailyQuests(
      { date: 'd', ids: ['d_gather3'], progress: { d_gather3: 5 }, claimed: ['d_gather3'] },
      pool,
    );
    expect(merged[0]?.ready).toBe(false);
    expect(merged[0]?.claimed).toBe(true);
  });

  it('服务端给了未知任务 id 时跳过而不是崩溃', () => {
    const merged = mergeDailyQuests(
      { date: 'd', ids: ['ghost_id'], progress: {}, claimed: [] },
      pool,
    );
    expect(merged).toEqual([]);
  });

  it('无 daily 数据时返回空数组', () => {
    expect(mergeDailyQuests(null, pool)).toEqual([]);
  });
});

/* ---------------- 探险点 ---------------- */

describe('sortSpotsForDisplay', () => {
  it('按服务端给的 fy/fx 排序（视觉位由后端决定）', () => {
    const sorted = sortSpotsForDisplay([
      spot({ id: 'b', fy: 0.9, fx: 0.1 }),
      spot({ id: 'a', fy: 0.1, fx: 0.5 }),
      spot({ id: 'c', fy: 0.1, fx: 0.2 }),
    ]);
    expect(sorted.map((s) => s.id)).toEqual(['c', 'a', 'b']);
  });

  it('不修改入参（原数组保持不变）', () => {
    const input = [spot({ id: 'x', fy: 0.9 }), spot({ id: 'y', fy: 0.1 })];
    const copy = [...input];
    sortSpotsForDisplay(input);
    expect(input).toEqual(copy);
  });
});

describe('spotsForMaterial', () => {
  it('找出产出该材料的探险点', () => {
    const list = [spot({ id: 'a', material: 'wood' }), spot({ id: 'b', material: 'flower' })];
    expect(spotsForMaterial(list, 'wood').map((s) => s.id)).toEqual(['a']);
    expect(spotsForMaterial(list, 'nope')).toEqual([]);
  });
});

/* ---------------- 事件（诚实：nothing 就是 nothing） ---------------- */

describe('isNothingEvent', () => {
  it('识别「一无所获」', () => {
    expect(isNothingEvent({ id: 'nothing', text: '' })).toBe(true);
    expect(isNothingEvent({ id: 'cat_gift', text: '' })).toBe(false);
    expect(isNothingEvent(null)).toBe(false);
  });
});

describe('formatEventReward', () => {
  it('nothing 事件返回 null——绝不显示 0 金币之类的假收益', () => {
    expect(formatEventReward({ id: 'nothing', text: '' })).toBeNull();
  });

  it('组合材料与金币', () => {
    const ev: EventView = { id: 'x', text: '', items: { moss: 2 }, coins: 12 };
    expect(formatEventReward(ev)).toBe('moss×2 · 12 金币');
  });

  it('没有可展示收益时返回 null（不编造）', () => {
    expect(formatEventReward({ id: 'x', text: '', items: {}, coins: 0 })).toBeNull();
    expect(formatEventReward({ id: 'x', text: '' })).toBeNull();
  });
});

/* ---------------- 喜好（recognized=false 不得说「命中喜好」） ---------------- */

describe('preferenceHint', () => {
  const prefs: CabinSaveView['preferences'] = {
    personality: 'grumpy',
    recognized: true,
    person: { food: 'mushroom', food_label: '蘑菇', interaction: 'a', touch: 'b', note: '心情会变好' },
    pet: { food: 'fish', food_label: '鱼', interaction: 'a', touch: 'b', note: '会摇尾巴' },
  };

  it('识别成功时说明喜好', () => {
    expect(preferenceHint(prefs, 'person')).toContain('蘑菇');
    expect(preferenceHint(prefs, 'person')).toContain('心情会变好');
  });

  it('未识别时如实标注「性格未识别」，不谎称命中', () => {
    const unknown = { ...prefs, recognized: false };
    const hint = preferenceHint(unknown, 'pet');
    expect(hint).toContain('性格未识别');
  });

  it('服务端没返回偏好时明说未知', () => {
    expect(preferenceHint(null, 'person')).toContain('未知');
  });
});
