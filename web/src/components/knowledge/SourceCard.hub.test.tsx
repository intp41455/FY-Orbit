import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { SourceCard } from './SourceCard';
import type { KBSourceStatus } from '../../api/knowledge';

function hubSource(patch: Partial<KBSourceStatus> = {}): KBSourceStatus {
  return {
    source_id: 'hub:hub-abc123',
    display_name: '中台 ima',
    available: true,
    configured: true,
    degraded: false,
    latency_ms: 12,
    detail: '连接正常',
    hint: '凭证来自中台连接（加密存储）',
    credential_fields: ['api_key', 'base_url'],
    credentials_present: { api_key: true, base_url: true },
    storage: 'hub',
    persist_restart: true,
    capabilities: { searchable: true, full_text: true, incremental: false, retryable: true },
    source: 'hub:hub-abc123',
    origin: 'connection',
    connection_id: 'hub-abc123',
    connection_state: 'active',
    connection_state_text: '已启用',
    builtin_source_id: 'ima',
    health: { ok: true, checked_at: '2026-10-04T00:00:00Z', latency_ms: 12, detail: '' },
    ...patch,
  };
}

function nativeSource(patch: Partial<KBSourceStatus> = {}): KBSourceStatus {
  return {
    source_id: 'ima',
    display_name: 'ima 知识库',
    available: false,
    configured: false,
    degraded: false,
    latency_ms: null,
    detail: '未接入',
    hint: '填 API Key',
    credential_fields: ['api_key', 'base_url'],
    credentials_present: { api_key: false, base_url: false },
    storage: 'hub_fernet',
    persist_restart: true,
    capabilities: { searchable: true, full_text: true, incremental: false, retryable: true },
    ...patch,
  };
}

function setup(source: KBSourceStatus) {
  const onConfigure = vi.fn().mockResolvedValue(undefined);
  const onForget = vi.fn().mockResolvedValue(undefined);
  const onProbe = vi.fn().mockResolvedValue(undefined);
  const onSync = vi.fn().mockResolvedValue(undefined);
  render(
    <SourceCard
      source={source}
      onConfigure={onConfigure}
      onForget={onForget}
      onProbe={onProbe}
      onSync={onSync}
    />,
  );
  return { onConfigure, onForget, onProbe, onSync };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('SourceCard · hub 桥接源（验收 F2）', () => {
  it('标注来源为超级中台连接，并显示连接状态', () => {
    setup(hubSource());
    const origin = screen.getByTestId('kb-source-origin-hub:hub-abc123');
    expect(origin.textContent).toContain('超级中台');
    expect(origin.textContent).toContain('已启用');
  });

  it('不渲染凭证输入框与「保存凭证」——凭证只在中台配', () => {
    setup(hubSource());
    expect(screen.queryByLabelText('hub:hub-abc123-api_key')).toBeNull();
    expect(screen.queryByText('保存凭证')).toBeNull();
    expect(screen.getByTestId('kb-source-hint-hub:hub-abc123').textContent).toContain('超级中台');
  });

  it('不提供「清除凭证」（避免两条凭证写路径）', () => {
    const { onForget } = setup(hubSource());
    expect(screen.queryByText('清除凭证')).toBeNull();
    expect(onForget).not.toHaveBeenCalled();
  });

  it('「测试连接」与「同步」仍可用', async () => {
    const { onProbe, onSync } = setup(hubSource());
    fireEvent.click(screen.getByText('测试连接'));
    fireEvent.click(screen.getByText('同步到本地索引'));
    await waitFor(() => {
      expect(onProbe).toHaveBeenCalledWith('hub:hub-abc123');
      expect(onSync).toHaveBeenCalledWith('hub:hub-abc123');
    });
  });

  it('未配置时同步按钮禁用，测试连接仍可点（如实不谎报可用）', () => {
    const { onProbe } = setup(hubSource({ available: false, configured: false, detail: '未接入（可在中台页配置）' }));
    const syncBtn = screen.getByText('同步到本地索引') as HTMLButtonElement;
    expect(syncBtn.disabled).toBe(true);
    expect(screen.getByTestId('kb-source-detail-hub:hub-abc123').textContent).toContain('未接入');
    expect((screen.getByText('测试连接') as HTMLButtonElement).disabled).toBe(false);
    expect(onProbe).not.toHaveBeenCalled();
  });

  it('中台探活失败原因展示在卡片上', () => {
    setup(hubSource({
      available: false,
      health: { ok: false, checked_at: null, latency_ms: null, detail: 'Connection refused' },
    }));
    const origin = screen.getByTestId('kb-source-origin-hub:hub-abc123');
    expect(origin.textContent).toContain('Connection refused');
  });

  it('原生源行为不变：仍有凭证输入与保存/清除按钮', () => {
    setup(nativeSource());
    expect(screen.getByLabelText('ima-api_key')).toBeTruthy();
    expect(screen.getByText('保存凭证')).toBeTruthy();
    expect(screen.getByText('清除凭证')).toBeTruthy();
    expect(screen.queryByTestId('kb-source-origin-ima')).toBeNull();
  });

  it('原生源点击保存会把值交给 onConfigure（回归保护）', async () => {
    const { onConfigure } = setup(nativeSource());
    fireEvent.change(screen.getByLabelText('ima-api_key'), { target: { value: 'k-123' } });
    fireEvent.click(screen.getByText('保存凭证'));
    await waitFor(() => {
      expect(onConfigure).toHaveBeenCalledWith('ima', { api_key: 'k-123' });
    });
  });
});
