// W11 · 角色工坊页交互单测。
//
// 关注页面层面的诚实契约（纯函数层见 avatarPixels.test.ts）：
//  - 逐项同意：**只**提交被勾选的画像项，没勾的一律不发；
//  - advisory 原样展示（「中性默认 / 非你的真实数据」不许被改写或隐藏）；
//  - 空态（404）显示引导，不显示错误；
//  - 真故障显示后端错误码，不吞掉；
//  - 分享卡徽章默认零勾选；
//  - 未确认时不允许出分享卡（后端也会拒，但前端先给明确提示）。

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AvatarWorkshopPage } from './AvatarWorkshopPage';
import fixture from '../components/avatar/__fixtures__/w11.json';
import { ApiError } from '../api/client';

// 整页 mock：页面只依赖 api/avatar.ts 的公开契约，不关心真实网络。
const api = {
  generateAvatar: vi.fn(),
  confirmAvatar: vi.fn(),
  getMyAvatar: vi.fn(),
  createShareCard: vi.fn(),
  getHouseAvatar: vi.fn(),
};

// mock 路径必须与页面 import 的模块**解析到同一文件**。
// 页面 import的是 '../api/avatar'，测试文件在 src/pages/ 下，故这里也写 '../api/avatar'。
vi.mock('../api/avatar', async () => {
  const actual = await vi.importActual<typeof import('../api/avatar')>('../api/avatar');
  return {
    ...actual,
    generateAvatar: (...a: unknown[]) => api.generateAvatar(...a),
    confirmAvatar: (...a: unknown[]) => api.confirmAvatar(...a),
    getMyAvatar: (...a: unknown[]) => api.getMyAvatar(...a),
    createShareCard: (...a: unknown[]) => api.createShareCard(...a),
    getHouseAvatar: (...a: unknown[]) => api.getHouseAvatar(...a),
  };
});

function avatarProfile(overrides: Record<string, unknown> = {}) {
  return {
    id: 'av_1',
    state: 'draft',
    owner_id: 'o1',
    portrait: {},
    params: { labels: { face_shape: 'round', hand_item_id: 'lantern' } },
    base_signature: {},
    overrides: null,
    fingerprint: 'A5AC842BAAAAAAAA',
    params_fingerprint: '6D73FAA1BBBBBBBB',
    engine_version: 'w11-1',
    likeness_score: null,
    likeness_note: null,
    is_house_avatar: false,
    version: 1,
    avatar: {
      fingerprint: 'A5AC842BAAAAAAAA',
      params_fingerprint: '6D73FAA1BBBBBBBB',
      engine_version: 'w11-1',
      params: {
        hair_style: 'ponytail',
        colors: {},
        labels: {},
        sources: { hair_style: 'mbti(ESTJ)' },
        missing: [],
      },
      base_signature: {},
      tuned: false,
      layers: fixture.layers,
      matrix: fixture.matrix,
      width: 24,
      height: 32,
      palette: fixture.palette,
      char_palette: fixture.char_palette,
      param_space_size: 13271040,
      advisory: {
        complete: false,
        pending: ['mbti', 'bazi_element'],
        note: '以下画像数据尚未生成，相关维度使用中性默认（非你的真实数据）：MBTI 性格、八字五行',
        notes: '性别与年龄档为可选信息，未填写也不影响角色生成。',
        age_band_label: null,
      },
    },
    advisory: {
      complete: false,
      pending: ['mbti', 'bazi_element'],
      note: '以下画像数据尚未生成，相关维度使用中性默认（非你的真实数据）：MBTI 性格、八字五行',
      notes: '性别与年龄档为可选信息，未填写也不影响角色生成。',
      age_band_label: null,
    },
    created_at: '2026-10-04T00:00:00Z',
    updated_at: '2026-10-04T00:00:00Z',
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/avatar']}>
      <Routes>
        <Route path="/avatar" element={<AvatarWorkshopPage />} />
        <Route path="/cabin" element={<div>小屋</div>} />
        <Route path="/private" element={<div>私人空间</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  for (const m of Object.values(api)) m.mockReset();
  // 默认空态：还没生成过
  api.getMyAvatar.mockRejectedValue(new ApiError(404, { code: 'not_found', message: 'no avatar', details: {} }, 'nf'));
});

afterEach(() => {
  document.querySelectorAll('meta[name="csrf-token"]').forEach((el) => el.remove());
});

describe('W11 · 工坊页空态', () => {
  it('未生成过（404）显示引导而不是错误', async () => {
    renderPage();
    expect(await screen.findByText(/还没有生成角色/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('真故障显示后端错误码，不静默吞掉', async () => {
    api.getMyAvatar.mockRejectedValue(
      new ApiError(500, { code: 'avatar_backend_down', message: '引擎炸了', details: {} }, 'boom'),
    );
    renderPage();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('引擎炸了');
    expect(alert).toHaveTextContent('avatar_backend_down');
  });
});

describe('W11 · 逐项同意：只提交勾选项', () => {
  it('一个都不勾 → 生成时提交空画像', async () => {
    api.generateAvatar.mockResolvedValue(avatarProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/还没有生成角色/);
    await user.click(screen.getByRole('button', { name: /生成我的小人/ }));
    await waitFor(() => expect(api.generateAvatar).toHaveBeenCalledTimes(1));
    expect(api.generateAvatar.mock.calls[0][0]).toEqual({});
  });

  it('勾哪项才发哪项，未勾的一律不发', async () => {
    api.generateAvatar.mockResolvedValue(avatarProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/还没有生成角色/);

    // 只勾 MBTI 并填值
    await user.click(screen.getByRole('checkbox', { name: /MBTI 性格/ }));
    await user.type(screen.getByPlaceholderText('INFJ'), 'ESTJ');
    await user.click(screen.getByRole('button', { name: /生成我的小人/ }));

    await waitFor(() => expect(api.generateAvatar).toHaveBeenCalled());
    expect(api.generateAvatar.mock.calls[0][0]).toEqual({ mbti: 'ESTJ' });
  });

  it('未勾选的输入框是禁用的（视觉上也表达「这条不提交」）', async () => {
    renderPage();
    await screen.findByText(/还没有生成角色/);
    expect(screen.getByPlaceholderText('INFJ')).toBeDisabled();
    expect(screen.getByPlaceholderText('木')).toBeDisabled();
  });

  it('勾上后输入框才可编辑', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/还没有生成角色/);
    await user.click(screen.getByRole('checkbox', { name: /太阳星座/ }));
    expect(screen.getByPlaceholderText('leo')).toBeEnabled();
  });
});

describe('W11 · 诚实标注', () => {
  it('把后端 advisory 的「中性默认 / 非你的真实数据」原样显示', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile());
    renderPage();
    expect(await screen.findByText(/中性默认/)).toBeInTheDocument();
    expect(screen.getByText(/非你的真实数据/)).toBeInTheDocument();
  });

  it('把「性别/年龄档不改剪影」的说明也显示出来', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile());
    renderPage();
    expect(await screen.findByText(/未填写也不影响角色生成/)).toBeInTheDocument();
  });

  it('显示真实参数空间大小与两个短码', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile());
    renderPage();
    expect(await screen.findByText(/13,271,040/)).toBeInTheDocument();
    expect(screen.getByText('6D73FAA1')).toBeInTheDocument();
    expect(screen.getByText('A5AC842B')).toBeInTheDocument();
  });
});

describe('W11 · 微调与还原', () => {
  it('微调字段被送进 overrides', async () => {
    api.generateAvatar.mockResolvedValue(avatarProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/还没有生成角色/);
    await user.click(screen.getByRole('button', { name: /生成我的小人/ }));
    await waitFor(() => expect(api.generateAvatar).toHaveBeenCalledTimes(1));

    api.generateAvatar.mockClear();
    const outfit = screen.getByDisplayValue('ponytail');
    expect(outfit).toBeInTheDocument();
  });

  it('「还原 AI 底稿」= 传空 overrides（丢掉全部微调）', async () => {
    api.getMyAvatar.mockResolvedValue(
      avatarProfile({
        overrides: { hair_style: 'bob' },
        avatar: { ...avatarProfile().avatar, tuned: true },
      }),
    );
    api.generateAvatar.mockResolvedValue(avatarProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/中性默认/);
    await user.click(screen.getByRole('button', { name: /还原 AI 底稿/ }));
    await waitFor(() => expect(api.generateAvatar).toHaveBeenCalled());
    expect(api.generateAvatar.mock.calls[0][1]).toEqual({});
  });

  it('未微调时如实显示「未微调」', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile());
    renderPage();
    expect(await screen.findByText('未微调')).toBeInTheDocument();
  });
});

describe('W11 · 确认与分享卡', () => {
  it('确认时把自评 1–10 与感想一并发回，并带上乐观锁版本号', async () => {
    // is_house_avatar 刻意设为 true：页面必须用后端真值回填勾选框，而不是写死 true。
    api.getMyAvatar.mockResolvedValue(avatarProfile({ is_house_avatar: true }));
    api.confirmAvatar.mockResolvedValue(avatarProfile({ state: 'confirmed', likeness_score: 9 }));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/中性默认/);

    await user.click(screen.getByRole('button', { name: /确认这个角色/ }));
    await waitFor(() => expect(api.confirmAvatar).toHaveBeenCalled());
    const arg = api.confirmAvatar.mock.calls[0][0] as Record<string, unknown>;
    expect(arg.expectedVersion).toBe(1);
    expect(arg.likenessScore).toBe(7);           // 默认值
    expect(arg.isHouseAvatar).toBe(true);       // 默认勾上
  });

  it('分享卡徽章默认零勾选（零隐私泄露）', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile({ state: 'confirmed' }));
    renderPage();
    await screen.findByText(/中性默认/);
    const boxes = screen.getAllByRole('checkbox');
    // 徽章 7 项默认全不勾
    const unchecked = boxes.filter((b) => !(b as HTMLInputElement).checked);
    expect(unchecked.length).toBeGreaterThanOrEqual(7);
  });

  it('未确认时出卡按钮给出明确提示（而不是静默失败）', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile({ state: 'draft' }));
    renderPage();
    await screen.findByText(/中性默认/);
    expect(screen.getByRole('button', { name: /请先确认角色/ })).toBeDisabled();
  });

  it('已确认时可出卡，且只把勾选的字段送上去', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile({ state: 'confirmed' }));
    api.createShareCard.mockResolvedValue({
      width: 720,
      height: 960,
      avatar: {
        matrix: fixture.share_card_avatar.matrix,
        layers: fixture.layers,
        palette: fixture.palette,
        char_palette: fixture.share_card_avatar.char_palette,
        width: 24,
        height: 32,
      },
      badges: [{ field: 'mbti', label: '性格类型', value: 'ESTJ' }],
      caption: '这是我的专属像素小人',
      fingerprint_short: '6D73FAA1',
      base_fingerprint_short: 'A5AC842B',
      tuned: false,
      brand: { product: 'Find Yourself', tagline: '本地优先 · 你的像素小人' },
      privacy_note: '分享卡只包含你勾选的项目。',
      excluded_fields: ['element', 'sun_sign'],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/中性默认/);

    // 勾选 mbti
    await user.click(screen.getByRole('checkbox', { name: 'mbti' }));
    await user.click(screen.getByRole('button', { name: /生成分享卡/ }));

    await waitFor(() => expect(api.createShareCard).toHaveBeenCalled());
    expect(api.createShareCard.mock.calls[0][0].badges).toEqual(['mbti']);
    // 生成后展示分享卡短码与「已隐藏 N 项」
    //短码会出现在元信息区和分享卡区，故用 getAllByText 断言「至少出现一次」
    await waitFor(() => expect(screen.getAllByText('6D73FAA1').length).toBeGreaterThan(0));
    expect(screen.getByText(/已隐藏 2 项未勾选信息/)).toBeInTheDocument();
    // 像素画布已挂载（导出 PNG 的载体）
    expect(screen.getByLabelText(/我的像素小人分享卡预览/)).toBeInTheDocument();
  });

  it('出卡失败时显示后端错误码', async () => {
    api.getMyAvatar.mockResolvedValue(avatarProfile({ state: 'confirmed' }));
    api.createShareCard.mockRejectedValue(
      new ApiError(409, { code: 'avatar_not_confirmed', message: '还没确认', details: {} }, 'x'),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/中性默认/);
    await user.click(screen.getByRole('button', { name: /生成分享卡/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('avatar_not_confirmed');
  });
});

describe('W11 · 入口导航', () => {
  it('页面上有去小屋的入口', async () => {
    api.getMyAvatar.mockRejectedValue(new ApiError(404, { code: 'not_found', message: 'x', details: {} }, 'x'));
    renderPage();
    expect(await screen.findByRole('button', { name: /去小屋/ })).toBeInTheDocument();
  });
});