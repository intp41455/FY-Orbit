/**
 * I-12 · 远程源导入入口 + blocked 陈列（补前端闭环的回归测试）。
 *
 * 门禁核心：
 *  - 导入入口真的调 `POST /api/plugins/marketplace/sources/{id}/import` 并刷新列表；
 *  - 导入失败**如实展示后端错误码**，绝不吞掉、绝不假装成功；
 *  - 没有远程源连接时如实说明，不给假按钮；
 *  - blocked 包可见、明确标注「暂不可安装」并写明原因。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PluginMarketPage } from './PluginMarketPage';
import { pluginsApi, type PluginCard } from '../../api/plugins';
import { hubApi } from '../../api/hub';

vi.mock('../../api/plugins', () => ({
  pluginsApi: {
    list: vi.fn(),
    get: vi.fn(),
    publish: vi.fn(),
    install: vi.fn(),
    rate: vi.fn(),
    getRatings: vi.fn(),
    importSource: vi.fn(),
    getHierarchy: vi.fn(),
    listTemplates: vi.fn(),
    previewSwitchImpact: vi.fn(),
    exportTemplate: vi.fn(),
    securityReview: vi.fn(),
    importTemplate: vi.fn(),
    rateTemplate: vi.fn(),
  },
}));

vi.mock('../../api/hub', () => ({
  hubApi: {
    listConnections: vi.fn(),
  },
}));

const LISTED: PluginCard = {
  skill_id: 'sk-low', name: 'safe-instruction', version: '1.0.0',
  domain: 'work', source: 'internal', license: 'MIT', package_hash: 'a'.repeat(12),
  gate_profile: 'instruction', signature_verified: false,
  capabilities: ['instruction:inline'], risk_level: 'low', risk_reasons: [],
  scan: { passed: true, risk_level: 'none', finding_count: 0, scanner_version: '1' },
  rating: { average_rating: 4.5, rating_count: 10, score: 4.3 },
};

const BLOCKED: PluginCard = {
  ...LISTED,
  skill_id: 'sk-remote',
  name: 'remote-plugin',
  version: '2.0.0',
  source: 'remote:hub-1',
  gate_profile: 'plugin',
  capabilities: ['materialize:files'],
  risk_level: 'medium',
  risk_reasons: ['materializes_files', 'unsigned'],
  scan: { passed: false, risk_level: 'medium', finding_count: 1, scanner_version: '1' },
  installable: false,
  blocked_reasons: ['scan_not_passed', 'signature_not_verified'],
};

const GIT_SOURCE = {
  id: 'hub-git-1',
  name: '公司内部市场源',
  kind: 'market_source',
  group: 'tool',
  preset_id: '',
  icon: '🔌',
  description: '',
  state: 'active',
  config: { url: 'git@github.com:acme/fy-market.git', source_type: 'git' },
  secret_fields: [],
  capabilities: [],
  has_manifest: false,
  params: null,
  preference: 0,
  health: { ok: null, checked_at: null, latency_ms: null, detail: '' },
  version: 1,
  created_at: null,
  updated_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pluginsApi.list).mockResolvedValue({
    items: [LISTED], total: 1, limit: 20, offset: 0, blocked: [],
  });
  vi.mocked(pluginsApi.getHierarchy).mockResolvedValue({ layers: [] } as any);
  vi.mocked(pluginsApi.listTemplates).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(hubApi.listConnections).mockResolvedValue({
    connections: [GIT_SOURCE] as any,
    count: 1,
  });
});

describe('远程源导入入口', () => {
  it('选连接后调导入端点、刷新列表并如实展示导入条数', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.importSource).mockResolvedValue({
      connection_id: 'hub-git-1',
      imported: ['sk-remote'],
      skipped: [{ id: 'sk-bad', reason: 'hub_market_source_git_path_escape: 路径越界' }],
    });
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');

    await user.click(screen.getByTestId('market-source-open'));
    await screen.findByTestId('market-source-select');
    await user.click(screen.getByTestId('market-source-import'));

    await waitFor(() => {
      expect(pluginsApi.importSource).toHaveBeenCalledWith('hub-git-1');
    });
    const note = await screen.findByTestId('market-source-note');
    expect(note.textContent).toContain('导入完成：1 个包');
    // 跳过的包必须逐条展示（含后端原因），不能压成「部分成功」
    expect(note.textContent).toContain('hub_market_source_git_path_escape');
    // 导入后刷新市场列表（含 blocked 陈列）
    expect(vi.mocked(pluginsApi.list).mock.calls.length).toBeGreaterThan(1);
  });

  it('导入失败原样展示后端错误码，绝不假装成功', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.importSource).mockRejectedValue(
      Object.assign(new Error('git clone 失败（退出码 128）：repository not found'), {
        status: 400,
        body: { code: 'hub_market_source_git_clone_failed', message: 'git clone 失败（退出码 128）：repository not found' },
      }),
    );
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');
    await user.click(screen.getByTestId('market-source-open'));
    await screen.findByTestId('market-source-select');
    await user.click(screen.getByTestId('market-source-import'));

    const note = await screen.findByTestId('market-source-note');
    expect(note.textContent).toContain('导入失败');
    expect(note.textContent).toContain('hub_market_source_git_clone_failed');
    expect(note.className).toContain('ui-toast--error');
  });

  it('没有登记远程源时如实说明，不给假入口', async () => {
    const user = userEvent.setup();
    vi.mocked(hubApi.listConnections).mockResolvedValue({ connections: [], count: 0 });
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');
    await user.click(screen.getByTestId('market-source-open'));

    expect(await screen.findByTestId('market-source-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('market-source-import')).not.toBeInTheDocument();
  });

  it('读取远程源列表失败时如实报错', async () => {
    const user = userEvent.setup();
    vi.mocked(hubApi.listConnections).mockRejectedValue(new Error('backend down'));
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');
    await user.click(screen.getByTestId('market-source-open'));

    const note = await screen.findByTestId('market-source-note');
    expect(note.textContent).toContain('读取远程源列表失败');
    expect(note.textContent).toContain('backend down');
  });
});

describe('blocked 陈列（可见但暂不可安装）', () => {
  it('展示 blocked 包、标注不可安装并列出原因', async () => {
    vi.mocked(pluginsApi.list).mockResolvedValue({
      items: [LISTED], total: 1, limit: 20, offset: 0, blocked: [BLOCKED],
    });
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');

    const rows = await screen.findAllByTestId('plugin-blocked-item');
    expect(rows).toHaveLength(1);
    expect(rows[0]!.textContent).toContain('remote-plugin');
    expect(screen.getByTestId('plugin-blocked-badge').textContent).toContain('暂不可安装');
    const reasons = screen.getByTestId('plugin-blocked-reasons');
    expect(reasons.textContent).toContain('静态扫描未通过');
    expect(reasons.textContent).toContain('签名校验未通过');
    // blocked 包不能出现安装按钮（不可装就别给能点的入口）
    expect(screen.queryByTestId('plugin-install-sk-remote')).not.toBeInTheDocument();
  });

  it('没有 blocked 包时不渲染该区块', async () => {
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');
    expect(screen.queryByTestId('plugin-blocked-section')).not.toBeInTheDocument();
  });
});