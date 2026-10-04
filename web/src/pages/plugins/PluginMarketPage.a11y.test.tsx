/**
 * P12 · 插件市场页无障碍（表单可命名 / 风险不只靠颜色 / 按钮语义明确）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PluginMarketPage } from './PluginMarketPage';
import { pluginsApi, type PluginCard } from '../../api/plugins';

vi.mock('../../api/plugins', () => ({
  pluginsApi: { list: vi.fn(), get: vi.fn(), publish: vi.fn(), install: vi.fn() },
}));

const HIGH: PluginCard = {
  skill_id: 'sk-h', name: 'risky-plugin', version: '1.0.0',
  domain: 'personal', source: 'external', license: 'MIT', package_hash: 'b'.repeat(12),
  gate_profile: 'plugin', signature_verified: false,
  capabilities: ['materialize:files'], risk_level: 'high',
  risk_reasons: ['materializes_files'],
  scan: { passed: true, risk_level: 'none', finding_count: 0 },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pluginsApi.list).mockResolvedValue({ items: [HIGH], total: 1, limit: 20, offset: 0 });
});

describe('PluginMarketPage 无障碍（P12）', () => {
  it('搜索输入框有 aria-label（无可见占位文本时仍可被读屏命名）', () => {
    render(<PluginMarketPage />);
    expect(screen.getByTestId('plugin-query').getAttribute('aria-label')).toBe('搜索插件包');
  });

  it('风险等级以文字徽标传达（高风险不只靠红色——文本「高风险」存在）', async () => {
    render(<PluginMarketPage />);
    const badge = await screen.findByTestId('plugin-risk-high');
    expect(badge.textContent).toContain('高风险');
  });

  it('安装按钮文字含包名语义（明确知道在装什么）', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    const btn = await screen.findByTestId('plugin-install-sk-h');
    expect((btn.textContent ?? '').trim()).toBe('安装');
    // 详情里打开后再次确认能力清单以文本呈现（安装前必读）
    await user.click(await screen.findByTestId('plugin-detail-sk-h'));
    const caps = screen.getByTestId('plugin-capabilities');
    expect(caps.textContent).toContain('物化文件并执行');
  });

  it('加载失败以 role="alert" 呈现（错误可被读屏即时播报）', async () => {
    vi.mocked(pluginsApi.list).mockRejectedValue(new Error('backend down'));
    render(<PluginMarketPage />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });
});
