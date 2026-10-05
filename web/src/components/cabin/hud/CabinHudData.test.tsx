import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CabinHud } from './CabinHud';
import type { LifeSnapshot } from '../gameplay/lifeApi';
import fixture from '../gameplay/__fixtures__/lifeSave.sample.json';

function createMockSnapshot(overrides?: Partial<LifeSnapshot>): LifeSnapshot {
  return {
    save: {
      ...(fixture.save as any),
      bag: { wood: 10, stone: 5 },
      coins: 500,
      quest_log: {
        day: 1,
        entries: [{ quest_id: 'q_intro', progress: 1, done: false, claimed: false }],
      },
    },
    hud_line: '第1天 上午 晴 · 500 金币',
    npcs: [
      {
        id: 'npc_eldrin',
        name: '艾尔',
        role: '森林守护者',
        place: '林间小道',
        activity: '巡逻',
        awake: true,
        hearts: 3,
        hearts_display: '♥♥♥♡♡♡♡♡♡♡',
        marker: '!',
      },
    ],
    shop: fixture.shop_rows as any,
    craft: [
      {
        id: 'wood_chair',
        label: '原木椅',
        category: 'furniture',
        unlocked: true,
        required_level: 1,
        current_level: 1,
        need_level: null,
      },
    ],
    gather: fixture.gather_rows as any,
    version: 1,
    ...overrides,
  };
}

describe('2b · CabinHud 真实数据对接与流通测试 (判据 U1, U2, U2b, U3, U4, U6)', () => {
  it('U1/U2: 背包面板 —— 数据变动响应测试（A 变到 B 且非空/空态如实显示）', () => {
    const snapA = createMockSnapshot({
      save: {
        ...(fixture.save as any),
        bag: { pine_wood: 12, magic_ore: 4 },
      },
    });

    const { rerender } = render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={snapA.save.coins}
        dayText={snapA.hud_line}
        snapshot={snapA}
      />,
    );

    // 打开背包
    fireEvent.click(screen.getByTestId('hud-btn-backpack'));
    expect(screen.getByTestId('pixel-modal-backpack')).toBeInTheDocument();
    expect(screen.getByTestId('bag-item-pine_wood').textContent).toContain('pine_wood ×12');
    expect(screen.getByTestId('bag-item-magic_ore').textContent).toContain('magic_ore ×4');
    expect(screen.queryByTestId('bag-item-diamond')).not.toBeInTheDocument();

    // 变更为数据 B
    const snapB = createMockSnapshot({
      save: {
        ...(fixture.save as any),
        bag: { diamond: 99 },
      },
    });
    rerender(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={snapB.save.coins}
        dayText={snapB.hud_line}
        snapshot={snapB}
      />,
    );

    expect(screen.getByTestId('bag-item-diamond').textContent).toContain('diamond ×99');
    expect(screen.queryByTestId('bag-item-pine_wood')).not.toBeInTheDocument();

    // 空包状态
    const snapEmpty = createMockSnapshot({
      save: {
        ...(fixture.save as any),
        bag: {},
      },
    });
    rerender(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={snapEmpty.save.coins}
        dayText={snapEmpty.hud_line}
        snapshot={snapEmpty}
      />,
    );
    expect(screen.getByTestId('pixel-empty-backpack')).toBeInTheDocument();
  });

  it('U1/U2: 制作台面板 —— 配方状态与制作动作触发', () => {
    const onAction = vi.fn();
    const snap = createMockSnapshot({
      craft: [
        {
          id: 'bench',
          label: '木长椅',
          category: 'table_chair',
          unlocked: true,
          required_level: 1,
          current_level: 1,
          need_level: null,
        },
        {
          id: 'crystal_lamp',
          label: '水晶吊灯',
          category: 'lamp',
          unlocked: false,
          required_level: 5,
          current_level: 2,
          need_level: 5,
        },
      ],
    });

    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={100}
        dayText="第1天 上午 晴"
        snapshot={snap}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByTestId('hud-btn-craft'));
    expect(screen.getByTestId('pixel-modal-craft')).toBeInTheDocument();

    // 已解锁配方可制作
    const benchItem = screen.getByTestId('craft-item-bench');
    expect(benchItem.textContent).toContain('木长椅');
    const makeBtn = benchItem.querySelector('button')!;
    expect(makeBtn.disabled).toBe(false);
    fireEvent.click(makeBtn);
    expect(onAction).toHaveBeenCalledWith('craft', { recipe_id: 'bench' });

    // 未解锁配方禁用并提示需要等级
    const lampItem = screen.getByTestId('craft-item-crystal_lamp');
    expect(lampItem.textContent).toContain('需要技能 Lv5');
    const lockedBtn = lampItem.querySelector('button')!;
    expect(lockedBtn.disabled).toBe(true);
  });

  it('U1/U2: 任务日志面板 —— 任务列表展示与领奖动作触发', () => {
    const onAction = vi.fn();
    const snap = createMockSnapshot({
      save: {
        ...(fixture.save as any),
        quest_log: {
          day: 2,
          entries: [
            { quest_id: 'gather_herb', progress: 3, done: true, claimed: false },
            { quest_id: 'build_cabin', progress: 1, done: false, claimed: false },
          ],
        },
      },
    });

    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={100}
        dayText="第2天 下午 晴"
        snapshot={snap}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByTestId('hud-btn-quest'));
    expect(screen.getByTestId('pixel-modal-quest')).toBeInTheDocument();

    expect(screen.getByTestId('quest-item-gather_herb').textContent).toContain('（已完成）');
    expect(screen.getByTestId('quest-item-build_cabin').textContent).toContain('（进行中）');

    // 领奖动作
    const claimBtn = screen.getByTestId('quest-item-gather_herb').querySelector('button')!;
    expect(claimBtn).toBeInTheDocument();
    fireEvent.click(claimBtn);
    expect(onAction).toHaveBeenCalledWith('claim_quest', { quest_id: 'gather_herb' });
  });

  it('U1/U2: NPC 社交面板 —— 心数展示与赠礼动作触发', () => {
    const onAction = vi.fn();
    const snap = createMockSnapshot({
      npcs: [
        {
          id: 'npc_flora',
          name: '芙洛拉',
          role: '花店主人',
          place: '花坊',
          activity: '浇花',
          awake: true,
          hearts: 5,
          hearts_display: '♥♥♥♥♥♡♡♡♡♡',
          marker: '',
        },
      ],
    });

    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={100}
        dayText="第1天 上午 晴"
        snapshot={snap}
        onAction={onAction}
      />,
    );

    fireEvent.click(screen.getByTestId('hud-btn-npc'));
    expect(screen.getByTestId('pixel-modal-npc')).toBeInTheDocument();

    const npcCard = screen.getByTestId('npc-item-npc_flora');
    expect(npcCard.textContent).toContain('芙洛拉 · 花店主人');
    expect(npcCard.textContent).toContain('♥♥♥♥♥♡♡♡♡♡');

    const giftBtn = npcCard.querySelector('button')!;
    fireEvent.click(giftBtn);
    expect(onAction).toHaveBeenCalledWith('gift', { npc_id: 'npc_flora' });
  });

  it('U2: 顶部状态栏真金币与时间文案动态变动响应', () => {
    const { rerender } = render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={350}
        dayText="第2天 下午 晴"
      />,
    );

    expect(screen.getByTestId('pixel-coins').textContent).toContain('350');
    expect(screen.getByTestId('pixel-time-weather').textContent).toContain('第2天 下午 晴');

    rerender(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={8888}
        dayText="第10天 夜晚 雨"
      />,
    );

    expect(screen.getByTestId('pixel-coins').textContent).toContain('8,888');
    expect(screen.getByTestId('pixel-time-weather').textContent).toContain('第10天 夜晚 雨');
  });

  it('U6: 错误态明确展示在弹窗中，不许静默空白冒充正常', () => {
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={0}
        dayText="第1天 上午 晴"
        error="网络超时，无法连接数码小屋服务器"
      />,
    );

    fireEvent.click(screen.getByTestId('hud-btn-backpack'));
    const alertEl = screen.getByRole('alert');
    expect(alertEl).toBeInTheDocument();
    expect(alertEl.textContent).toContain('网络超时，无法连接数码小屋服务器');
  });

  it('U4: 全量保留 A6 样式类与 data-testid 结构', () => {
    render(
      <CabinHud
        view="outdoor"
        editMode={false}
        onToggleDecorate={vi.fn()}
        onNavigateBack={vi.fn()}
        coins={100}
        dayText="第1天 上午 晴"
      />,
    );

    expect(screen.getByTestId('cabin-hud-root')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-top-bar')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-back-btn')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-time-weather')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-coins')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-settings-btn')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-minimap')).toBeInTheDocument();
    expect(screen.getByTestId('pixel-bottom-bar')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-backpack')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-craft')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-quest')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-npc')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-decorate')).toBeInTheDocument();
    expect(screen.getByTestId('hud-btn-map')).toBeInTheDocument();
  });
});
