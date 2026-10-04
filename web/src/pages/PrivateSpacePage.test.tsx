// W9 私人空间三模块单测。
//
// 验收对应任务书 §4 的前端部分：
//  - 空库给诚实空态，不塞示例资产；
//  - 图片通道未接入 → 显示「未接入生成服务」，**没有**可点的生成按钮，也没有假图；
//  - 音乐模块未配 key 也能合成 → 网格里出现带「本地合成」标注的卡片；
//  - 拖拽上传 → 卡片入网格；删除 → 消失；挂小屋墙/设为 BGM → 按钮态切换；
//  - 后端 503 / 413 原样展示 code，不静默吞错。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/assets', async () => {
  const actual = await vi.importActual<typeof import('../api/assets')>('../api/assets');
  return {
    ...actual,
    assetsApi: {
      list: vi.fn(),
      upload: vi.fn(),
      remove: vi.fn(),
      setMount: vi.fn(),
      channels: vi.fn(),
      configureChannel: vi.fn(),
      forgetChannel: vi.fn(),
      generateImage: vi.fn(),
      generateMusic: vi.fn(),
      generateSpeech: vi.fn(),
      mounted: vi.fn(),
    },
  };
});

import { AssetApiError, assetsApi, type AssetRecord, type ChannelOverview } from '../api/assets';
import { PrivateSpacePage } from './PrivateSpacePage';

const api = () => assetsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function makeAsset(overrides: Partial<AssetRecord> = {}): AssetRecord {
  return {
    id: 'img1',
    owner_id: 'owner',
    kind: 'image',
    name: '挂画.png',
    mime: 'image/png',
    size: 2048,
    meta: {},
    version: 1,
    created_at: null,
    raw_url: '/api/assets/img1/raw',
    storage_rel: 'owner/img1.png',
    ...overrides,
  };
}

const OVERVIEW_OFFLINE: ChannelOverview = {
  channels: [
    {
      channel: 'image',
      configured: false,
      source: 'unconfigured',
      model: '',
      base_url: '',
      has_api_key: false,
      credential_fields: ['base_url', 'api_key'],
      credentials_present: {},
      storage: 'memory',
      persist_restart: false,
      detail: '未接入生成服务：请填写 Base URL（未配置时不会返回任何“已生成”内容）',
    },
    {
      channel: 'tts',
      configured: false,
      source: 'unconfigured',
      model: '',
      base_url: '',
      has_api_key: false,
      credential_fields: ['base_url', 'api_key'],
      credentials_present: {},
      storage: 'memory',
      persist_restart: false,
      detail: '未接入语音合成服务',
    },
  ],
  music: {
    channel: 'music',
    configured: true,
    provider: 'local_synth',
    detail: '本地芯片音乐合成器：零外部依赖，未配置任何 key 也可真实产出 WAV',
  },
  moods: [
    { id: 'calm', label: '平静', tempo_bpm: 72, waveform: 'triangle' },
    { id: 'bright', label: '明亮', tempo_bpm: 108, waveform: 'square' },
  ],
};

const OVERVIEW_IMAGE_ON: ChannelOverview = {
  ...OVERVIEW_OFFLINE,
  channels: OVERVIEW_OFFLINE.channels.map((c) =>
    c.channel === 'image'
      ? {
          ...c,
          configured: true,
          source: 'credentials',
          model: 'sdxl',
          base_url: 'http://127.0.0.1:7860/v1',
          has_api_key: false,
          detail: '已接入：http://127.0.0.1:7860/v1（配置来源 credentials）',
        }
      : c,
  ),
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/private']}>
      <PrivateSpacePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  api().list.mockResolvedValue({
    assets: [],
    count: 0,
    kinds: ['image', 'audio', 'music', 'doc'],
    max_bytes_by_kind: { image: 10 * 1024 * 1024, audio: 20 * 1024 * 1024, music: 20 * 1024 * 1024, doc: 50 * 1024 * 1024 },
  });
  api().channels.mockResolvedValue(OVERVIEW_OFFLINE);
  api().upload.mockImplementation((file: File) =>
    Promise.resolve(makeAsset({ id: `up-${file.name}`, name: file.name })),
  );
  api().remove.mockResolvedValue({ ...makeAsset(), deleted: true, file_removed: true });
  api().setMount.mockImplementation((id: string, role: string) =>
    Promise.resolve({ asset: makeAsset({ id, meta: { cabin_mount: role } }) }),
  );
  api().generateMusic.mockResolvedValue({
    asset: makeAsset({
      id: 'm1',
      kind: 'music',
      name: 'chiptune-calm.wav',
      mime: 'audio/wav',
      meta: { provider: 'local_synth', mood_label: '平静' },
    }),
  });
  api().generateImage.mockResolvedValue({ asset: makeAsset({ id: 'gen1' }) });
  api().configureChannel.mockResolvedValue({
    ...OVERVIEW_OFFLINE.channels[0],
    configured: true,
  });
});

describe('PrivateSpacePage · 加载与空态', () => {
  it('空库时给出诚实空态，不塞示例资产', async () => {
    renderPage();
    expect(await screen.findByTestId('assets-empty-image')).toHaveTextContent('还没有内容');
    expect(screen.getByTestId('assets-empty-music')).toBeInTheDocument();
    expect(screen.queryByTestId('asset-img1')).not.toBeInTheDocument();
  });

  it('列表真实返回的图片渲染缩略图与体积', async () => {
    api().list.mockResolvedValue({
      assets: [makeAsset()],
      count: 1,
      kinds: ['image'],
      max_bytes_by_kind: { image: 10 * 1024 * 1024 },
    });
    renderPage();
    const card = await screen.findByTestId('asset-img1');
    expect(within(card).getByTestId('asset-thumb')).toHaveAttribute(
      'src',
      expect.stringContaining('/api/assets/img1/raw'),
    );
    expect(within(card).getByText(/2\.0 KB/)).toBeInTheDocument();
    expect(within(card).getByText(/本地上传/)).toBeInTheDocument();
  });
});

describe('PrivateSpacePage · 图片通道诚实性', () => {
  it('未接入时显示「未接入生成服务」，且没有可点的生成按钮', async () => {
    renderPage();
    expect(await screen.findByTestId('asset-image-unconfigured')).toHaveTextContent(
      '未接入生成服务',
    );
    expect(screen.queryByTestId('asset-image-generate')).not.toBeInTheDocument();
  });

  it('配置后可生成图片，成功后网格出现新资产', async () => {
    api().channels.mockResolvedValue(OVERVIEW_IMAGE_ON);
    renderPage();
    const prompt = await screen.findByTestId('asset-image-prompt');
    await userEvent.type(prompt, '像素风雪夜小屋');
    await userEvent.click(screen.getByTestId('asset-image-generate'));
    await waitFor(() =>
      expect(api().generateImage).toHaveBeenCalledWith({ prompt: '像素风雪夜小屋', size: '1024x1024' }),
    );
    expect(await screen.findByText(/图片已生成并入库/)).toBeInTheDocument();
  });

  it('生成失败原样展示后端 code，不假装成功', async () => {
    api().channels.mockResolvedValue(OVERVIEW_IMAGE_ON);
    api().generateImage.mockRejectedValue(
      new AssetApiError(503, 'image_provider_not_configured', '未接入图片生成服务'),
    );
    renderPage();
    await userEvent.type(await screen.findByTestId('asset-image-prompt'), '猫');
    await userEvent.click(screen.getByTestId('asset-image-generate'));
    expect(await screen.findByTestId('asset-generate-error')).toHaveTextContent(
      'image_provider_not_configured',
    );
  });
});

describe('PrivateSpacePage · 音乐模块（零 key 真实产出）', () => {
  it('合成后卡片标注「本地合成」并提供播放控件', async () => {
    api().list.mockResolvedValue({
      assets: [
        makeAsset({
          id: 'm1',
          kind: 'music',
          name: 'chiptune-calm.wav',
          mime: 'audio/wav',
          raw_url: '/api/assets/m1/raw',
          meta: { provider: 'local_synth', mood_label: '平静' },
        }),
      ],
      count: 1,
      kinds: ['music'],
      max_bytes_by_kind: { music: 20 * 1024 * 1024 },
    });
    renderPage();
    await userEvent.click(await screen.findByTestId('asset-music-generate'));
    await waitFor(() =>
      expect(api().generateMusic).toHaveBeenCalledWith({ mood: 'calm', seconds: 12 }),
    );
    const card = await screen.findByTestId('asset-m1');
    expect(within(card).getByText(/本地合成 · 平静/)).toBeInTheDocument();
    expect(within(card).getByTestId('asset-audio-m1')).toHaveAttribute(
      'src',
      expect.stringContaining('/api/assets/m1/raw'),
    );
    expect(within(card).getByText(/波形条为示意样式/)).toBeInTheDocument();
  });
});

describe('PrivateSpacePage · 上传 / 删除 / 挂载', () => {
  it('选择文件即入库，网格出现该资产', async () => {
    renderPage();
    const input = await screen.findByTestId('asset-file-input');
    const file = new File([new Uint8Array([1, 2, 3])], '照片.png', { type: 'image/png' });
    await userEvent.upload(input, file);
    await waitFor(() => expect(api().upload).toHaveBeenCalledWith(file, 'image'));
    expect(await screen.findByText(/已入库：照片\.png/)).toBeInTheDocument();
  });

  it('删除后卡片消失并说明文件是否真被删', async () => {
    api().list.mockResolvedValue({
      assets: [makeAsset()],
      count: 1,
      kinds: ['image'],
      max_bytes_by_kind: { image: 10 * 1024 * 1024 },
    });
    renderPage();
    await userEvent.click(await screen.findByTestId('asset-delete-img1'));
    await waitFor(() => expect(api().remove).toHaveBeenCalledWith('img1'));
    expect(await screen.findByText(/记录 \+ 磁盘文件/)).toBeInTheDocument();
  });

  it('图片可挂小屋墙，音乐可设为 BGM（按钮态可切换）', async () => {
    api().list.mockResolvedValue({
      assets: [
        makeAsset(),
        makeAsset({ id: 'm1', kind: 'music', name: 'bgm.wav', mime: 'audio/wav' }),
      ],
      count: 2,
      kinds: ['image', 'music'],
      max_bytes_by_kind: { image: 10 * 1024 * 1024, music: 20 * 1024 * 1024 },
    });
    renderPage();
    await userEvent.click(await screen.findByTestId('asset-wall-img1'));
    await waitFor(() => expect(api().setMount).toHaveBeenCalledWith('img1', 'wall'));
    expect(await screen.findByText(/挂到小屋墙上/)).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('asset-bgm-m1'));
    await waitFor(() => expect(api().setMount).toHaveBeenCalledWith('m1', 'bgm'));
  });

  it('文档类资产不提供挂载按钮（不会给出无效入口）', async () => {
    api().list.mockResolvedValue({
      assets: [makeAsset({ id: 'd1', kind: 'doc', name: 'note.txt', mime: 'text/plain' })],
      count: 1,
      kinds: ['doc'],
      max_bytes_by_kind: { doc: 50 * 1024 * 1024 },
    });
    renderPage();
    await screen.findByTestId('asset-d1');
    expect(screen.queryByTestId('asset-wall-d1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('asset-bgm-d1')).not.toBeInTheDocument();
  });

  it('超限文件不上传并提示上限', async () => {
    api().list.mockResolvedValue({
      assets: [],
      count: 0,
      kinds: ['image'],
      max_bytes_by_kind: { image: 10 * 1024 * 1024 },
    });
    renderPage();
    const input = await screen.findByTestId('asset-file-input');
    const big = new File([new Uint8Array(2)], '大图.png', { type: 'image/png' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    await userEvent.upload(input, big);
    expect(await screen.findByTestId('asset-generate-error')).toHaveTextContent('超过图片上限 10MB');
    expect(api().upload).not.toHaveBeenCalled();
  });
});

describe('PrivateSpacePage · 通道配置', () => {
  it('保存接入后刷新通道状态并如实提示来源', async () => {
    renderPage();
    await userEvent.type(await screen.findByTestId('asset-channel-base-url'), 'http://127.0.0.1:7860/v1');
    await userEvent.click(screen.getByTestId('asset-channel-save'));
    await waitFor(() =>
      expect(api().configureChannel).toHaveBeenCalledWith('image', {
        base_url: 'http://127.0.0.1:7860/v1',
        api_key: '',
        model: '',
      }),
    );
    expect(await screen.findByText(/通道 image/)).toBeInTheDocument();
  });

  it('语音通道未接入时徽标显示「未接入」', async () => {
    renderPage();
    expect(await screen.findByTestId('asset-tts-badge')).toHaveTextContent('未接入');
  });
});