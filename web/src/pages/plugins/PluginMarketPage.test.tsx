/**
 * P6 · 插件市场页测试。
 *
 * 门禁核心：**高风险包在列表与详情都显示风险标记**；安装前展示能力；
 * 安装失败显式报错（绝不假装成功）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PluginMarketPage } from './PluginMarketPage';
import { pluginsApi, type PluginCard } from '../../api/plugins';

vi.mock('../../api/plugins', () => ({
  pluginsApi: {
    list: vi.fn(),
    get: vi.fn(),
    publish: vi.fn(),
    install: vi.fn(),
    rate: vi.fn(),
    getRatings: vi.fn(),
    getHierarchy: vi.fn(),
    listTemplates: vi.fn(),
    previewSwitchImpact: vi.fn(),
    exportTemplate: vi.fn(),
    securityReview: vi.fn(),
    importTemplate: vi.fn(),
    rateTemplate: vi.fn(),
  },
}));

const FAKE_HIERARCHY = {
  schema_version: '1.0.0',
  single_source: true,
  layers: [
    {
      id: 'novice_default',
      label: '新手默认层（开箱即用）',
      items: [
        {
          template_id: 'builtin:novice:general',
          name: '全能助手默认模板',
          scenario: 'general',
          scenario_label: '通用全能',
          summary: '内置默认模板，隐藏所有编排复杂度',
          complexity_hidden: true,
          example_task: { goal: '快速启动日常任务', prompt: '帮我总结这份会议纪要' },
        },
      ],
    },
    {
      id: 'advanced_swappable',
      label: '进阶场景层（按需切换）',
      scenarios: [
        {
          id: 'code_review',
          label: '代码评审',
          templates: [
            { template_id: 'builtin:adv:review', name: '深度代码评审团队', summary: '专业级审查', member_count: 3 },
          ],
        },
      ],
    },
    {
      id: 'technical_removable',
      label: '技术可拆层（透明调试）',
      portal: {
        first_class_entry: true,
        description: '开发者与高级工程师专属入口，支持直视 Prompt、AST 上下文与调试',
        supported_verbs: ['view_prompt', 'debug_ast', 'inspect_context', 'disassemble_pipeline'],
        features: [
          { id: 'prompt_visibility', label: 'Prompt 完全可见与导出' },
          { id: 'cursor_context', label: 'Cursor 上下文装配引擎' },
        ],
      },
    },
  ],
};

const FAKE_TEMPLATES = [
  {
    template_id: 'builtin:novice:general',
    name: '全能助手默认模板',
    scenario: 'general',
    scenario_label: '通用全能',
    summary: '内置默认模板',
    quality_tier: 'production',
    member_count: 1,
    is_factory: true,
    rating: { average_rating: 4.8, rating_count: 12, score: 4.6, distribution: { '5': 10, '4': 2, '3': 0, '2': 0, '1': 0 } },
  },
];

const LOW: PluginCard = {
  skill_id: 'sk-low', name: 'safe-instruction', version: '1.0.0',
  domain: 'work', source: 'internal', license: 'MIT', package_hash: 'a'.repeat(12),
  gate_profile: 'instruction', signature_verified: false,
  capabilities: ['instruction:inline'], risk_level: 'low', risk_reasons: [],
  scan: { passed: true, risk_level: 'none', finding_count: 0, scanner_version: '1' },
  rating: { average_rating: 4.5, rating_count: 10, score: 4.3, distribution: { '5': 6, '4': 3, '3': 1, '2': 0, '1': 0 } },
};
const HIGH: PluginCard = {
  skill_id: 'sk-high', name: 'scary-plugin', version: '2.0.0',
  domain: 'personal', source: 'external', license: 'MIT', package_hash: 'b'.repeat(12),
  gate_profile: 'plugin', signature_verified: false,
  capabilities: ['materialize:files'], risk_level: 'high',
  risk_reasons: ['materializes_files', 'unsigned'],
  scan: { passed: false, risk_level: 'high', finding_count: 3, scanner_version: '1' },
  rating: { average_rating: 2.0, rating_count: 2, score: 2.5, distribution: { '5': 0, '4': 0, '3': 0, '2': 2, '1': 0 } },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(pluginsApi.list).mockResolvedValue({
    items: [LOW, HIGH], total: 2, limit: 20, offset: 0,
  });
  vi.mocked(pluginsApi.getHierarchy).mockResolvedValue(FAKE_HIERARCHY as any);
  vi.mocked(pluginsApi.listTemplates).mockResolvedValue({ items: FAKE_TEMPLATES as any, total: 1 });
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

describe('PluginMarketPage 评分体系 (A-工具市场-03)', () => {
  it('列表中展示星级徽标与贝叶斯综合分', async () => {
    render(<PluginMarketPage />);
    const badges = await screen.findAllByTestId('plugin-rating-badge');
    expect(badges.length).toBeGreaterThanOrEqual(1);
    expect(badges[0].textContent).toContain('★ 4.5');
    expect(badges[0].textContent).toContain('10评价');
  });

  it('切换排序方式调用带 sortBy 参数的 API', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    await screen.findAllByTestId('plugin-item');
    const sortSelect = screen.getByTestId('plugin-sort-select');
    await user.selectOptions(sortSelect, 'rating');
    await waitFor(() => {
      expect(pluginsApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ sortBy: 'rating' }),
      );
    });
  });

  it('在插件详情中展示评分直方图并支持打分提交', async () => {
    const user = userEvent.setup();
    vi.mocked(pluginsApi.rate).mockResolvedValue({
      summary: { average_rating: 4.8, rating_count: 11, score: 4.5 },
      rating: 5,
      comment: '非常好用的插件',
    });
    render(<PluginMarketPage />);
    await user.click(await screen.findByTestId('plugin-detail-sk-low'));
    expect(await screen.findByTestId('rating-section')).toBeInTheDocument();
    
    // 点击 5 星打分
    await user.click(screen.getByTestId('rate-star-5'));
    await user.type(screen.getByTestId('rate-comment-input'), '非常好用的插件');
    await user.click(screen.getByTestId('submit-rating-btn'));

    await waitFor(() => {
      expect(pluginsApi.rate).toHaveBeenCalledWith('sk-low', 5, '非常好用的插件');
    });
    expect(await screen.findByText(/评分成功！当前平均分：4.8/)).toBeInTheDocument();
  });
});

describe('PluginMarketPage 模板分层与一级技术入口 (A-开箱模板-04 🔒 GATE)', () => {
  it('顶栏常驻一级可见技术入口，点击直达技术层', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    const techBtn = screen.getByTestId('first-class-tech-entry');
    expect(techBtn).toBeInTheDocument();
    expect(techBtn.textContent).toContain('技术可拆入口');

    await user.click(techBtn);
    expect(await screen.findByTestId('technical-template-view')).toBeInTheDocument();
    expect(screen.getByText(/技术层 · 可拆解拓扑与代码模式/)).toBeInTheDocument();
    expect(screen.getByText(/受限 DSL 代码同源生成/)).toBeInTheDocument();
  });

  it('支持切换至新手默认层，展示开箱即用卡片', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    const noviceTab = screen.getByTestId('tab-templates-novice');
    await user.click(noviceTab);
    expect(await screen.findByTestId('novice-template-view')).toBeInTheDocument();
    expect(screen.getByText('小白模式 · 开箱即用')).toBeInTheDocument();
    expect(screen.getByTestId('novice-card-builtin:novice:general')).toBeInTheDocument();
  });

  it('支持切换至进阶场景流水线，展示场景筛选', async () => {
    const user = userEvent.setup();
    render(<PluginMarketPage />);
    const advTab = screen.getByTestId('tab-templates-advanced');
    await user.click(advTab);
    expect(await screen.findByTestId('advanced-template-view')).toBeInTheDocument();
    expect(screen.getByText('写作流水线')).toBeInTheDocument();
  });
});

describe('PluginMarketPage 模板导入导出与安全审查门禁 (A-开箱模板-06)', () => {
  it('点击导入按钮唤起安全审查模态框并执行安全审查与裁剪确认', async () => {
    const user = userEvent.setup();
    const fakeReview = {
      passed: false,
      risk_level: 'high' as const,
      checksum_verified: true,
      can_import: false,
      requested_tools: ['safe_tool', 'bash_eval'],
      dangerous_tools: ['bash_eval'],
      sensitive_tools: [],
      findings: [{ severity: 'high', code: 'dangerous_tool', message: '检测到高危工具 bash_eval' }],
      requires_user_confirmation: true,
    };
    vi.mocked(pluginsApi.securityReview).mockResolvedValue(fakeReview);
    vi.mocked(pluginsApi.importTemplate).mockResolvedValue({
      imported: true,
      template_id: 'imported:tmpl-1',
      name: '安全导入模板',
    });

    render(<PluginMarketPage />);
    const importBtn = screen.getByTestId('template-import-btn');
    await user.click(importBtn);

    expect(await screen.findByTestId('template-import-modal')).toBeInTheDocument();
    
    // 输入合法 JSON
    const textarea = screen.getByTestId('import-json-textarea');
    fireEvent.change(textarea, { target: { value: '{"template_id":"test"}' } });

    // 执行安全审查
    await user.click(screen.getByTestId('run-security-review-btn'));
    await waitFor(() => {
      expect(pluginsApi.securityReview).toHaveBeenCalledWith({ template_id: 'test' });
    });

    // 审查报告展示高危警告
    expect(await screen.findByTestId('security-report-panel')).toBeInTheDocument();
    expect(screen.getByText(/申请了高危工具：bash_eval/)).toBeInTheDocument();

    // 确认裁剪并导入
    await user.click(screen.getByTestId('confirm-import-btn'));
    await waitFor(() => {
      expect(pluginsApi.importTemplate).toHaveBeenCalledWith(
        { template_id: 'test' },
        ['safe_tool'],
      );
    });
    expect(await screen.findByText(/导入成功！模板「安全导入模板」已安全落地。/)).toBeInTheDocument();
  });
});

