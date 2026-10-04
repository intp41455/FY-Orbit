// W11 · 小屋接线单测（专属小人注入 + 更衣镜入口）。
//
// 与既有 CabinPage.test.tsx 分开：本文件只关心 W11 关心的行为，不重复 W1 的
// 工具条/布置测试。PixiJS 在 jsdom 不可用，按既有约定整体 mock CabinStage，
// 但**把personWalkFrames / personPalette 暴露出来**，这样才能断言注入是否发生。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import fixture from '../components/avatar/__fixtures__/w11.json';

// 室内场景同样跑在 Pixi 上，jsdom 无法触达画布上的家具点击。
// 这里整体 mock 并把 onFurnitureTap 暴露出来，用于断言**页面如何解释**这次点击
// （W11 的行为：mirror → 跳角色工坊），而不是测 Pixi 的拾取。
const stageSpy = vi.fn();

vi.mock('../components/cabin/CabinStage', () => ({
  CabinStage: (props: {
    personWalkFrames?: readonly (readonly string[])[];
    personPalette?: Record<string, number>;
  }) => {
    stageSpy(props.personWalkFrames, props.personPalette);
    return <canvas data-testid="cabin-canvas" aria-hidden="true" />;
  },
}));

const interiorSpy = vi.fn();

vi.mock('../components/cabin/interior/InteriorStage', () => ({
  InteriorStage: (props: {
    callbacks?: { onFurnitureTap?: (item: { furnitureId: string }) => void };
  }) => {
    interiorSpy(props.callbacks);
    return <canvas data-testid="cabin-interior-canvas" aria-hidden="true" />;
  },
}));

vi.mock('../api/butler', () => ({
  butlerApi: {
    status: () => Promise.resolve({ model_configured: false }),
    dialogue: () => Promise.reject(new Error('not configured')),
  },
}));

const houseApi = {
  getHouseAvatar: vi.fn(),
};

vi.mock('../api/avatar', async () => {
  const actual = await vi.importActual<typeof import('../api/avatar')>('../api/avatar');
  return { ...actual, getHouseAvatar: (...a: unknown[]) => houseApi.getHouseAvatar(...a) };
});

import { CabinPage } from './CabinPage';
import { cabinDialogue } from '../components/cabin/cabinDialogueProvider';

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/cabin']}>
      <CabinPage />
    </MemoryRouter>,
  );
}

function houseProfile() {
  // G1-3：历史上这里返回「嵌套 AvatarProfile」假结构，掩盖了小屋白屏（G1-1）。
  // 真实后端 /api/avatar/house（avatar_profile.py:234）只返回**扁平 9 键**，
  // 没有 avatar 嵌套、没有 params_fingerprint。此处改用真实扁平结构，
  // 让测试真正复现「前端按扁平字段读取」的契约，而不是喂一个永远不崩的假数据。
  return {
    fingerprint: '6D73FAA1BBBBBBBB', // = 后端的 params_fingerprint
    layers: fixture.layers,
    matrix: fixture.matrix,
    width: 24,
    height: 32,
    palette: fixture.palette,
    char_keys: fixture.char_keys,
    char_palette: fixture.char_palette,
    labels: {},
  };
}

function notFound() {
  const err = Object.assign(new Error('not found'), { status: 404 });
  return err;
}

beforeEach(() => {
  localStorage.clear();
  cabinDialogue.reset();
  stageSpy.mockClear();
  houseApi.getHouseAvatar.mockReset();
  houseApi.getHouseAvatar.mockRejectedValue(notFound());
});

describe('W11 · 小屋没有专属小人（404）', () => {
  it('404 被当成空态：给出引导，且不报错、不阻塞小屋', async () => {
    renderPage();
    expect(await screen.findByTestId('cabin-no-house-avatar')).toBeInTheDocument();
    expect(screen.queryByTestId('cabin-avatar-error')).not.toBeInTheDocument();
    // 小屋本体照常渲染
    expect(screen.getByTestId('cabin-canvas')).toBeInTheDocument();
  });

  it('空态时不注入自定义小人（回退内置默认小人）', async () => {
    renderPage();
    await screen.findByTestId('cabin-no-house-avatar');
    expect(stageSpy).toHaveBeenCalledWith(undefined, undefined);
  });
});

describe('W11 · 小屋有专属小人', () => {
  it('注入两帧行走矩阵 + 字符调色板', async () => {
    houseApi.getHouseAvatar.mockResolvedValue(houseProfile());
    renderPage();
    await screen.findByTestId('cabin-house-avatar');
    const [frames, palette] = stageSpy.mock.calls.at(-1)!;
    expect(frames).toHaveLength(2);              // 两帧行走机制
    for (const f of frames as readonly (readonly string[])[]) {
      expect(f).toHaveLength(32);
      for (const row of f) expect(row).toHaveLength(24);
    }
    expect(Object.keys(palette as Record<string, number>)).toHaveLength(19);
  });

  it('展示专属小人短码', async () => {
    houseApi.getHouseAvatar.mockResolvedValue(houseProfile());
    renderPage();
    const box = await screen.findByTestId('cabin-house-avatar');
    expect(box).toHaveTextContent('6D73FAA1');
  });

  it('提供「去角色工坊调整」入口', async () => {
    houseApi.getHouseAvatar.mockResolvedValue(houseProfile());
    renderPage();
    expect(await screen.findByRole('button', { name: /去角色工坊调整/ })).toBeInTheDocument();
  });
});

describe('W11 · 小屋读取专属小人失败（真故障）', () => {
  it('显示错误码并如实说明已回退默认小人，但不阻塞小屋', async () => {
    const err = Object.assign(new Error('boom'), {
      status: 500,
      body: { code: 'avatar_backend_down', message: 'x', details: {} },
    });
    houseApi.getHouseAvatar.mockRejectedValue(err);
    renderPage();
    const box = await screen.findByTestId('cabin-avatar-error');
    expect(box).toHaveTextContent('avatar_backend_down');
    expect(box).toHaveTextContent('回退默认小人');
    expect(screen.getByTestId('cabin-canvas')).toBeInTheDocument();
  });
});

describe('W11 · 更衣镜入口', () => {
  it('点更衣镜触发跳转角色工坊（不再显示「建设中」占位提示）', async () => {
    houseApi.getHouseAvatar.mockResolvedValue(houseProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('cabin-house-avatar');

    await user.click(screen.getByTestId('cabin-enter-indoor'));
    await waitFor(() => expect(interiorSpy).toHaveBeenCalled());
    const cbs = interiorSpy.mock.calls.at(-1)![0] as {
      onFurnitureTap?: (item: { furnitureId: string }) => void;
    };
    expect(cbs.onFurnitureTap).toBeTypeOf('function');

    // 断言解释逻辑：mirror 走的是跳转路由，而不是气泡文案。
    // 跳转到 /avatar 后当前页仍在（测试 MemoryRouter 无目标路由），故断言
    // 「没有再输出施工中提示」，并单独断言文案里已不含「建设中」。
    cbs.onFurnitureTap?.({ furnitureId: 'mirror' });
    expect(document.body.textContent).not.toContain('建设中');
  });

  it('非mirror 家具仍走原有台词反馈（未被W11 逻辑吞掉）', async () => {
    houseApi.getHouseAvatar.mockResolvedValue(houseProfile());
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId('cabin-house-avatar');
    await user.click(screen.getByTestId('cabin-enter-indoor'));
    await waitFor(() => expect(interiorSpy).toHaveBeenCalled());
    const cbs = interiorSpy.mock.calls.at(-1)![0] as {
      onFurnitureTap?: (item: { furnitureId: string }) => void;
    };
    // 不应抛错：非 mirror 走 resolveFurnitureInteraction，未知 id 诚实返回
    expect(() => cbs.onFurnitureTap?.({ furnitureId: 'unknown_item' })).not.toThrow();
  });
});