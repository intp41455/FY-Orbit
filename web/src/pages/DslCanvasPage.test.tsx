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
vi.mock('../api/dslCanvas', () => ({
  dslCanvasApi: { validate: vi.fn(), schema: vi.fn() },
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
    schema: {}, node_types: ['input', 'transform', 'output'], transform_verbs: ['map', 'filter', 'template'],
  });
});

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
