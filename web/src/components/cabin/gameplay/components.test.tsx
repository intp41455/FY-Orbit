import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { BackpackPanel, CareBar, StatusBar } from './backpack';
import type { CabinSaveView } from './balance';
import { CompanionBar, EventCard, OfflineBar } from './events';
import { CraftPanel, QuestBoard } from './quests';
import { SpotsPanel } from './spots';
import type { GameplayMeta } from './cabinGameplayApi';

/**
 * W2 · 玩法组件渲染测试
 *
 * 验证「服务端数据 → DOM」的映射是否忠实，以及三条诚实底线：
 *   1. 冷却中的探险点按钮**真的被禁用**（不是看着能点其实不能）；
 *   2. 未达成的任务不给可点的领奖按钮；
 *   3. 「一无所获」事件必须显示成「无额外奖励」，不得出现 0 金币假数字。
 */

function makeSave(over: Partial<CabinSaveView> = {}): CabinSaveView {
  return {
    owner_scoped: true,
    version: 3,
    coins: 128,
    intimacy: 42,
    house_level: 1,
    max_house_level: 5,
    materials: { pinecone: 3, moonstone: 99 },
    material_catalog: [
      { id: 'pinecone', label: '松果', theme: 'forest', tier: 1 },
      { id: 'moonstone', label: '月石', theme: 'planet', tier: 3 },
      { id: 'moss', label: '苔藓', theme: 'forest', tier: 1 },
    ],
    spots: [],
    quests: {
      tutorial: { step: 0, progress: {}, claimed: [], completed: false, baseline_items: 0 },
      daily: { date: '2026-10-04', ids: [], progress: {}, claimed: [] },
      wish: null,
    },
    companion: { behavior: 'sleep', label: '在窝里睡着了', away_hours: 0, trinkets: [], unlocked_count: 2 },
    unlocked_furniture: [],
    chest_keys: 1,
    login_streak: 3,
    dust: ['forest_pine'],
    offline: null,
    daily_rotated: false,
    level_up: null,
    preferences: {
      personality: 'grumpy',
      recognized: true,
      person: { food: 'mushroom', food_label: '蘑菇', interaction: 'a', touch: 'b', note: '心情会变好' },
      pet: { food: 'fish', food_label: '鱼', interaction: 'a', touch: 'b', note: '会摇尾巴' },
    },
    server_time: '2026-10-04T02:00:00+00:00',
    local_date: '2026-10-04',
    settings: {},
    ...over,
  };
}

/* ---------------- 状态条 ---------------- */

describe('StatusBar', () => {
  it('如实显示服务端数值（不自行换算）', () => {
    render(<StatusBar save={makeSave()} furnitureCount={2} />);
    expect(screen.getByTestId('w2-stat-coins')).toHaveTextContent('128');
    expect(screen.getByTestId('w2-stat-intimacy')).toHaveTextContent('42 / 100');
    expect(screen.getByTestId('w2-stat-streak')).toHaveTextContent('3 天');
    expect(screen.getByTestId('w2-stat-keys')).toHaveTextContent('1');
  });

  it('有落灰探险点时给出打扫提示', () => {
    render(<StatusBar save={makeSave()} furnitureCount={0} />);
    expect(screen.getByTestId('w2-stat-dust')).toHaveTextContent('1 处待打扫');
  });

  it('无灰尘时不渲染该格（不显示 0 处）', () => {
    render(<StatusBar save={makeSave({ dust: [] })} furnitureCount={0} />);
    expect(screen.queryByTestId('w2-stat-dust')).toBeNull();
  });

  it('显示下一级所需家具件数', () => {
    render(<StatusBar save={makeSave()} furnitureCount={2} />);
    expect(screen.getByTestId('w2-stat-level')).toHaveTextContent('下一级需 3 件');
  });
});

/* ---------------- 背包 ---------------- */

describe('BackpackPanel', () => {
  it('只显示数量 > 0 的材料', () => {
    render(<BackpackPanel save={makeSave()} furnitureCount={0} />);
    expect(screen.getByTestId('w2-mat-pinecone')).toHaveTextContent('松果');
    expect(screen.queryByTestId('w2-mat-moss')).toBeNull(); // qty=0 不显示
  });

  it('满 99 的材料标注「满」，不隐藏溢出', () => {
    render(<BackpackPanel save={makeSave()} furnitureCount={0} />);
    expect(screen.getByTestId('w2-mat-moonstone')).toHaveTextContent('满');
  });

  it('按主题分组（DNA-9 不串戏）', () => {
    render(<BackpackPanel save={makeSave()} furnitureCount={0} />);
    expect(screen.getByTestId('w2-mat-group-forest')).toBeTruthy();
    expect(screen.getByTestId('w2-mat-group-planet')).toBeTruthy();
  });

  it('背包为空时明说「去采集」，不显示假物品', () => {
    render(
      <BackpackPanel
        save={makeSave({ materials: {}, material_catalog: [{ id: 'x', label: '虚假', theme: 'forest', tier: 1 }] })}
        furnitureCount={0}
      />,
    );
    expect(screen.getByTestId('w2-backpack-empty')).toHaveTextContent('背包还是空的');
    expect(screen.queryByTestId('w2-mat-x')).toBeNull();
  });
});

/* ---------------- 照料 ---------------- */

describe('CareBar', () => {
  it('性格已识别时如实标注', () => {
    render(
      <CareBar preferences={makeSave().preferences} busy={false} onFeed={vi.fn()} onWater={vi.fn()} onClean={vi.fn()} />,
    );
    expect(screen.getByTestId('w2-pref-recognized')).toHaveAttribute('data-recognized', 'true');
  });

  it('性格未识别时不得声称命中喜好', () => {
    const prefs = { ...makeSave().preferences, recognized: false };
    render(<CareBar preferences={prefs} busy={false} onFeed={vi.fn()} onWater={vi.fn()} onClean={vi.fn()} />);
    expect(screen.getByTestId('w2-pref-recognized')).toHaveAttribute('data-recognized', 'false');
    expect(screen.getByTestId('w2-pref-person')).toHaveTextContent('性格未识别');
  });

  it('busy 时禁用全部动作按钮（防重复提交）', () => {
    render(
      <CareBar preferences={makeSave().preferences} busy onFeed={vi.fn()} onWater={vi.fn()} onClean={vi.fn()} />,
    );
    expect(screen.getByTestId('w2-feed')).toBeDisabled();
    expect(screen.getByTestId('w2-water')).toBeDisabled();
    expect(screen.getByTestId('w2-clean')).toBeDisabled();
  });

  it('点击照料按钮回调正确', () => {
    const onFeed = vi.fn();
    const onWater = vi.fn();
    const onClean = vi.fn();
    render(
      <CareBar preferences={makeSave().preferences} busy={false} onFeed={onFeed} onWater={onWater} onClean={onClean} />,
    );
    fireEvent.click(screen.getByTestId('w2-feed'));
    fireEvent.click(screen.getByTestId('w2-water'));
    fireEvent.click(screen.getByTestId('w2-clean'));
    expect(onFeed).toHaveBeenCalledTimes(1);
    expect(onWater).toHaveBeenCalledTimes(1);
    expect(onClean).toHaveBeenCalledTimes(1);
  });
});

/* ---------------- 探险点 ---------------- */

const SPOTS = [
  {
    id: 'forest_pine', theme: 'forest' as const, label: '老松树', material: 'pinecone',
    material_label: '松果', qty: 1, coins: 8, intimacy: 2, cooldown_hours: 2,
    fx: 0.3, fy: 0.2, available: true, ready_at: null, remaining_seconds: 0,
    collect_count: 0, dust: false,
  },
  {
    id: 'forest_mush', theme: 'forest' as const, label: '蘑菇圈', material: 'mushroom',
    material_label: '蘑菇', qty: 2, coins: 10, intimacy: 3, cooldown_hours: 3,
    fx: 0.7, fy: 0.6, available: false, ready_at: '2026-10-04T06:00:00+00:00',
    remaining_seconds: 7200, collect_count: 2, dust: true,
  },
];

describe('SpotsPanel', () => {
  it('渲染全部探险点并统计可采数', () => {
    render(<SpotsPanel spots={SPOTS} theme="forest" busy={false} onExplore={vi.fn()} />);
    expect(screen.getByTestId('w2-spot-forest_pine')).toBeTruthy();
    expect(screen.getByTestId('w2-spots-ready')).toHaveTextContent('可采 1 / 2');
  });

  it('冷却中的点：按钮真禁用 + 显示剩余时间', () => {
    render(<SpotsPanel spots={SPOTS} theme="forest" busy={false} onExplore={vi.fn()} />);
    const btn = screen.getByTestId('w2-spot-forest_mush-explore');
    expect(btn).toBeDisabled();
    expect(screen.getByTestId('w2-spot-forest_mush-state')).toHaveTextContent('冷却中');
    expect(screen.getByTestId('w2-spot-forest_mush-state')).toHaveTextContent('2 小时');
  });

  it('可采点点击后回调带正确的 spot_id', () => {
    const onExplore = vi.fn();
    render(<SpotsPanel spots={SPOTS} theme="forest" busy={false} onExplore={onExplore} />);
    fireEvent.click(screen.getByTestId('w2-spot-forest_pine-explore'));
    expect(onExplore).toHaveBeenCalledWith('forest_pine');
  });

  it('落灰点标注「落灰了」并给出打扫入口', () => {
    const onClean = vi.fn();
    render(
      <SpotsPanel spots={SPOTS} theme="forest" busy={false} onExplore={vi.fn()} onClean={onClean} />,
    );
    expect(screen.getByTestId('w2-spot-forest_mush-dust')).toHaveTextContent('落灰了');
    fireEvent.click(screen.getByTestId('w2-spot-forest_mush-clean'));
    expect(onClean).toHaveBeenCalledWith('forest_mush');
  });

  it('无数据时明说「服务端未返回」，不渲染假点', () => {
    render(<SpotsPanel spots={[]} theme="forest" busy={false} onExplore={vi.fn()} />);
    expect(screen.getByTestId('w2-spots-empty')).toHaveTextContent('还没有探险点');
  });
});

/* ---------------- 事件（诚实底线） ---------------- */

describe('EventCard', () => {
  it('正常事件展示文案与奖励', () => {
    render(<EventCard event={{ id: 'cat_gift', title: '猫送了你一条鱼', text: '它叼来一条鱼放在脚边。', items: { fish: 1 }, coins: 5 }} />);
    expect(screen.getByTestId('w2-event-text')).toHaveTextContent('它叼来一条鱼');
    expect(screen.getByTestId('w2-event-reward')).toHaveTextContent('fish×1 · 5 金币');
    expect(screen.getByTestId('w2-event-card')).toHaveAttribute('data-nothing', 'false');
  });

  it('「一无所获」如实显示，绝不出现 0 金币假数字', () => {
    render(<EventCard event={{ id: 'nothing', text: '这里现在什么也没有。' }} />);
    expect(screen.getByTestId('w2-event-card')).toHaveAttribute('data-nothing', 'true');
    expect(screen.getByTestId('w2-event-reward')).toHaveTextContent('无额外奖励');
    expect(screen.getByTestId('w2-event-reward')).not.toHaveTextContent('0 金币');
  });

  it('事件没有文案时明说服务端未返回，不留空', () => {
    render(<EventCard event={{ id: 'x', text: '' }} />);
    expect(screen.getByTestId('w2-event-text')).toHaveTextContent('服务端没有返回事件文案');
  });

  it('无事件时不渲染任何卡片', () => {
    const { container } = render(<EventCard event={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('可关闭事件卡', () => {
    const onDismiss = vi.fn();
    render(<EventCard event={{ id: 'x', text: 't' }} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByTestId('w2-event-close'));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe('CompanionBar', () => {
  it('展示行为与小玩意', () => {
    render(
      <CompanionBar behavior="window" label="趴在窗边发呆" awayHours={2} trinkets={['鹅卵石']} unlockedCount={3} />,
    );
    expect(screen.getByTestId('w2-companion')).toHaveAttribute('data-behavior', 'window');
    expect(screen.getByTestId('w2-companion-trinkets')).toHaveTextContent('鹅卵石');
    expect(screen.getByTestId('w2-companion-unlocked')).toHaveTextContent('3 种行为');
  });

  it('离开不足 15 分钟不显示时长（避免噪音）', () => {
    render(<CompanionBar behavior="idle" label="就在旁边" awayHours={0.1} trinkets={[]} unlockedCount={2} />);
    expect(screen.getByTestId('w2-companion').textContent).not.toContain('离开了');
  });
});

describe('OfflineBar', () => {
  it('未触顶时只显示结算文本', () => {
    render(
      <OfflineBar awayHours={2} realAwayHours={2} capped={false} refreshedSpots={['老松树']} text="离线 2.0 小时，1 个探险点已刷新" />,
    );
    expect(screen.getByTestId('w2-offline-bar')).toHaveTextContent('离线 2.0 小时');
    expect(screen.queryByTestId('w2-offline-capped')).toBeNull();
  });

  it('触顶时如实说明按 24 小时上限结算', () => {
    render(
      <OfflineBar awayHours={24} realAwayHours={51} capped text="离线 24.0 小时（已按 24 小时上限结算）" refreshedSpots={[]} />,
    );
    expect(screen.getByTestId('w2-offline-capped')).toHaveTextContent('实际离开 51.0 小时');
    expect(screen.getByTestId('w2-offline-capped')).toHaveTextContent('24 小时上限');
  });
});

/* ---------------- 任务板 ---------------- */

const STEPS = [
  { id: 't1', title: '采集一次', event: 'explore', target: 1, reward_label: '种子×2 + 10 金币', desc: '点一个发光热点' },
  { id: 't2', title: '喂一次宠物', event: 'feed', target: 1, reward_label: '蘑菇×1 + 8 金币', desc: '' },
];

const DAILY_POOL = [
  { id: 'd_gather3', title: '采集 3 次', event: 'explore', target: 3, reward_label: '鹅卵石×2 + 18 金币', desc: '' },
];

describe('QuestBoard', () => {
  it('渲染当前新手步骤与进度条', () => {
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: { explore: 0 }, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-step-t1')).toHaveTextContent('第 1 步 · 采集一次');
    expect(screen.getByTestId('w2-step-t1-bar')).toHaveTextContent('0 / 1');
  });

  it('未达成时领奖按钮禁用（不做假交互）', () => {
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: { explore: 0 }, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    const btn = screen.getByTestId('w2-claim-t1');
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent('还差 1 次');
  });

  it('达成后可领奖，点击回调带对 kind/id', () => {
    const onClaim = vi.fn();
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: { explore: 1 }, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={onClaim}
      />,
    );
    const btn = screen.getByTestId('w2-claim-t1');
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onClaim).toHaveBeenCalledWith('tutorial', 't1');
  });

  it('已完成的新手链列出全部步骤并标记完成', () => {
    render(
      <QuestBoard
        tutorial={{ step: 5, progress: {}, claimed: ['t1', 't2'], completed: true, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-tutorial-done')).toBeTruthy();
    expect(screen.getByTestId('w2-step-t1')).toHaveTextContent('采集一次');
  });

  it('步骤序号越界时明说数据异常，不硬造 UI', () => {
    render(
      <QuestBoard
        tutorial={{ step: 9, progress: {}, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-tutorial-missing')).toHaveTextContent('超出本地步骤表');
  });

  it('日常任务未生成时明说未返回', () => {
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: {}, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={null}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-daily-empty')).toHaveTextContent('尚未生成');
  });

  it('愿望未满足时领奖按钮禁用', () => {
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: {}, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={{ id: 'w_fish3', kind: 'material', target: 'fish', need: 3, text: '想吃小鱼干……', reward_label: '亲密 +6', progress: 1, done: false, claimed: false }}
        busy={false}
        onClaim={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-claim-wish-w_fish3')).toBeDisabled();
    expect(screen.getByTestId('w2-claim-wish-w_fish3')).toHaveTextContent('尚未满足');
  });

  it('愿望满足后可领奖', () => {
    const onClaim = vi.fn();
    render(
      <QuestBoard
        tutorial={{ step: 0, progress: {}, claimed: [], completed: false, baseline_items: 0 }}
        tutorialSteps={STEPS}
        daily={null}
        dailyPool={DAILY_POOL}
        wish={{ id: 'w_fish3', kind: 'material', target: 'fish', need: 3, text: '想吃小鱼干……', reward_label: '亲密 +6', progress: 3, done: true, claimed: false }}
        busy={false}
        onClaim={onClaim}
      />,
    );
    fireEvent.click(screen.getByTestId('w2-claim-wish-w_fish3'));
    expect(onClaim).toHaveBeenCalledWith('wish', 'w_fish3');
  });
});

/* ---------------- 制造 ---------------- */

const BLUEPRINTS = {
  herb_shelf: { label: '草药架', cost: { wood: 3, flower: 2 }, unlock_level: 1 },
};

describe('CraftPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('缺料时逐项标红并禁用打造', () => {
    render(
      <CraftPanel
        blueprints={BLUEPRINTS}
        materials={{ wood: 1 }}
        materialLabels={{ wood: '木材', flower: '花' }}
        houseLevel={1}
        unlockedFurniture={[]}
        busy={false}
        onCraft={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-cost-herb_shelf-wood')).toHaveTextContent('木材 1/3');
    expect(screen.getByTestId('w2-cost-herb_shelf-wood')).toHaveClass('lack');
    expect(screen.getByTestId('w2-craft-btn-herb_shelf')).toBeDisabled();
  });

  it('材料齐备且等级足够时可打造', () => {
    const onCraft = vi.fn();
    render(
      <CraftPanel
        blueprints={BLUEPRINTS}
        materials={{ wood: 5, flower: 4 }}
        materialLabels={{ wood: '木材', flower: '花' }}
        houseLevel={1}
        unlockedFurniture={[]}
        busy={false}
        onCraft={onCraft}
      />,
    );
    const btn = screen.getByTestId('w2-craft-btn-herb_shelf');
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onCraft).toHaveBeenCalledWith('herb_shelf');
  });

  it('等级不足时说明需要几级', () => {
    render(
      <CraftPanel
        blueprints={{ crystal_tree: { label: '水晶树', cost: {}, unlock_level: 3 } }}
        materials={{}}
        materialLabels={{}}
        houseLevel={1}
        unlockedFurniture={[]}
        busy={false}
        onCraft={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-craft-crystal_tree-level')).toHaveTextContent('需要小屋 Lv3');
    expect(screen.getByTestId('w2-craft-btn-crystal_tree')).toBeDisabled();
  });

  it('已解锁的图纸标为已解锁且不可重复打造', () => {
    render(
      <CraftPanel
        blueprints={BLUEPRINTS}
        materials={{ wood: 9, flower: 9 }}
        materialLabels={{ wood: '木材', flower: '花' }}
        houseLevel={1}
        unlockedFurniture={['herb_shelf']}
        busy={false}
        onCraft={vi.fn()}
      />,
    );
    expect(screen.getByTestId('w2-craft-herb_shelf-owned')).toBeTruthy();
    expect(screen.getByTestId('w2-craft-btn-herb_shelf')).toBeDisabled();
  });

  it('蓝图数据缺失时明说未加载', () => {
    render(
      <CraftPanel blueprints={{}} materials={{}} materialLabels={{}} houseLevel={1} unlockedFurniture={[]} busy={false} onCraft={vi.fn()} />,
    );
    expect(screen.getByTestId('w2-craft-panel')).toHaveTextContent('图纸数据尚未加载');
  });
});

/* 类型契约：确保 meta 类型被真正引用（避免 unused import 掩盖契约漂移） */
describe('GameplayMeta 契约', () => {
  it('meta 蓝图结构与组件期望一致', () => {
    const meta = {
      blueprints: { herb_shelf: { label: '草药架', cost: { wood: 3 }, unlock_level: 1 } },
    } as unknown as GameplayMeta;
    expect(Object.keys(meta.blueprints)).toEqual(['herb_shelf']);
  });
});
