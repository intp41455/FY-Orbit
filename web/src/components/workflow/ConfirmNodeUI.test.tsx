import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../api/dslCanvas', async (importOriginal) => ({
  // B5 · 单一真源：展开真模块，保留 DSL_NODE_TYPES 等常量不被 mock 掉，
  // 只替换网络面 dslCanvasApi。
  ...(await importOriginal<typeof import('../../api/dslCanvas')>()),
  dslCanvasApi: { validate: vi.fn(), schema: vi.fn(), validateIr: vi.fn() },
}));
import { PropertyPanel } from './PropertyPanel';
import type { EditorNode } from './FlowEditor';

describe('PropertyPanel shows explicit non-executable marking for confirm node', () => {
  const confirmNode: EditorNode = {
    id: 'confirm1',
    type: 'transform',
    verb: 'confirm',
    params: {
      prompt: '请确认是否继续',
      role: 'owner',
    },
    x: 0,
    y: 0,
  };

  it('shows muted text about HITL not connected when confirm node is selected', () => {
    render(<PropertyPanel node={confirmNode} onChange={() => {}} onChangeParams={() => {}} />);

    // Check for the muted text
    const mutedText = screen.getByText(/人工确认动词位：HITL 中断\/恢复尚未接入，执行时该节点必定失败。/);
    expect(mutedText).toBeInTheDocument();
  });
});