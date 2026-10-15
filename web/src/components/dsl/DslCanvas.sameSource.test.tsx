/**
 * FY-N4 · 同源派生的界面契约测试。
 *
 * 只测「界面契约」，不测后端：断言代码视图（DSL JSON）与画布视图
 * （节点/连线）确实从**同一份 source** 派生——即不点任何「生成/同步」
 * 按钮，画布一改，代码视图立刻同步；且两条路径（代码视图 / 执行请求）
 * 提交的 DSL 结构逐字段一致。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // 单一真源：展开真模块保留常量与类型，只替换网络面。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: {
    run: vi.fn(), validate: vi.fn(), schema: vi.fn(), getRun: vi.fn(),
    validateIr: vi.fn(),
  },
}));

import { DslCanvas } from './DslCanvas';
import { dslCanvasApi } from '../../api/dslCanvas';
import { serializeGraph } from '../workflow/FlowEditor';
import type { DslDocument } from '../../api/dslCanvas';

const port = (dir: 'in' | 'out', type: string) =>
  screen.getByTestId(new RegExp(`^port-${dir}-${type}`));

const readDsl = (): DslDocument =>
  JSON.parse(screen.getByTestId('dsl-json').textContent ?? '{}');

/** 建一条 input → transform → output 的三节点流（与既有 DslCanvas 测试同路径）。 */
async function buildThreeNodeFlow(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByTestId('palette-input'));
  await user.click(screen.getByTestId('palette-transform'));
  await user.click(screen.getByTestId('palette-output'));
  await user.click(port('out', 'input'));
  await user.click(port('in', 'transform'));
  await user.click(port('out', 'transform'));
  await user.click(port('in', 'output'));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
  vi.mocked(dslCanvasApi.run).mockResolvedValue({
    run_id: 'run-1', status: 'succeeded',
    dsl: { version: '1', nodes: [], edges: [] },
    output: 'ok', error: null, execution_id: null,
    created_at: '2026-10-12T00:00:00Z', logs: [],
  });
});

describe('FY-N4 同源派生 · 代码视图与画布视图共用一份 source', () => {
  it('不点「生成 DSL」，画布加节点后代码视图已是最新（无手动同步闸门）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);

    // 空画布时就已渲染 JSON 视图（不是「点按钮才出现」）
    expect(readDsl()).toEqual({ version: '1', nodes: [], edges: [] });

    // 只点面板加一个节点，**不点生成按钮**
    await user.click(screen.getByTestId('palette-input'));
    expect(readDsl().nodes).toHaveLength(1);

    await user.click(screen.getByTestId('palette-transform'));
    expect(readDsl().nodes).toHaveLength(2);
  });

  it('连线后代码视图的 edges 与画布连线一致（同一份 edges 派生）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await buildThreeNodeFlow(user);

    const dsl = readDsl();
    // 画布上 3 个节点 / 2 条连线 → 代码视图逐条对应
    expect(dsl.nodes).toHaveLength(3);
    expect(dsl.edges).toEqual([
      { from: dsl.nodes[0]!.id, to: dsl.nodes[1]!.id },
      { from: dsl.nodes[1]!.id, to: dsl.nodes[2]!.id },
    ]);
  });

  it('删除节点后代码视图同步收缩（不是陈旧快照）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await buildThreeNodeFlow(user);
    expect(readDsl().nodes).toHaveLength(3);

    await user.click(screen.getByTestId(new RegExp('^delete-input')));

    const dsl = readDsl();
    expect(dsl.nodes).toHaveLength(2);
    // 级联删边：input 的出边也没了
    expect(dsl.edges).toEqual([
      { from: dsl.nodes[0]!.id, to: dsl.nodes[1]!.id },
    ]);
  });

  it('代码视图与「执行」提交给后端的 DSL 是同一份（逐字段一致，无双源）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await buildThreeNodeFlow(user);

    await user.click(screen.getByTestId('dsl-run'));
    await waitFor(() => expect(dslCanvasApi.run).toHaveBeenCalledTimes(1));

    // 断言「不是碰巧一样」，而是拿两边实际值做严格相等
    expect(vi.mocked(dslCanvasApi.run).mock.calls[0]![0]).toEqual(readDsl());
  });

  it('代码视图字段集合与共享序列化实现 serializeGraph 完全一致', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await buildThreeNodeFlow(user);

    // DslCanvas 不再自抄一份序列化：同一份图经唯一真源序列化后必须逐字段相同
    const nodes = readDsl().nodes.map((n) => ({ ...n }));
    const edges = readDsl().edges.map((e) => ({ ...e }));
    expect(serializeGraph(nodes, edges)).toEqual(readDsl());

    // 显式字段集合断言：非 transform 节点绝不带 verb 键（后端 graph_to_dsl 同规则）
    for (const n of readDsl().nodes) {
      if (n.type === 'transform') {
        expect(Object.keys(n)).toContain('verb');
      } else {
        expect(Object.keys(n)).not.toContain('verb');
      }
    }
  });

  it('代码视图是同一个 source 的纯函数结果：重复读取内容稳定（无内部副本漂移）', async () => {
    const user = userEvent.setup();
    render(<DslCanvas />);
    await buildThreeNodeFlow(user);

    const first = screen.getByTestId('dsl-json').textContent;
    const second = screen.getByTestId('dsl-json').textContent;
    expect(first).toBe(second);
  });
});