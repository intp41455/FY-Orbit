// W9 小屋联动单测：资产库挂画/BGM 在 CabinPage 的接线。
//
// 关键断言（任务书 §1.4）：
//  - 墙上有挂画 → 渲染 DOM 覆盖层 + 取下按钮（不改 cabinScene，只注入覆盖层）；
//  - 挂了 BGM → 出现播放/暂停钮 + <audio src> 指向鉴权 raw 端点；
//  - 没挂东西 → 诚实空态（不塞默认图/默认曲）；
//  - 取下挂画 → 调 setMount(id,'') 并刷新。
// PixiJS 在 jsdom 不可用，按既有约定整体 mock CabinStage / InteriorStage。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../components/cabin/CabinStage', () => ({
  CabinStage: () => <canvas data-testid="cabin-canvas" aria-hidden="true" />,
}));

vi.mock('../components/cabin/interior/InteriorStage', () => ({
  InteriorStage: () => <canvas data-testid="cabin-interior-canvas" aria-hidden="true" />,
}));

vi.mock('../api/butler', () => ({
  butlerApi: {
    status: () => Promise.resolve({ model_configured: false }),
    dialogue: () => Promise.reject(new Error('not configured')),
  },
}));

vi.mock('../api/avatar', async () => {
  const actual = await vi.importActual<typeof import('../api/avatar')>('../api/avatar');
  return {
    ...actual,
    getHouseAvatar: () => Promise.reject(Object.assign(new Error('no avatar'), { code: 'avatar_not_found' })),
  };
});

vi.mock('../api/assets', async () => {
  const actual = await vi.importActual<typeof import('../api/assets')>('../api/assets');
  return {
    ...actual,
    assetsApi: {
      list: vi.fn().mockResolvedValue({ assets: [], count: 0, kinds: ['image'], max_bytes_by_kind: {} }),
      mounted: vi.fn(),
      setMount: vi.fn().mockResolvedValue({ asset: { id: 'a1' } }),
      upload: vi.fn(),
      remove: vi.fn(),
      channels: vi.fn(),
      configureChannel: vi.fn(),
      forgetChannel: vi.fn(),
      generateImage: vi.fn(),
      generateMusic: vi.fn(),
      generateSpeech: vi.fn(),
    },
  };
});

import { assetsApi, type AssetRecord } from '../api/assets';
import { CabinPage } from './CabinPage';

const api = () => assetsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function makeAsset(overrides: Partial<AssetRecord> = {}): AssetRecord {
  return {
    id: 'a1',
    owner_id: 'owner',
    kind: 'image',
    name: '挂画.png',
    mime: 'image/png',
    size: 4096,
    meta: {},
    version: 1,
    created_at: null,
    raw_url: '/api/assets/a1/raw',
    storage_rel: 'owner/a1.png',
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/cabin']}>
      <CabinPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  api().mounted.mockImplementation((role: string) =>
    Promise.resolve({ role, assets: [], count: 0 }),
  );
  api().setMount.mockResolvedValue({ asset: makeAsset({ meta: { cabin_mount: '' } }) });
});

describe('CabinPage · W9 资产联动', () => {
  it('没有挂画/BGM 时给诚实空态，不塞默认内容', async () => {
    renderPage();
    expect(await screen.findByTestId('w9-wall-empty')).toHaveTextContent('墙上还是空的');
    expect(await screen.findByTestId('w9-bgm-empty')).toHaveTextContent('还没设置 BGM');
    expect(screen.queryByTestId('cabin-wallart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('w9-bgm-audio')).not.toBeInTheDocument();
  });

  it('墙上挂画渲染为 DOM 覆盖层并指向鉴权 raw 端点', async () => {
    api().mounted.mockImplementation((role: string) =>
      Promise.resolve(
        role === 'wall'
          ? { role, assets: [makeAsset()], count: 1 }
          : { role, assets: [], count: 0 },
      ),
    );
    renderPage();
    const art = await screen.findByTestId('cabin-wallart');
    expect(art.querySelector('img')).toHaveAttribute('src', expect.stringContaining('/api/assets/a1/raw'));
    expect(await screen.findByTestId('w9-wall-name')).toHaveTextContent('挂画.png');
  });

  it('取下挂画会调 setMount(id, "") 并刷新', async () => {
    api().mounted.mockImplementation((role: string) =>
      Promise.resolve(
        role === 'wall'
          ? { role, assets: [makeAsset()], count: 1 }
          : { role, assets: [], count: 0 },
      ),
    );
    renderPage();
    await userEvent.click(await screen.findByTestId('w9-wall-remove'));
    await waitFor(() => expect(api().setMount).toHaveBeenCalledWith('a1', ''));
    await waitFor(() => expect(api().mounted).toHaveBeenCalledWith('wall'));
  });

  it('BGM 资产渲染播放钮与 <audio>（raw 端点、循环、不预取）', async () => {
    api().mounted.mockImplementation((role: string) =>
      Promise.resolve(
        role === 'bgm'
          ? {
              role,
              assets: [makeAsset({ id: 'm1', kind: 'music', name: 'bgm.wav', mime: 'audio/wav', raw_url: '/api/assets/m1/raw' })],
              count: 1,
            }
          : { role, assets: [], count: 0 },
      ),
    );
    renderPage();
    expect(await screen.findByTestId('w9-bgm-name')).toHaveTextContent('bgm.wav');
    const audio = screen.getByTestId('w9-bgm-audio') as HTMLAudioElement;
    expect(audio.getAttribute('src')).toContain('/api/assets/m1/raw');
    expect(audio).toHaveAttribute('loop');
    expect(audio).toHaveAttribute('preload', 'none');
    expect(await screen.findByTestId('w9-bgm-toggle')).toHaveTextContent('播放');
  });
});