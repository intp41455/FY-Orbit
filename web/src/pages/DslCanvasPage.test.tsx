import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// DslCanvasPage 挂两个子组件；这里只关心页签接线与「生成结果流进画布」这条链。
// 原有 DSL 文本模式的 DslCanvas 单独 mock，避免把它的内部实现卷进来。
vi.mock('../components/dsl/DslCanvas', () => ({
  DslCanvas: () => <div data-testid="dsl-canvas-stub" />,
}));
vi.mock('../api/workflowGen', () => ({
  workflowGenApi: { status: vi.fn(), generate: vi.fn() },
}));
vi.mock('../api/dslCanvas', async (importOriginal) => ({
  // B5 · 单一真源：展开真模块，保留 DSL_NODE_TYPES 等常量不被 mock 掉，
  // 只替换网络面 dslCanvasApi。
  ...(await importOriginal<typeof import('../api/dslCanvas')>()),
  dslCanvasApi: {
    validate: vi.fn(), schema: vi.fn(),
    modes: vi.fn(), run: vi.fn(), validateIr: vi.fn(),
  },
}));

import { workflowGenApi } from '../api/workflowGen';
import { dslCanvasApi } from '../api/dslCanvas';
import { DslCanvasPage } from './DslCanvasPage';
import type { DslDocument } from '../api/dslCanvas';

const VALID_DSL: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '示例' }] } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

/** 后端 `/api/dsl/modes` 给的起手模板（真正的那张图，不是前端自编的）。 */
const TEMPLATE_DSL: DslDocument = {
  version: '1',
  nodes: [
    { id: 'src', type: 'input', params: { kind: 'literal', value: [{ text: '甲' }] } },
    { id: 'out', type: 'output', params: { format: 'json' } },
  ],
  edges: [{ from: 'src', to: 'out' }],
};

const MODES = [
  {
    mode: 'beginner' as const, label: '小白（画布模板）',
    entries: [{ mode: 'beginner' as const, entry_id: 'canvas', label: '画布搭建器', description: '拖拽画布 + 受限动词面板（同源 DSL）', builtin: true }],
    templates: [{ template_id: 'beginner-starter', mode: 'beginner' as const, label: '起手：筛选非空行', description: 'input → filter → map → output', dsl: TEMPLATE_DSL }],
    default_template: 'beginner-starter',
  },
  {
    mode: 'technical' as const, label: '技术（代码 SDK）',
    entries: [{ mode: 'technical' as const, entry_id: 'code-sdk', label: '代码优先 SDK', description: 'GraphBuilder 代码定义图', builtin: true }],
    templates: [], default_template: 'technical-pipeline',
  },
  {
    mode: 'enterprise' as const, label: '企业（治理接入）',
    entries: [{ mode: 'enterprise' as const, entry_id: 'governed-canvas', label: '治理画布', description: 'approval 动词 + 治理接入', builtin: true }],
    templates: [], default_template: 'enterprise-governed',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workflowGenApi.status).mockResolvedValue({ model_configured: true });
  vi.mocked(workflowGenApi.generate).mockResolvedValue({
    dsl: VALID_DSL,
    model: 'gpt-4o-mini',
    provider_id: 'deepseek',
    attempts: [{ attempt: 1, ok: true, error: '' }],
    usage: {},
    settled_usd: '0.0001',
    degraded_from: '',
    degraded_reason: '',
  });
  vi.mocked(dslCanvasApi.schema).mockResolvedValue({
    schema: {}, node_types: ['input', 'transform', 'output'],
    transform_verbs: ['map', 'filter', 'template', 'branch', 'aggregate', 'merge', 'agent', 'confirm', 'artifact'],
    verb_catalog: [],
    aggregate_ops: ['count', 'sum', 'min', 'max', 'avg', 'first', 'last', 'join', 'unique'],
    merge_ops: ['concat', 'first', 'last'],
    output_formats: ['json', 'text'],
  });
  vi.mocked(dslCanvasApi.modes).mockResolvedValue({ modes: MODES });
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
});

/** 切到技术模式：代码视图 / 手改 DSL / 导出都只在非小白模式下出现。 */
async function toTechnical(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(await screen.findByTestId('mode-tab-technical'));
}

describe('DslCanvasPage 工作流工坊页签接线', () => {
  it('默认进入「工坊模式」，同时挂载生成器与拖拽画布（三视图合一）', async () => {
    render(<DslCanvasPage />);
    expect(screen.getByTestId('workshop-panel')).toBeInTheDocument();
    expect(screen.getByTestId('flow-generate')).toBeInTheDocument();
    expect(await screen.findByTestId('flow-editor-root')).toBeInTheDocument();
    // 原有 DSL 文本模式此时不挂载
    expect(screen.queryByTestId('dsl-canvas-stub')).toBeNull();
    expect(screen.getByTestId('tab-workshop')).toHaveAttribute('aria-selected', 'true');
  });

  it('切到「DSL 文本模式」：原 DslCanvas 完整保留，不是被删掉', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(screen.getByTestId('tab-text'));
    expect(screen.getByTestId('text-panel')).toBeInTheDocument();
    expect(screen.getByTestId('dsl-canvas-stub')).toBeInTheDocument();
    // 工坊模式的组件已卸载
    expect(screen.queryByTestId('flow-editor-root')).toBeNull();
    expect(screen.getByTestId('tab-text')).toHaveAttribute('aria-selected', 'true');
  });

  it('页签可来回切换，不锁死', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(screen.getByTestId('tab-text'));
    await user.click(screen.getByTestId('tab-workshop'));
    expect(screen.getByTestId('workshop-panel')).toBeInTheDocument();
    expect(screen.getByTestId('tab-workshop')).toHaveAttribute('aria-selected', 'true');
  });

  it('「一句话生成」成功 → 真实 DSL 载入工坊画布，两个视图数据互通', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-model-state');
    await toTechnical(user);

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    // 生成面板 → 画布：节点真的出现了
    expect(await screen.findByTestId('flow-node-in1')).toBeInTheDocument();
    expect(screen.getByTestId('flow-node-out1')).toBeInTheDocument();
    // 代码视图与画布同源，且不含布局坐标
    const code = JSON.parse(screen.getByTestId('flow-code').textContent ?? '{}');
    expect(code).toEqual(VALID_DSL);
    await waitFor(() => expect(screen.getByTestId('flow-export')).toBeEnabled());
  });

  it('生成失败（503）时画布保持空白，不塞任何兜底 DSL', async () => {
    const user = userEvent.setup();
    const { ApiError } = await import('../api/client');
    vi.mocked(workflowGenApi.generate).mockRejectedValue(
      new ApiError(503, { code: 'model_not_configured', message: 'No model provider is configured.' }, 'fb'),
    );
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-model-state');
    await toTechnical(user);

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));

    await screen.findByTestId('flow-generate-error');
    // 关键诚实断言：没有节点、没有连线、代码视图是空图
    expect(screen.queryByTestId(/^flow-node-/)).toBeNull();
    expect(screen.queryByTestId(/^flow-edge-/)).toBeNull();
    const code = JSON.parse(screen.getByTestId('flow-code').textContent ?? '{}');
    expect(code).toEqual({ version: '1', nodes: [], edges: [] });
  });
});

describe('A-三重模式-01/04 · 模式壳与小白起手', () => {
  it('三模式可见，默认落在小白且不展示技术层复杂度', async () => {
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    expect(await screen.findByTestId('mode-tab-beginner')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('mode-tab-technical')).toBeInTheDocument();
    expect(screen.getByTestId('mode-tab-enterprise')).toBeInTheDocument();

    // 小白模式：代码视图 / 手改 DSL / 导出都不在（藏的是「怎么读图」）
    expect(screen.queryByTestId('flow-code')).toBeNull();
    expect(screen.queryByTestId('flow-export')).toBeNull();
    expect(screen.queryByTestId('flow-dsl-input')).toBeNull();
    // 但节点面板照旧（16 类可拖这件事不受模式影响）
    expect(screen.getByTestId('flow-palette')).toBeInTheDocument();
  });

  it('切到技术模式：代码视图 / 手改 / 导出回来，图本身不变', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await toTechnical(user);
    expect(screen.getByTestId('flow-code')).toBeInTheDocument();
    expect(screen.getByTestId('flow-export')).toBeInTheDocument();
    expect(screen.getByTestId('flow-dsl-input')).toBeInTheDocument();
    expect(screen.queryByTestId('template-starter')).toBeNull();
  });

  it('模式清单拉不到时如实报错，仍停在当前模式并能编辑', async () => {
    vi.mocked(dslCanvasApi.modes).mockRejectedValue(new Error('boom'));
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    expect(await screen.findByTestId('mode-error')).toBeInTheDocument();
    expect(screen.queryByTestId('mode-tab-beginner')).toBeNull();
    expect(screen.getByTestId('flow-palette')).toBeInTheDocument();
  });

  it('小白全流程：模板起手 → 装配 → 运行（图就是后端给的模板图）', async () => {
    const user = userEvent.setup();
    vi.mocked(dslCanvasApi.run).mockResolvedValue({
      run_id: 'run-1', status: 'succeeded', dsl: TEMPLATE_DSL,
      output: [{ text: '甲' }], error: null, execution_id: null,
      created_at: '2026-10-07T10:00:00Z', logs: [],
    });
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    // 起手模板默认**不自动载入**（不替用户丢掉他刚搭的图）
    expect(screen.queryByTestId(/^flow-node-/)).toBeNull();
    await user.click(await screen.findByTestId('template-beginner-starter'));

    // 装配：模板图真的落到了画布上
    expect(await screen.findByTestId('flow-node-src')).toBeInTheDocument();
    expect(screen.getByTestId('flow-node-out')).toBeInTheDocument();

    // 运行：小白也能跑到底，结果如实展示
    await user.click(screen.getByTestId('flow-run'));
    expect(await screen.findByTestId('flow-run-ok')).toBeInTheDocument();
    expect(screen.getByTestId('flow-run-output').textContent).toContain('甲');
    expect(vi.mocked(dslCanvasApi.run).mock.calls[0][0]).toEqual(TEMPLATE_DSL);
  });

  it('运行失败如实报错，不假装跑通', async () => {
    const user = userEvent.setup();
    const { ApiError } = await import('../api/client');
    vi.mocked(dslCanvasApi.run).mockRejectedValue(
      new ApiError(422, { code: 'dsl_invalid', message: '节点 miss 无上游输入' }, 'fb'),
    );
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(await screen.findByTestId('template-beginner-starter'));
    await screen.findByTestId('flow-node-src');
    await user.click(screen.getByTestId('flow-run'));

    expect(await screen.findByTestId('flow-run-error')).toHaveTextContent('节点 miss 无上游输入');
    expect(screen.queryByTestId('flow-run-ok')).toBeNull();
  });
});
