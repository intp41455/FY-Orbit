import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DslCanvas } from './DslCanvas';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // B5 · 单一真源：展开真模块，保留 DSL_TRANSFORM_VERBS 等常量不被 mock 掉，
  // 只替换网络面 dslCanvasApi。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: {
    run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn(),
    validateIr: vi.fn(), // P1 · 收集式 IR 校验
  },
}));

import { dslCanvasApi } from '../../api/dslCanvas';

/** 节点 id 跨测试递增，用前缀正则定位端口/删除按钮。 */
const port = (dir: 'in' | 'out', type: string) =>
  screen.getByTestId(new RegExp(`^port-${dir}-${type}`));
const delBtn = (type: string) =>
  screen.getByTestId(new RegExp(`^delete-${type}`));

describe('DslCanvas 拖拽生成 DSL → 执行', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // P1 · 默认 IR 校验通过（个别用例自行覆盖返回值）
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({
      valid: true, diagnostics: [],
    });
    vi.mocked(dslCanvasApi.run).mockResolvedValue({
      run_id: 'run-1',
      status: 'succeeded',
      dsl: { version: '1', nodes: [], edges: [] },
      output: '你好，张三！你今年 34 岁。',
      error: null,
      execution_id: null,
      created_at: '2026-10-03T00:00:00Z',
      logs: [
        { node_id: 'input1', node_type: 'input', verb: null, status: 'succeeded',
          input: null, output: [{ name: '张三', age: 34 }], error: null,
          started_at: '', finished_at: '' },
        { node_id: 'transform1', node_type: 'transform', verb: 'template', status: 'succeeded',
          input: [{ name: '张三', age: 34 }], output: ['你好，张三！你今年 34 岁。'], error: null,
          started_at: '', finished_at: '' },
        { node_id: 'output1', node_type: 'output', verb: null, status: 'succeeded',
          input: ['你好，张三！你今年 34 岁。'], output: '你好，张三！你今年 34 岁。', error: null,
          started_at: '', finished_at: '' },
      ],
    });
  });

  it('添加三类节点并连线后，生成 DSL 只含模型不含布局坐标', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    // 点击面板依次添加三类节点（拖拽在 E2E 中验证）
    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('palette-transform'));
    await user.click(screen.getByTestId('palette-output'));

    // 连线：input1 出 → transform1 入；transform1 出 → output1 入
    await user.click(port('out', 'input'));
    await user.click(port('in', 'transform'));
    await user.click(port('out', 'transform'));
    await user.click(port('in', 'output'));

    await user.click(screen.getByTestId('dsl-generate'));

    const dsl = JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');
    expect(dsl.version).toBe('1');
    const ids = dsl.nodes.map((n: { id: string }) => n.id);
    expect(ids[0]).toMatch(/^input/);
    expect(ids[1]).toMatch(/^transform/);
    expect(ids[2]).toMatch(/^output/);
    expect(dsl.nodes[1].verb).toBe('template');
    const [tid, oid] = [dsl.nodes[1].id, dsl.nodes[2].id];
    expect(dsl.edges).toEqual([
      { from: dsl.nodes[0].id, to: tid },
      { from: tid, to: oid },
    ]);
    // 布局与模型分离：DSL 里不允许出现坐标字段
    expect(JSON.stringify(dsl)).not.toContain('"x"');
    expect(JSON.stringify(dsl)).not.toContain('"y"');
  });

  it('执行按钮调用后端并回显逐步日志与输出', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('palette-transform'));
    await user.click(screen.getByTestId('palette-output'));
    await user.click(port('out', 'input'));
    await user.click(port('in', 'transform'));
    await user.click(port('out', 'transform'));
    await user.click(port('in', 'output'));

    await user.click(screen.getByTestId('dsl-run'));

    await waitFor(() => {
      expect(screen.getByTestId('dsl-run-output').textContent)
        .toContain('你好，张三！你今年 34 岁。');
    });
    expect(dslCanvasApi.run).toHaveBeenCalledTimes(1);
    const submitted = vi.mocked(dslCanvasApi.run).mock.calls[0][0];
    expect(submitted.version).toBe('1');
    expect(submitted.edges).toHaveLength(2);
    // 逐步日志回显三个节点
    const logs = screen.getByTestId('dsl-run-logs');
    expect(logs.textContent).toContain('input1');
    expect(logs.textContent).toContain('transform1');
    expect(logs.textContent).toContain('output1');
  });

  it('删除节点会同时移除关联边', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('palette-transform'));
    await user.click(port('out', 'input'));
    await user.click(port('in', 'transform'));

    await user.click(delBtn('input'));
    await user.click(screen.getByTestId('dsl-generate'));

    const dsl = JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');
    expect(dsl.nodes).toHaveLength(1);
    expect(dsl.edges).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// P1 · 收集式 IR 诊断的前端消费（红点 / 字段定位 / 一次全量显示）
// ---------------------------------------------------------------------------

const DIAG_A = {
  node_id: 'input1', field_path: 'params.value', code: 'extra_field',
  message: 'Extra inputs are not permitted',
};
const DIAG_B = {
  node_id: 'transform1', field_path: 'params.template', code: 'string_type',
  message: 'Input should be a valid string',
};

describe('DslCanvas 消费 IR 诊断（P1）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dslCanvasApi.run).mockResolvedValue({
      run_id: 'run-1', status: 'succeeded',
      dsl: { version: '1', nodes: [], edges: [] },
      output: '', error: null, execution_id: null,
      created_at: '2026-10-05T00:00:00Z', logs: [],
    });
  });

  it('两个节点各带一个非法字段 → 诊断面板一次显示 2 条（绝不只报第一条）', async () => {
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({
      valid: false, diagnostics: [DIAG_A, DIAG_B],
    });
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('palette-transform'));

    // 防抖后调用后端 IR 校验
    await waitFor(() => expect(dslCanvasApi.validateIr).toHaveBeenCalled());
    const items = await screen.findAllByTestId('dsl-ir-item');
    expect(items).toHaveLength(2);           // 一次全量：2 条全部可见
    items.forEach((el) => expect(el).toBeVisible());
    expect(screen.getByTestId('dsl-ir-count').textContent).toBe('2');
    // 每条都带 code 与 message（悬停 title 亦然）
    expect(items[0].getAttribute('title')).toContain('extra_field');
    expect(items[1].getAttribute('title')).toContain('string_type');
    expect(screen.getByTestId('dsl-ir-diagnostics').textContent)
      .toContain('params.template');
  });

  it('有诊断的节点画布上打红点，悬停 title 含 code+message', async () => {
    // 节点 id 跨测试递增：诊断按 validateIr 收到的真实文档节点 id 生成。
    vi.mocked(dslCanvasApi.validateIr).mockImplementation(async (doc) => ({
      valid: false,
      diagnostics: [
        { node_id: doc.nodes[0].id, field_path: 'params.value',
          code: 'extra_field', message: 'Extra inputs are not permitted' },
        { node_id: doc.nodes[1].id, field_path: 'params.template',
          code: 'string_type', message: 'Input should be a valid string' },
      ],
    }));
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));
    await user.click(screen.getByTestId('palette-transform'));

    const dots = await screen.findAllByTestId(/^dsl-node-diag-/);
    expect(dots).toHaveLength(2);           // 两个节点各一枚红点
    expect(dots[0].textContent).toContain('1');
    const titles = dots.map((d) => d.getAttribute('title') ?? '');
    const all = titles.join('|');
    expect(all).toContain('params.value');
    expect(all).toContain('extra_field');
    expect(all).toContain('string_type');
    expect(screen.getByTestId('dsl-ir-diagnostics').getAttribute('role')).toBe('alert');
  });

  it('校验通过时面板显示 0 条诊断（绿色态，无 alert 角色）', async () => {
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({
      valid: true, diagnostics: [],
    });
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-input'));

    await waitFor(() => expect(dslCanvasApi.validateIr).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByTestId('dsl-ir-count').textContent).toBe('0');
    });
    expect(screen.getByTestId('dsl-ir-diagnostics').textContent)
      .toContain('类型校验通过');
    expect(screen.queryAllByTestId('dsl-ir-item')).toHaveLength(0);
    expect(screen.getByTestId('dsl-ir-diagnostics').getAttribute('role')).toBeNull();
  });

  it('编辑节点参数后（模型变化）会重新发起 IR 校验', async () => {
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({
      valid: true, diagnostics: [],
    });
    const user = userEvent.setup();
    render(<DslCanvas />);
    await user.click(screen.getByTestId('palette-transform'));

    await waitFor(() => expect(dslCanvasApi.validateIr).toHaveBeenCalledTimes(1));
    await user.click(screen.getByTestId('palette-output'));
    await waitFor(() => expect(dslCanvasApi.validateIr).toHaveBeenCalledTimes(2));
  });
});
