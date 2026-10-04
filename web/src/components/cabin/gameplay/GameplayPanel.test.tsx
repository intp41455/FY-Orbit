import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiError, NetworkError } from '../../../api/client';
import { GameplayPanel } from './GameplayPanel';
import type { GameplayMeta } from './cabinGameplayApi';
import type { CabinSaveView } from './balance';

/**
 * W2 · GameplayPanel 接线测试
 *
 * 只测**编排与诚实底线**，不重复测子组件渲染：
 *   1. 服务端权威：动作成功后用返回的存档覆盖本地 state，
 *      而不是前端自己加金币/材料；
 *   2. 动作失败（409 冷却 / 422 未达成 / 网络断）必须显示真实原因，
 *      且明确「本次操作未生效」；
 *   3. 存档加载失败时**不渲染任何假面板**。
 */

const fetchSave = vi.fn();
const fetchGameplayMeta = vi.fn();
const postAction = vi.fn();

vi.mock('./cabinGameplayApi', async () => {
  const actual = await vi.importActual<typeof import('./cabinGameplayApi')>('./cabinGameplayApi');
  return {
    ...actual,
    fetchSave: (...a: unknown[]) => fetchSave(...a),
    fetchGameplayMeta: (...a: unknown[]) => fetchGameplayMeta(...a),
    postAction: (...a: unknown[]) => postAction(...a),
  };
});

function makeSave(over: Partial<CabinSaveView> = {}): CabinSaveView {
  return {
    owner_scoped: true,
    version: 1,
    coins: 10,
    intimacy: 5,
    house_level: 1,
    max_house_level: 5,
    materials: { pinecone: 1 },
    material_catalog: [{ id: 'pinecone', label: '松果', theme: 'forest', tier: 1 }],
    spots: [
      {
        id: 'forest_pine', theme: 'forest', label: '老松树', material: 'pinecone',
        material_label: '松果', qty: 1, coins: 8, intimacy: 2, cooldown_hours: 2,
        fx: 0.3, fy: 0.2, available: true, ready_at: null, remaining_seconds: 0,
        collect_count: 0, dust: false,
      },
    ],
    quests: {
      tutorial: { step: 0, progress: {}, claimed: [], completed: false, baseline_items: 0 },
      daily: { date: '2026-10-04', ids: [], progress: {}, claimed: [] },
      wish: null,
    },
    companion: { behavior: 'sleep', label: '在窝里睡着了', away_hours: 0, trinkets: [], unlocked_count: 2 },
    unlocked_furniture: [],
    chest_keys: 0,
    login_streak: 1,
    dust: [],
    offline: null,
    daily_rotated: false,
    level_up: null,
    preferences: {
      personality: 'grumpy', recognized: true,
      person: { food: 'mushroom', food_label: '蘑菇', interaction: 'a', touch: 'b', note: '会开心' },
      pet: { food: 'fish', food_label: '鱼', interaction: 'a', touch: 'b', note: '会摇尾巴' },
    },
    server_time: '2026-10-04T02:00:00+00:00',
    local_date: '2026-10-04',
    settings: {},
    ...over,
  };
}

function makeMeta(): GameplayMeta {
  return {
    themes: ['forest', 'garden', 'stream', 'field', 'planet'],
    materials: [{ id: 'pinecone', label: '松果', theme: 'forest', tier: 1 }],
    spots: [],
    blueprints: { herb_shelf: { label: '草药架', cost: { wood: 3 }, unlock_level: 1 } },
    events: {},
    tutorial_steps: [
      { id: 't1', title: '采集一次', desc: '点一个发光热点', event: 'explore', target: 1, reward_label: '种子×2' },
    ],
    daily_pool: [],
    limits: {
      max_material_qty: 99, max_coins: 1e9, max_intimacy: 100, max_house_level: 5,
      offline_cap_hours: 24, level_thresholds: [3, 7, 11, 16],
    },
    actions: ['explore', 'feed', 'water', 'clean', 'claim', 'craft'],
  };
}

function renderPanel() {
  return render(
    <GameplayPanel personality="grumpy" personName="阿元" furnitureCount={0} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchSave.mockResolvedValue(makeSave());
  fetchGameplayMeta.mockResolvedValue(makeMeta());
  postAction.mockResolvedValue({ action: 'explore' });
});

describe('GameplayPanel · 加载', () => {
  it('挂载时并行拉 meta 与 save，并把身份透传给后端', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('w2-gameplay')).toBeTruthy());
    expect(fetchGameplayMeta).toHaveBeenCalledTimes(1);
    expect(fetchSave).toHaveBeenCalledWith({
      theme: 'forest',
      personality: 'grumpy',
      person_name: '阿元',
    });
  });

  it('存档加载中显示加载态', async () => {
    fetchSave.mockReturnValue(new Promise(() => {}));
    renderPanel();
    expect(await screen.findByTestId('w2-save-loading')).toBeTruthy();
  });

  it('存档读取失败时显示真实原因，且不渲染玩法面板', async () => {
    fetchSave.mockRejectedValue(new ApiError(500, { code: 'boom', message: '数据库炸了' }, 'fallback'));
    renderPanel();
    const err = await screen.findByTestId('w2-save-error');
    expect(err).toHaveTextContent('数据库炸了');
    expect(err).toHaveTextContent('boom');
    expect(screen.queryByTestId('w2-status-bar')).toBeNull();
    expect(screen.queryByTestId('w2-spots-panel')).toBeNull();
  });

  it('网络失败如实标注 offline', async () => {
    fetchSave.mockRejectedValue(new NetworkError('offline', '连不上服务器'));
    renderPanel();
    expect(await screen.findByTestId('w2-save-error')).toHaveTextContent('网络不可用');
  });

  it('meta 失败不影响存档面板，只让任务/制造不可用', async () => {
    fetchGameplayMeta.mockRejectedValue(new ApiError(500, null, 'meta 挂了'));
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('w2-meta-error')).toBeTruthy());
    expect(screen.getByTestId('w2-status-bar')).toBeTruthy();
    expect(screen.getByTestId('w2-spots-panel')).toBeTruthy();
  });
});

describe('GameplayPanel · 服务端权威', () => {
  it('动作成功后用服务端返回的存档覆盖本地数值', async () => {
    // 服务端结算后：金币 10 → 45，材料 pinecone 1 → 4
    postAction.mockResolvedValue({
      action: 'explore',
      version: 2,
      coins: 45,
      materials: { pinecone: 4 },
      event: { id: 'cat_gift', text: '猫叼来一条鱼', items: { fish: 1 }, coins: 5 },
      feedback: '收获 松果 ×1',
    });
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('w2-stat-coins')).toHaveTextContent('10'));

    fireEvent.click(await screen.findByTestId('w2-spot-forest_pine-explore'));

    await waitFor(() => expect(screen.getByTestId('w2-stat-coins')).toHaveTextContent('45'));
    // 材料来自服务端，不靠前端累加
    expect(screen.getByTestId('w2-mat-pinecone')).toHaveTextContent('×4');
  });

  it('动作带上性格与名字（否则拿不到个性化文案）', async () => {
    renderPanel();
    await waitFor(() => expect(fetchSave).toHaveBeenCalled());
    fireEvent.click(await screen.findByTestId('w2-feed'));
    await waitFor(() =>
      expect(postAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'feed', personality: 'grumpy', person_name: '阿元' }),
      ),
    );
  });

  it('动作回执不完整时重新拉存档，不展示过期数值', async () => {
    // 只回了 action，没有 version/materials → 必须兜底重拉
    postAction.mockResolvedValue({ action: 'feed' });
    renderPanel();
    await waitFor(() => expect(fetchSave).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByTestId('w2-feed'));
    await waitFor(() => expect(fetchSave).toHaveBeenCalledTimes(2));
  });

  it('成功动作后展示服务端事件卡与反馈', async () => {
    postAction.mockResolvedValue({
      action: 'explore',
      version: 2,
      coins: 15,
      materials: { pinecone: 1 },
      event: { id: 'cat_gift', text: '猫叼来一条鱼放在脚边。', items: { fish: 1 } },
      feedback: '收获 松果 ×1',
    });
    renderPanel();
    fireEvent.click(await screen.findByTestId('w2-spot-forest_pine-explore'));
    expect(await screen.findByTestId('w2-event-card')).toHaveTextContent('猫叼来一条鱼');
    expect(screen.getByTestId('w2-event-feedback')).toHaveTextContent('收获 松果 ×1');
  });
});

describe('GameplayPanel · 诚实错误', () => {
  it('409 冷却冲突显示后端原文且明确「未生效」', async () => {
    postAction.mockRejectedValue(
      new ApiError(409, { code: 'cabin_spot_not_ready', message: '老松树 refreshes in 7199s' }, 'conflict'),
    );
    renderPanel();
    fireEvent.click(await screen.findByTestId('w2-spot-forest_pine-explore'));
    const err = await screen.findByTestId('w2-action-error');
    expect(err).toHaveTextContent('本次操作未生效');
    expect(err).toHaveTextContent('老松树 refreshes in 7199s');
    expect(err).toHaveTextContent('cabin_spot_not_ready');
  });

  it('422 未达成任务时如实显示，不假装领奖成功', async () => {
    postAction.mockRejectedValue(
      new ApiError(422, { code: 'cabin_quest_incomplete', message: 'Step t1 needs 1 progress, has 0' }, 'v'),
    );
    renderPanel();
    fireEvent.click(await screen.findByTestId('w2-feed'));
    expect(await screen.findByTestId('w2-action-error')).toHaveTextContent('cabin_quest_incomplete');
  });

  it('动作失败后金币不变（前端没有偷偷加钱）', async () => {
    postAction.mockRejectedValue(new ApiError(500, null, '服务端异常'));
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('w2-stat-coins')).toHaveTextContent('10'));
    fireEvent.click(await screen.findByTestId('w2-feed'));
    await screen.findByTestId('w2-action-error');
    expect(screen.getByTestId('w2-stat-coins')).toHaveTextContent('10');
  });
});

describe('GameplayPanel · 主题切换与解锁', () => {
  it('切换主题会带新 theme 重新拉存档', async () => {
    renderPanel();
    await waitFor(() => expect(fetchSave).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('w2-theme-garden'));
    await waitFor(() => expect(fetchSave).toHaveBeenCalledTimes(2));
    expect(fetchSave).toHaveBeenLastCalledWith(
      expect.objectContaining({ theme: 'garden' }),
    );
  });

  it('未解锁的 quest/craft 家具标注原因（不静默灰显）', async () => {
    renderPanel();
    const row = await screen.findByTestId('w2-unlock-crystal_tree');
    expect(row).toHaveAttribute('data-unlocked', 'false');
    expect(row).toHaveTextContent('尚未解锁');
  });

  it('服务端下发解锁白名单后如实标为已解锁', async () => {
    fetchSave.mockResolvedValue(makeSave({ unlocked_furniture: ['crystal_tree'] }));
    renderPanel();
    const row = await screen.findByTestId('w2-unlock-crystal_tree');
    expect(row).toHaveAttribute('data-unlocked', 'true');
    expect(row).toHaveTextContent('可在布置模式摆放');
  });

  it('小屋升级由服务端告知时给出提示', async () => {
    fetchSave.mockResolvedValue(makeSave({ level_up: { from: 1, to: 2 } }));
    renderPanel();
    expect(await screen.findByTestId('w2-unlock-note')).toHaveTextContent('Lv2');
  });
});

describe('GameplayPanel · 离线与照料', () => {
  it('有离线结算时显示离线条', async () => {
    fetchSave.mockResolvedValue(
      makeSave({
        offline: {
          away_hours: 24, real_away_hours: 51, capped: true,
          refreshed_spots: ['老松树'], text: '离线 24.0 小时（已按 24 小时上限结算）',
        },
      }),
    );
    renderPanel();
    expect(await screen.findByTestId('w2-offline-bar')).toBeTruthy();
    expect(screen.getByTestId('w2-offline-capped')).toHaveTextContent('24 小时上限');
  });

  it('可以关闭离线提示', async () => {
    fetchSave.mockResolvedValue(
      makeSave({
        offline: {
          away_hours: 2, real_away_hours: 2, capped: false,
          refreshed_spots: [], text: '离线 2.0 小时',
        },
      }),
    );
    renderPanel();
    fireEvent.click(await screen.findByTestId('w2-offline-close'));
    await waitFor(() => expect(screen.queryByTestId('w2-offline-bar')).toBeNull());
  });

  it('展示宠物自主行为', async () => {
    fetchSave.mockResolvedValue(
      makeSave({
        companion: { behavior: 'window', label: '趴在窗边发呆', away_hours: 3, trinkets: ['鹅卵石'], unlocked_count: 3 },
      }),
    );
    renderPanel();
    const bar = await screen.findByTestId('w2-companion');
    expect(bar).toHaveAttribute('data-behavior', 'window');
    expect(bar).toHaveTextContent('离开了 3.0 小时');
  });
});
