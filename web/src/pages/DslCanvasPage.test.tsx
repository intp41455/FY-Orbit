import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// DslCanvasPage 同时挂载 FlowEditor 与 DslCanvas 两个画布（共享同一条状态通道），
// 这里直接用真实的 DslCanvas：页签接线与「两个画布同源」都要看它的真实输出
// （dsl-json / dsl-node-* / delete-*），stub 掉就测不到同源了。
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
    // N3 切片 3：页面级草稿持久化会真的调这三个方法，缺一个就报 TypeError。
    getFlow: vi.fn(), saveDraft: vi.fn(), createFlow: vi.fn(),
    // G4-h：运行记录面板读 runs 列表（默认空，各用例按需覆盖）。
    listRuns: vi.fn(),
  },
}));
vi.mock('../api/dslRunStream', () => ({
  openDslRunStream: vi.fn(() => ({ close: vi.fn() })),
}));

import { workflowGenApi } from '../api/workflowGen';
import { dslCanvasApi } from '../api/dslCanvas';
import { openDslRunStream } from '../api/dslRunStream';
import { writeDslHandoff } from '../api/dslHandoff';
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
  vi.mocked(dslCanvasApi.listRuns).mockResolvedValue({ items: [] });
  // N3 切片 3：草稿通道默认成功路径（用例要冲突时单独覆盖）。
  sessionStorage.clear();
  vi.mocked(dslCanvasApi.createFlow).mockResolvedValue({
    id: 'flow-1', flow_type: 'workflow', draft_doc: null,
    published_version: 0, draft_version: 1,
  });
  vi.mocked(dslCanvasApi.saveDraft).mockResolvedValue({
    id: 'flow-1', flow_type: 'workflow', draft_doc: null,
    published_version: 0, draft_version: 2,
  });
  vi.mocked(dslCanvasApi.getFlow).mockResolvedValue({
    id: 'flow-1', flow_type: 'workflow', draft_doc: null,
    published_version: 0, draft_version: 3,
  });
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
    // 改动后两个画布**同时挂载**（只是可见性切换），文本画布已在 DOM 里但不可见
    expect(screen.getByTestId('dsl-canvas-root')).toBeInTheDocument();
    expect(screen.getByTestId('text-panel')).not.toBeVisible();
    expect(screen.getByTestId('tab-workshop')).toHaveAttribute('aria-selected', 'true');
  });

  it('切到「DSL 文本模式」：原 DslCanvas 完整保留，不是被删掉', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(screen.getByTestId('tab-text'));
    expect(screen.getByTestId('text-panel')).toBeInTheDocument();
    expect(screen.getByTestId('dsl-canvas-root')).toBeInTheDocument();
    // 工坊画布**不再卸载**（否则切页签就丢图），只切可见性
    expect(screen.getByTestId('flow-editor-root')).toBeInTheDocument();
    expect(screen.getByTestId('workshop-panel')).not.toBeVisible();
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
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    expect(await screen.findByTestId('mode-tab-beginner')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('mode-tab-technical')).toBeInTheDocument();
    expect(screen.getByTestId('mode-tab-enterprise')).toBeInTheDocument();

    // G4-i · 小白模式：代码视图 / 手改 DSL / 导出**收进折叠区**（不是卸载）。
    // 折叠而非卸载是硬约束：N3 那条 beginner 同源断言（dslDoc() 读 dsl-json）
    // 也跑在小白模式下，卸载会把它打红。
    expect(screen.getByTestId('flow-code')).not.toBeVisible();
    expect(screen.getByTestId('flow-export')).not.toBeVisible();
    expect(screen.getByTestId('flow-dsl-input')).not.toBeVisible();
    expect(screen.getByTestId('flow-code')).toBeInTheDocument();
    // 点开折叠区 → 三者原样回来
    await user.click(screen.getByTestId('flow-code-summary'));
    expect(screen.getByTestId('flow-code')).toBeVisible();
    expect(screen.getByTestId('flow-export')).toBeVisible();
    expect(screen.getByTestId('flow-dsl-input')).toBeVisible();
    // 但节点面板照旧（18 类可拖这件事不受模式影响）
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
    // G4-i：技术模式不套折叠壳（就是原来那张常开卡片，DOM 结构零变化）
    expect(screen.getByTestId('flow-code')).toBeVisible();
    expect(screen.queryByTestId('flow-code-summary')).toBeNull();
  });

  it('G4-i · R10：小白模式下文本页签那份同源 JSON 也收起，不给绕过口子', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    // 切到「DSL 文本模式」——同一个小白用户、同一个页面，
    // 工坊画布藏了技术面，文本画布不能明晃晃摊着同一份 JSON。
    await user.click(screen.getByTestId('tab-text'));
    expect(screen.getByTestId('dsl-json')).not.toBeVisible();

    // 点开折叠区 → 可见，且内容与工坊画布代码视图逐字段相同（同源没被折叠打断）
    await user.click(screen.getByTestId('dsl-json-summary'));
    expect(screen.getByTestId('dsl-json')).toBeVisible();
    const fromTextCanvas = JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');
    expect(fromTextCanvas).toEqual({ version: '1', nodes: [], edges: [] });
  });

  it('G4-i · R10：切到技术模式后文本页签的 JSON 恢复可见', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(screen.getByTestId('tab-text'));
    await toTechnical(user);
    expect(screen.getByTestId('dsl-json')).toBeVisible();
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

  it('运行挂起如实显示为「等裁决」，不报成失败（202 也是成功分支）', async () => {
    const user = userEvent.setup();
    // `/api/dsl-canvas/runs` 走到 confirm|approval 且无人工裁决时返回 202 +
    // 挂起载荷。它是 res.ok，`request()` 会把 body 原样交回——前端若当成失败，
    // 企业模式的 approval 节点就会被红字误报成「执行失败」。
    vi.mocked(dslCanvasApi.run).mockResolvedValue({
      status: 'suspended',
      suspended: {
        checkpoint: 'cp-1', dsl_digest: 'sha256:abc',
        context: { prompt: '是否写入长期记忆？', role: 'owner', node_id: 'govern' },
        options: [{ value: 'approve', label: '同意' }], node_id: 'govern',
      },
    } as never);
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(await screen.findByTestId('template-beginner-starter'));
    await screen.findByTestId('flow-node-src');
    await user.click(screen.getByTestId('flow-run'));

    expect(await screen.findByTestId('flow-run-suspended')).toHaveTextContent('等待人工裁决');
    expect(screen.getByTestId('flow-run-suspended')).toHaveTextContent('是否写入长期记忆？');
    expect(screen.queryByTestId('flow-run-error')).toBeNull();
    expect(screen.queryByTestId('flow-run-ok')).toBeNull();
  });
});

/**
 * N3 状态同步 · 切片 2（修 G1）：两个 DSL 画布共享同一条状态通道。
 * 两个画布**同时挂载**（页签只切可见性），文档由页面单一持有，
 * 子组件通过 initialDoc 载入 / onDocChange 回传。
 */
describe('N3 · 两个画布共享同一条状态通道', () => {
  /** FlowEditor 的代码视图是 textarea，取 value；DslCanvas 的是 pre，取 textContent。 */
  const flowDoc = (): DslDocument =>
    JSON.parse((screen.getByTestId('flow-code') as HTMLTextAreaElement).value);
  const dslDoc = (): DslDocument =>
    JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');

  it('切到 text 页签后两个面板的 DSL 代码视图逐字段一致（同源）', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-model-state');
    await toTechnical(user);

    await user.type(screen.getByTestId('flow-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('flow-generate-run'));
    expect(await screen.findByTestId('flow-node-in1')).toBeInTheDocument();

    // 切页签前后，文本画布拿到的必须是同一份文档
    await user.click(screen.getByTestId('tab-text'));
    expect(dslDoc()).toEqual(VALID_DSL);
    expect(dslDoc()).toEqual(flowDoc());
  });

  it('在 text 模式删掉节点，切回 workshop 后工坊画布也少了该节点', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(await screen.findByTestId('template-beginner-starter'));
    expect(await screen.findByTestId('flow-node-src')).toBeInTheDocument();

    await user.click(screen.getByTestId('tab-text'));
    await screen.findByTestId('dsl-node-src');
    await user.click(screen.getByTestId('delete-src'));
    await waitFor(() => expect(screen.queryByTestId('dsl-node-src')).toBeNull());

    // 共享通道生效：工坊画布同步收缩，而不是各自留一份
    await user.click(screen.getByTestId('tab-workshop'));
    await waitFor(() => expect(screen.queryByTestId('flow-node-src')).toBeNull());
    expect(screen.getByTestId('flow-node-out')).toBeInTheDocument();
  });

  it('应用模板后两个画布同时呈现该模板（不是只有一个）', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    await user.click(await screen.findByTestId('template-beginner-starter'));
    expect(await screen.findByTestId('flow-node-src')).toBeInTheDocument();

    // 不切页签也能在文本画布看到同一张图
    expect(await screen.findByTestId('dsl-node-src')).toBeInTheDocument();
    expect(screen.getByTestId('dsl-node-out')).toBeInTheDocument();
    expect(dslDoc()).toEqual(TEMPLATE_DSL);
  });
});

/**
 * N3 状态同步 · 切片 3（前端 G4）：草稿持久化 + 乐观锁 + 消除 UI 假绿灯。
 *
 * 这里测的是**用户能看到什么**（页面壳层）：
 *   - 未落盘前指示器必须说「未接入持久化」，不得显示存储正常 / 已保存；
 *   - 409 冲突时横幅必须出现，且给「重新读取 / 覆盖保存」两个出口；
 *   - 冲突后不得再自动发保存（不静默覆盖）。
 */
describe('N3 · 草稿持久化与版本冲突的用户可见行为', () => {
  it('未落盘时保存指示器不是绿灯：显示「未接入持久化」且不说「已保存」', async () => {
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    // 关键诚实断言：一个字节都没写出去过，就不许声称存储正常。
    expect(screen.getByTestId('save-unlinked-badge')).toHaveTextContent('未接入持久化');
    expect(screen.getByRole('status')).toHaveTextContent('无待保存内容');
    expect(screen.getByRole('status')).not.toHaveTextContent('已保存');
  });

  it('模板起手后落盘成功：请求带 expected_version，且存储状态转 ok', async () => {
    const user = userEvent.setup();
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(await screen.findByTestId('template-beginner-starter'));
    expect(await screen.findByTestId('flow-node-src')).toBeInTheDocument();

    await waitFor(() => expect(dslCanvasApi.saveDraft).toHaveBeenCalled(), { timeout: 4000 });
    const call = vi.mocked(dslCanvasApi.saveDraft).mock.calls[0];
    expect(call[0]).toBe('flow-1');
    expect(call[2]).toEqual({ expected_version: 1 });
    await waitFor(() => expect(screen.queryByTestId('save-unlinked-badge')).toBeNull());
  });

  it('版本冲突（409）→ 横幅可见 + 两个出口，且不再自动重试覆盖', async () => {
    const user = userEvent.setup();
    const { DslDraftConflictError } = await import('../api/dslCanvas');
    vi.mocked(dslCanvasApi.saveDraft).mockRejectedValue(new DslDraftConflictError('flow-1'));

    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(await screen.findByTestId('template-beginner-starter'));

    const banner = await screen.findByTestId('draft-conflict', {}, { timeout: 4000 });
    expect(banner).toHaveTextContent('草稿已被其他会话修改，需重新读取后再保存');
    expect(screen.getByTestId('draft-reload')).toBeInTheDocument();
    expect(screen.getByTestId('draft-overwrite')).toBeInTheDocument();

    // 冲突后再等一段时间：一次都不许再发保存（绝不静默覆盖）
    const before = vi.mocked(dslCanvasApi.saveDraft).mock.calls.length;
    await new Promise((r) => { setTimeout(r, 1200); });
    expect(vi.mocked(dslCanvasApi.saveDraft)).toHaveBeenCalledTimes(before);
  });

  it('冲突后点「覆盖保存」：发出一次不带 expected_version 的写入并解除冲突', async () => {
    const user = userEvent.setup();
    const { DslDraftConflictError } = await import('../api/dslCanvas');
    vi.mocked(dslCanvasApi.saveDraft)
      .mockRejectedValueOnce(new DslDraftConflictError('flow-1'))
      .mockResolvedValue({
        id: 'flow-1', flow_type: 'workflow', draft_doc: null,
        published_version: 0, draft_version: 8,
      });

    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(await screen.findByTestId('template-beginner-starter'));
    await screen.findByTestId('draft-conflict', {}, { timeout: 4000 });

    await user.click(screen.getByTestId('draft-overwrite'));

    await waitFor(() => expect(screen.queryByTestId('draft-conflict')).toBeNull());
    const last = vi.mocked(dslCanvasApi.saveDraft).mock.calls.at(-1);
    expect(last?.[2]?.expected_version).toBeUndefined();
  });

  it('冲突后点「重新读取」：读回服务端版本并解除冲突', async () => {
    const user = userEvent.setup();
    const { DslDraftConflictError } = await import('../api/dslCanvas');
    vi.mocked(dslCanvasApi.saveDraft).mockRejectedValue(new DslDraftConflictError('flow-1'));
    vi.mocked(dslCanvasApi.getFlow).mockResolvedValue({
      id: 'flow-1', flow_type: 'workflow', draft_doc: null,
      published_version: 0, draft_version: 11,
    });

    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    await user.click(await screen.findByTestId('template-beginner-starter'));
    await screen.findByTestId('draft-conflict', {}, { timeout: 4000 });

    await user.click(screen.getByTestId('draft-reload'));

    await waitFor(() => expect(screen.queryByTestId('draft-conflict')).toBeNull());
    expect(dslCanvasApi.getFlow).toHaveBeenCalledWith('flow-1');
  });

  it('草稿读取失败时如实报错，不静默', async () => {
    vi.mocked(dslCanvasApi.getFlow).mockRejectedValue(new Error('读不到草稿'));
    sessionStorage.setItem('fy-orbit.dsl-draft.flow-id', 'flow-1');

    render(<DslCanvasPage />);

    expect(await screen.findByTestId('draft-error')).toHaveTextContent('读取草稿失败');
    expect(screen.getByTestId('draft-error')).toHaveTextContent('读不到草稿');
  });

  it('G4-k：开箱模板交接的文档在挂载时载入工坊（一次性消费）', async () => {
    const user = userEvent.setup();
    const handoffDoc: DslDocument = {
      version: '1',
      nodes: [
        { id: 'tpl-in', type: 'input', params: { kind: 'literal', value: 't' } },
        { id: 'tpl-out', type: 'output', params: { format: 'text' } },
      ],
      edges: [{ from: 'tpl-in', to: 'tpl-out' }],
    };
    writeDslHandoff(handoffDoc, '开箱模板:tpl-1');

    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    // 文本页签的同源 JSON 里出现交接文档的节点（不是默认空图）。
    await user.click(screen.getByTestId('tab-text'));
    await user.click(screen.getByTestId('dsl-json-summary'));
    const loaded = JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');
    expect(loaded.nodes.map((n: { id: string }) => n.id)).toEqual(['tpl-in', 'tpl-out']);
    // 一次性消费：刷新不再重复载入（键已删）。
    expect(sessionStorage.getItem('fy-orbit.dsl-handoff')).toBeNull();
  });

  it('G4-l：起手模板与开箱模板/团队模板名实区分', async () => {
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');
    // 工坊里的模板永远叫“起手模板”，不与开箱模板、团队模板混名。
    expect(screen.getByTestId('template-starter')).toHaveTextContent('从起手模板开始');
    expect(screen.getByTestId('template-starter')).toHaveTextContent('开箱模板');
  });

  it('G4-h：运行记录列出状态，附着后收事件流', async () => {
    const user = userEvent.setup();
    vi.mocked(dslCanvasApi.listRuns).mockResolvedValue({
      items: [
        { run_id: 'run-aaa111', status: 'succeeded' },
        { run_id: 'run-bbb222', status: 'failed' },
      ],
    });
    let handlers: { onNodeStarted?: (e: { node_id: string }) => void } = {};
    vi.mocked(openDslRunStream).mockImplementation(((_runId: string, h: never) => {
      handlers = h as never;
      return { close: vi.fn() };
    }) as never);
    render(<DslCanvasPage />);
    await screen.findByTestId('flow-editor-root');

    // 列表带状态。
    expect(await screen.findByTestId('run-history')).toBeInTheDocument();
    expect(screen.getByText('run-aaa111'.slice(0, 12))).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();

    // 附着 → openDslRunStream 被 run_id 调用；模拟节点事件 → feed 出现。
    await user.click(screen.getByTestId('run-attach-run-aaa1'));
    await waitFor(() => expect(openDslRunStream).toHaveBeenCalledWith(
      'run-aaa111', expect.anything(),
    ));
    handlers.onNodeStarted!({ node_id: 't' });
    await waitFor(() =>
      expect(screen.getByTestId('run-feed')).toHaveTextContent('t 开始'),
    );

    // 断开 → close 被调。
    await user.click(screen.getByTestId('run-detach'));
    expect(screen.queryByTestId('run-feed')).toBeNull();
  });
});
