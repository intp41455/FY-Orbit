import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // B5 · 单一真源：展开真模块，保留 DSL_NODE_TYPES 等常量不被 mock 掉，
  // 只替换网络面 dslCanvasApi。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: { validate: vi.fn(), schema: vi.fn(), validateIr: vi.fn() },
}));
import { dslCanvasApi, DSL_NODE_TYPES } from '../../api/dslCanvas';
import type { DslNodeType } from '../../api/dslCanvas';
import { FlowEditor } from './FlowEditor';

describe('FlowEditor can add all 18 node types from palette', () => {
  // 单一真源：不在测试里另抄一份节点类型清单（后端/前端已扩到 18 类）。
  const ALL_TYPES = DSL_NODE_TYPES as readonly DslNodeType[];
  const TYPE_COUNT = 20;

  beforeEach(() => {
    vi.clearAllMocks();
    expect(ALL_TYPES).toHaveLength(TYPE_COUNT);
    // Mock schema to return all 20 types
    vi.mocked(dslCanvasApi.schema).mockResolvedValue({
      schema: {},
      node_types: [...ALL_TYPES],
      transform_verbs: ['map', 'filter', 'template', 'branch', 'aggregate', 'merge', 'agent', 'confirm', 'artifact', 'approval'],
      verb_catalog: [],
      aggregate_ops: ['count', 'sum', 'min', 'max', 'avg', 'first', 'last', 'join', 'unique'],
      merge_ops: ['concat', 'first', 'last'],
      output_formats: ['json', 'text'],
    });
    // Mock validate to always pass
    vi.mocked(dslCanvasApi.validate).mockResolvedValue({ valid: true, topological_order: [] });
    // Mock validateIr to always pass
    vi.mocked(dslCanvasApi.validateIr).mockResolvedValue({ valid: true, diagnostics: [] });
  });

  it('can add each node type and verify it appears in the canvas', async () => {
    render(<FlowEditor />);
    await waitFor(() => expect(dslCanvasApi.schema).toHaveBeenCalled());

    // Click each palette button to add a node of that type
    for (const type of ALL_TYPES) {
      const paletteButton = await screen.findByTestId(`flow-palette-${type}`);
      await userEvent.click(paletteButton);
      // After each click, we can wait for the node count to increase by 1, but we'll just do a short wait
      // to allow the state to update. We'll wait for 50ms.
      await new Promise(resolve => setTimeout(resolve, 50));
    }

    // After adding all types, we expect 20 nodes in the canvas.
    await waitFor(() => {
      const nodes = screen.getAllByTestId(/^flow-node-/);
      return nodes.length === TYPE_COUNT;
    });

    const nodes = screen.getAllByTestId(/^flow-node-/);
    expect(nodes).toHaveLength(TYPE_COUNT);

    // Additionally, we can verify that each node has the correct type in its header.
    // We'll check that for each type, there is a node whose header contains that type.
    for (const type of ALL_TYPES) {
      // We look for an element that contains the type string and is within a node.
      // We can use screen.getAllByText(type) and then check that it is inside a node.
      // But for simplicity, we can just check that the text content of the canvas contains each type.
      // We'll get the entire text content of the canvas and check that it includes each type.
      const canvasText = screen.getByTestId('flow-canvas').textContent;
      expect(canvasText).toContain(type);
    }
  });
});