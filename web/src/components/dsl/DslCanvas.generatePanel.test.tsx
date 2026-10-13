/**
 * FY-N4 · G4-f：「生成 DSL」按钮名副其实（不再是误导性空壳）。
 *
 * 背景（差距清单 §9.2 G4-f）：`DslCanvas.generate()` 的函数体只有一句
 * `setError(null)`，而代码视图的 JSON 早已由 `nodes/edges` 实时派生——
 * 于是这个按钮**点与不点没有任何区别**，名字却承诺了「生成」，属误导性空壳。
 *
 * 本文件钉死修好之后必须成立的契约：
 *  1. 按钮**打开一句话生成面板**（默认收起，点开才挂载），并把焦点交给需求输入框；
 *  2. 面板产出的 DSL **真的载入 text 画布**（`dsl-json` 立刻变成后端返回的那份）；
 *  3. 生成走**后端真实接口**（`workflowGenApi.generate`），不是前端自编模板；
 *  4. 旧语义不丢：点按钮仍清掉上一次的运行错误提示；
 *  5. 诚实契约在 text 面板同样成立：503 不用兜底 DSL 冒充成功，画布保持原样。
 *
 * 同时反向钉死同源（N4 核心）：**按钮不得成为同步闸门**——
 * 展开面板这一步不允许改变代码视图里那份 JSON。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // 单一真源：展开真模块保留 DSL_NODE_TYPES / DSL_TRANSFORM_VERBS 等常量，
  // 只替换网络面 dslCanvasApi。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: {
    run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn(),
    validateIr: vi.fn(),
  },
}));

// G4-f：面板会真的去问后端「模型配没配」，不 mock 就会打真实 fetch。
vi.mock('../../api/workflowGen', () => ({
  workflowGenApi: { status: vi.fn(), generate: vi.fn() },
}));

import { DslCanvas } from './DslCanvas';
import { dslCanvasApi } from '../../api/dslCanvas';
import { workflowGenApi } from '../../api/workflowGen';
import { ApiError } from '../../api/client';
import type { DslDocument } from '../../api/dslCanvas';

/** 后端 `/api/workflow/generate` 真实返回的那份 DSL（不是前端自编的）。 */
const GENERATED_DSL: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '示例' }] } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [{ from: 'in1', to: 'out1' }],
};

const readDsl = (): DslDocument =>
  JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
  vi.mocked(dslCanvasApi.run).mockResolvedValue({
    run_id: 'run-1', status: 'succeeded',
    dsl: { version: '1', nodes: [], edges: [] },
    output: 'ok', error: null, execution_id: null,
    created_at: '2026-10-13T00:00:00Z', logs: [],
  });
  vi.mocked(workflowGenApi.status).mockResolvedValue({ model_configured: true });
  vi.mocked(workflowGenApi.generate).mockResolvedValue({
    dsl: GENERATED_DSL,
    model: 'gpt-4o-mini',
    provider_id: 'deepseek',
    attempts: [{ attempt: 1, ok: true, error: '' }],
    usage: {},
    settled_usd: '0.0001',
    degraded_from: '',
    degraded_reason: '',
  });
});

describe('G4-f · 「生成 DSL」按钮打开一句话生成面板（不再是空壳）', () => {
  it('默认收起面板；点按钮才挂载，并把焦点交给需求输入框', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    // 未点之前不占用一整块屏，也没有多打一次「模型状态」请求
    expect(screen.queryByTestId('canvas-generate')).toBeNull();
    expect(workflowGenApi.status).not.toHaveBeenCalled();

    await user.click(screen.getByTestId('dsl-generate'));

    expect(await screen.findByTestId('canvas-generate')).toBeInTheDocument();
    // 「打开」的另一半：焦点真的落在需求输入框上，用户可以直接打字
    await waitFor(() => expect(screen.getByTestId('canvas-generate-prompt')).toHaveFocus());
    // 展开后如实去问后端模型状态
    await waitFor(() => expect(workflowGenApi.status).toHaveBeenCalled());
  });

  it('面板生成的 DSL 真的载入 text 画布（代码视图立刻变成后端那份）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    await user.click(screen.getByTestId('dsl-generate'));
    await screen.findByTestId('canvas-generate-prompt');
    await user.type(screen.getByTestId('canvas-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('canvas-generate-run'));

    // 走的是后端真实接口，且带上了用户原话
    await waitFor(() => expect(workflowGenApi.generate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(workflowGenApi.generate).mock.calls[0][0]).toBe('每天读文件并发邮件');

    // 生成结果进了画布（画布节点 testid 与代码视图必须指向同一份）
    expect(await screen.findByTestId('dsl-node-in1')).toBeInTheDocument();
    expect(screen.getByTestId('dsl-node-out1')).toBeInTheDocument();
    expect(readDsl()).toEqual(GENERATED_DSL);
  });

  it('展开面板这一步不碰代码视图（同源闸门不被按钮重新引入）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    const before = screen.getByTestId('dsl-json').textContent;
    await user.click(screen.getByTestId('dsl-generate'));
    await screen.findByTestId('canvas-generate');
    expect(screen.getByTestId('dsl-json').textContent).toBe(before);
  });

  it('旧语义保留：点按钮仍清掉上一次的运行错误提示', async () => {
    const user = userEvent.setup();
    vi.mocked(dslCanvasApi.run).mockRejectedValue(new Error('后端炸了'));
    render(<DslCanvas />);

    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('dsl-run'));
    expect(await screen.findByText('后端炸了')).toBeInTheDocument();

    await user.click(screen.getByTestId('dsl-generate'));
    await waitFor(() => expect(screen.queryByText('后端炸了')).toBeNull());
  });

  it('503 未配置模型：如实报错，绝不用兜底 DSL 冒充生成成功', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.generate).mockRejectedValue(
      new ApiError(503, { code: 'model_not_configured', message: 'No model provider is configured.' }, 'fb'),
    );
    render(<DslCanvas />);

    await user.click(screen.getByTestId('dsl-generate'));
    await screen.findByTestId('canvas-generate-prompt');
    await user.type(screen.getByTestId('canvas-generate-prompt'), '每天读文件并发邮件');
    await user.click(screen.getByTestId('canvas-generate-run'));

    expect(await screen.findByTestId('canvas-generate-error')).toHaveTextContent('No model provider is configured.');
    // 画布仍是原来那张（空图），一个节点都没多
    expect(screen.queryByTestId('dsl-node-in1')).toBeNull();
    expect(readDsl()).toEqual({ version: '1', nodes: [], edges: [] });
  });
});