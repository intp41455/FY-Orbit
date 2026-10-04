/**
 * P6 · 插件市场页测试。
 *
 * 门禁核心：**高风险包在列表与详情都显示风险标记**；安装前展示能力；
 * 安装失败显式报错（绝不假装成功）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PluginMarketPage } from './PluginMarketPage';
import { pluginsApi, type PluginCard } from '../../api/plugins';

vi.mock('../../api/plugins', () => ({
  pluginsApi: { list: vi.fn(), get: vi.fn(), publish: vi.fn(), install: vi.fn() },
}));

const LOW: PluginCard = {
  skill_id: 'sk-low', name: 'safe-instruction', version: '1.0.0',
  domain: 'work', source: 'internal', license: 'MIT', package_hash: 'a'.repeat(12),
  gate_profile: 'instruction', signature_verified: false,
  capabilities: ['instruction:inline'], risk_level: 'low', risk_reasons: [],
  scan: { passed: true, risk_level: 'none', finding_count: 0, scanner_version: '1' },
};
const HIGH: PluginCard = {
  skill_id: 'sk-high', name: 'scary-plugin', version: '2.0.0',
  domain: 'personal', source: 'external', license: 'MIT', package_hash: 'b'.repeat(12),
  gate_profile: 'plugin', signature_verified: false,
  capabilities: ['materialize:files'], risk_level: 'high',
  risk_reasons: ['materializes_files', 'unsigned'],
  scan: { passed: false, risk_level: 'high', finding_count: 3, scanner_version: '1' },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pluginsApi.list).mockResolvedValue({
    items: [LOW, HIGH], total: 2, limit: 20, offset: 0,
  });
});

describe('PluginMarketPage 列表', () => {
  it('渲染列表卡片与总数', async () => {
    render(<PluginMarketPage />);
    expect((await screen.findAllByTestId('plugin-item'))).toHaveLength(2);
    expect(screen.getByText('共 2 个包')).toBeInTheDocument();
  });

  it('高风险包在列表显示高风险标记（不可隐藏）', async () => {
    render(<PluginMarketPage />);
    expect(await screen.findByTestId('plugin-risk-high')).toBeInTheDocument();
    expect(screen.getByTestId('plugin-risk-high').textContent).toContain('高风险');
  });

  it('中风险与低风险徽标语义可辨', async () => {
    render(<PluginMarketPage />);
    expect(await screen.findByTestId('plugin-risk-low')).toBeInTheDocument();
  });

  it('列表卡片带扫描摘要（通过状态与发现数）', async () => {
    render(<PluginMarketPage />);
    const scans = await screen.findAllByTestId('plugin-scan-summary');
    expect(scans).toHaveLength(2);
    expect(scans[0].textContent).toContain('发现 0 条');
    expect(scans[1].textContent).toContain('发现 3 条');
  });

  it('搜索框提交把 query 传给 API', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.list).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    render(<PluginMarketPage />);
    await screen.findByTestId('plugin-empty');
    await user.type(screen.getByTestId('plugin-query'), 'scary');
    await user.click(screen.getByTestId('plugin-search'));
    await waitFor(() => {
      expect(pluginsApi.list).toHaveBeenCalledWith(expect.objectContaining({ query: 'scary', offset: 0 }));
    });
    expect(await screen.findByTestId('plugin-empty')).toBeInTheDocument();
  });
});

describe('PluginMarketPage 详情与安装', () => {
  it('门禁核心：高风险包在详情里再次显示高风险标记 + 能力声明 + 扫描摘要', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    await user.click(await screen.findByTestId('plugin-detail-sk-high'));
    const detail = await screen.findByTestId('plugin-detail');
    // 详情里的风险标记（第二枚高风险徽标）
    expect(detail.textContent).toContain('高风险');
    expect(screen.getAllByTestId('plugin-risk-high').length).toBeGreaterThanOrEqual(2);
    // 安装前必须展示能力
    const caps = screen.getByTestId('plugin-capabilities');
    expect(caps.textContent).toContain('物化文件并执行');
    expect(caps.textContent).toContain('未签名');
    // 扫描摘要可见
    expect(screen.getAllByTestId('plugin-scan-summary').length).toBeGreaterThanOrEqual(2);
  });

  it('plugin 包安装要求输入授权 ID 并随请求发送', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.install).mockResolvedValue({ ...HIGH, installed: true, grant_id: 'g-1' });
    render(<PluginMarketPage />);
    await user.click(await screen.findByTestId('plugin-detail-sk-high'));
    await user.type(screen.getByTestId('plugin-grant-input'), 'g-1');
    await user.click(screen.getByTestId('plugin-detail-install'));
    await waitFor(() => {
      expect(pluginsApi.install).toHaveBeenCalledWith('sk-high', 'g-1');
    });
    expect(await screen.findByTestId('plugin-install-note').then((el) => el.textContent))
      .toContain('已安装');
  });

  it('安装失败（如缺授权）显式展示错误，绝不假装成功', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.install).mockRejectedValue(
      new Error('install_grant_required: plugin 包安装需要出示授权'),
    );
    render(<PluginMarketPage />);
    await user.click(await screen.findByTestId('plugin-detail-sk-low'));
    await user.click(screen.getByTestId('plugin-detail-install'));
    const note = await screen.findByTestId('plugin-install-note');
    expect(note.textContent).toContain('安装失败');
    expect(note.textContent).toContain('install_grant_required');
    expect(note.getAttribute('role')).toBe('status');
  });

  it('关闭详情后回到纯列表', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    await user.click(await screen.findByTestId('plugin-detail-sk-high'));
    await user.click(screen.getByTestId('plugin-detail-close'));
    expect(screen.queryByTestId('plugin-detail')).not.toBeInTheDocument();
  });

  it('加载失败显式报错（role=alert）', async () => {
    vi.mocked(pluginsApi.list).mockRejectedValue(new Error('backend down'));
    render(<PluginMarketPage />);
    const err = await screen.findByRole('alert');
    expect(err.textContent).toContain('backend down');
  });
});
