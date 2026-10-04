// W9 资产库纯函数单测（assetFormat.ts）。
// 重点：来源标注必须诚实（本地合成 ≠ 模型生成）、未接入不得显示可用态、
// 挂载位判定与上限预检。
import { describe, expect, it } from 'vitest';
import {
  assetOriginLabel,
  channelBadge,
  exceedsLimit,
  formatBytes,
  isMountedAt,
  KIND_LABEL,
  kindFromFileName,
  mountOptionsFor,
} from './assetFormat';
import type { AssetRecord, ChannelStatus } from '../../api/assets';

function asset(overrides: Partial<AssetRecord> = {}): AssetRecord {
  return {
    id: 'a1',
    owner_id: 'owner',
    kind: 'image',
    name: 'x',
    mime: 'image/png',
    size: 1024,
    meta: {},
    version: 1,
    created_at: null,
    raw_url: '/api/assets/a1/raw',
    storage_rel: 'owner/a1.png',
    ...overrides,
  };
}

function channel(overrides: Partial<ChannelStatus> = {}): ChannelStatus {
  return {
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
    detail: '未接入',
    ...overrides,
  };
}

describe('formatBytes', () => {
  it('按量级给出可读体积', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(-5)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
  });
});

describe('assetOriginLabel · 诚实标注来源', () => {
  it('本地合成器明确标注「本地合成」而非模型生成', () => {
    const label = assetOriginLabel(
      asset({ kind: 'music', meta: { provider: 'local_synth', mood_label: '平静' } }),
    );
    expect(label).toContain('本地合成');
    expect(label).not.toContain('AI 作曲');
  });

  it('外部服务生成必须带模型名', () => {
    const label = assetOriginLabel(
      asset({ meta: { provider: 'openai_compatible_images', model: 'gpt-image-1' } }),
    );
    expect(label).toBe('外部服务生成 · gpt-image-1');
  });

  it('上传资产标为本地上传', () => {
    expect(assetOriginLabel(asset({ meta: { source: 'upload' } }))).toBe('本地上传');
  });
});

describe('isMountedAt', () => {
  it('只有匹配角色才为真', () => {
    const a = asset({ meta: { cabin_mount: 'wall' } });
    expect(isMountedAt(a, 'wall')).toBe(true);
    expect(isMountedAt(a, 'bgm')).toBe(false);
    expect(isMountedAt(asset(), 'wall')).toBe(false);
  });
});

describe('channelBadge · 未接入绝不显示可用态', () => {
  it('未配置 → 未接入', () => {
    expect(channelBadge(channel()).label).toBe('未接入');
  });

  it('已配置但缺 key 且非本机 → 警告', () => {
    const badge = channelBadge(channel({ configured: true, base_url: 'https://api.example.com/v1' }));
    expect(badge.label).toBe('已配置缺 Key');
    expect(badge.tone).toBe('warn');
  });

  it('本机端点免鉴权也算已接入', () => {
    expect(channelBadge(channel({ configured: true, base_url: 'http://127.0.0.1:7860/v1' })).label).toBe(
      '已接入',
    );
  });
});

describe('mountOptionsFor', () => {
  it('图片才能上墙，音频/音乐才能当 BGM', () => {
    expect(mountOptionsFor('image')).toEqual({ wall: true, bgm: false });
    expect(mountOptionsFor('music')).toEqual({ wall: false, bgm: true });
    expect(mountOptionsFor('audio')).toEqual({ wall: false, bgm: true });
    expect(mountOptionsFor('doc')).toEqual({ wall: false, bgm: false });
  });
});

describe('exceedsLimit', () => {
  const limits = { image: 100, music: 200 };

  it('超限为真、边界为假', () => {
    expect(exceedsLimit(101, limits, 'image')).toBe(true);
    expect(exceedsLimit(100, limits, 'image')).toBe(false);
  });

  it('未知类型不猜上限（交给后端拒）', () => {
    expect(exceedsLimit(10 ** 9, limits, 'doc')).toBe(false);
  });
});

describe('kindFromFileName', () => {
  it('按扩展名归类，未知归为文档', () => {
    expect(kindFromFileName('a.PNG')).toBe('image');
    expect(kindFromFileName('b.wav')).toBe('music');
    expect(kindFromFileName('c.flac')).toBe('music');
    expect(kindFromFileName('d.bin')).toBe('doc');
  });
});

describe('KIND_LABEL', () => {
  it('四类都有中文标签', () => {
    expect(KIND_LABEL).toEqual({ image: '图片', audio: '音频', music: '音乐', doc: '文档' });
  });
});