import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DslCanvas } from './DslCanvas';

vi.mock('../../api/dslCanvas', () => ({
  dslCanvasApi: { run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn() },
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
    vi.mocked(dslCanvasApi.run).mockResolvedValue({
      run_id: 'run-1',
      status: 'succeeded',
      dsl: { version: '1', nodes: [], edges: [] },
      output: '你好，张三！你今年 34 岁。',
      error: null,
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
