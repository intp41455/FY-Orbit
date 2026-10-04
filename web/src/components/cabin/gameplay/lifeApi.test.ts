import { describe, expect, it } from 'vitest';
import fixture from './__fixtures__/lifeSave.sample.json';
import {
  craftLockReason,
  formatMinute,
  gatherPrompt,
  heartsDisplay,
  heartsForPoints,
  hudLine,
  isThemeId,
  MARKER_MEANING,
  MAX_HEARTS,
  settlementConsistent,
  settlementSummary,
  sortedBag,
  THEME_IDS,
  type CraftRow,
  type GatherRow,
  type LifeSave,
  type NpcRow,
  type Settlement,
  type ShopRow,
} from './lifeApi';

/**
 * B 包前端契约测试。
 *
 * fixture 是**后端真实快照**（由 `cabin_life` 生成，见
 * `tests/unit/test_cabin_life.py::test_frontend_fixture_is_current`）。
 * 这里的价值在于：如果后端改了字段名而前端没跟上，或前端开始自己重算规则，
 * 这份测试会红——它锁的是「两端说的是同一件事」。
 */

const save = fixture.save as unknown as LifeSave;
const npcRows = fixture.npc_rows as unknown as NpcRow[];
const shopRows = fixture.shop_rows as unknown as ShopRow[];
const craftRows = fixture.craft_rows as unknown as CraftRow[];
const gatherRows = fixture.gather_rows as unknown as GatherRow[];
const settlement = fixture.settlement as unknown as Settlement;

describe('lifeApi · 主题注册表', () => {
  it('九个主题都在（5 背景 + 四大主题）', () => {
    expect(THEME_IDS).toHaveLength(9);
    expect(THEME_IDS).toContain('forest');
    expect(THEME_IDS).toContain('ink');
  });

  it('未知主题不被当成合法值', () => {
    expect(isThemeId('atlantis')).toBe(false);
    expect(isThemeId('ink')).toBe(true);
  });
});

describe('lifeApi · 快照契约', () => {
  it('快照含后端下发的全部字段', () => {
    for (const key of [
      'owner',
      'theme',
      'clock',
      'weather',
      'bag',
      'coins',
      'skill_exp',
      'affinity',
      'gifts_today',
      'shop',
      'quest_log',
      'gather_counts',
      'version',
    ]) {
      expect(save).toHaveProperty(key);
    }
  });

  it('HUD 文案与后端 hud_line 一致（前端不得二次润色）', () => {
    expect(hudLine(save)).toBe(fixture.hud_line);
  });

  it('天气块自带玩法影响字段（前端不重算）', () => {
    for (const key of ['id', 'label', 'icon', 'light', 'yield_pct', 'pace_pct',
      'demand_pct', 'blocks_gather']) {
      expect(save.weather).toHaveProperty(key);
    }
    expect(save.weather.light).toBeGreaterThanOrEqual(0);
    expect(save.weather.light).toBeLessThanOrEqual(1);
  });
});

describe('lifeApi · 好感心数', () => {
  it('每 100 点一心、10 心封顶', () => {
    expect(heartsForPoints(0)).toBe(0);
    expect(heartsForPoints(99)).toBe(0);
    expect(heartsForPoints(100)).toBe(1);
    expect(heartsForPoints(99999)).toBe(MAX_HEARTS);
  });

  it('负数/NaN 不猜，按 0 心处理', () => {
    expect(heartsForPoints(-5)).toBe(0);
    expect(heartsForPoints(Number.NaN)).toBe(0);
  });

  it('展示串长度恒为 10', () => {
    expect(heartsDisplay(0)).toHaveLength(MAX_HEARTS);
    expect(heartsDisplay(100000)).toBe('♥'.repeat(MAX_HEARTS));
  });

  it('NPC 行的点数与心数自洽', () => {
    for (const row of npcRows) {
      const points = save.affinity[row.id] ?? 0;
      expect(row.hearts).toBe(heartsForPoints(points));
      expect(row.hearts_display).toBe(heartsDisplay(points));
    }
  });

  it('头顶标记语义都有解释（不能只靠颜色/符号）', () => {
    expect(MARKER_MEANING['!']).toBeTruthy();
    expect(MARKER_MEANING['?']).toBeTruthy();
    for (const row of npcRows) {
      expect(row.marker in MARKER_MEANING).toBe(true);
    }
  });
});

describe('lifeApi · 采集提示（3 秒规则）', () => {
  it('范围内给出手提示', () => {
    const row = gatherRows.find((r) => r.in_range);
    expect(row).toBeTruthy();
    expect(gatherPrompt(row)).toContain('✋');
  });

  it('范围外不给提示（不假装能交互）', () => {
    expect(gatherPrompt({ ...gatherRows[0], in_range: false })).toBeNull();
    expect(gatherPrompt(null)).toBeNull();
  });
});

describe('lifeApi · 制作台', () => {
  it('未解锁必须带原因文案', () => {
    for (const row of craftRows) {
      const reason = craftLockReason(row);
      expect(reason).toBeTruthy();
      if (!row.unlocked) expect(reason).toContain('技能');
    }
  });

  it('已解锁行显示已解锁', () => {
    expect(craftLockReason({ ...craftRows[0], unlocked: true })).toBe('已解锁');
  });
});

describe('lifeApi · 日结算', () => {
  it('后端快照自洽', () => {
    expect(settlementConsistent(settlement)).toBe(true);
    expect(settlementSummary(settlement)).not.toContain('异常');
  });

  it('数值对不上时如实报异常，而不是照单全收', () => {
    const broken: Settlement = {
      ...settlement,
      total_revenue: settlement.total_revenue + 1,
    };
    expect(settlementConsistent(broken)).toBe(false);
    expect(settlementSummary(broken)).toContain('异常');
  });

  it('没卖掉的东西必须被说出来', () => {
    const withLeftover: Settlement = {
      ...settlement,
      lines: settlement.lines.map((l, i) => (i === 0 ? { ...l, leftover: 3 } : l)),
    };
    expect(settlementSummary(withLeftover)).toContain('没卖掉');
  });
});

describe('lifeApi · 展示派生', () => {
  it('分钟补零，负数显示未知', () => {
    expect(formatMinute(0)).toBe('00:00');
    expect(formatMinute(549)).toBe('09:09');
    expect(formatMinute(-1)).toBe('--:--');
  });

  it('背包按数量降序、同量按 id 稳定排序', () => {
    const rows = sortedBag({ b: 2, a: 2, c: 5, d: 0 });
    expect(rows.map((r) => r.id)).toEqual(['c', 'a', 'b']);
  });

  it('空背包不报错', () => {
    expect(sortedBag({})).toEqual([]);
  });

  it('经营行价格均为正（不允许 0 价白送）', () => {
    for (const row of shopRows) {
      expect(row.buy_price).toBeGreaterThan(0);
      expect(row.fair_price).toBeGreaterThan(0);
      expect(row.ask_price).toBeGreaterThan(0);
    }
  });
});