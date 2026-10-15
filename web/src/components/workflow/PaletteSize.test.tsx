import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { DslNodeType } from '../../api/dslCanvas';
import { DSL_NODE_TYPES } from '../../api/dslCanvas';
import { NodePalette } from './NodePalette';

describe('NodePalette renders correct number of types', () => {
  // 单一真源：节点类型清单只在 api/dslCanvas 里维护一份（含 N5 新增的
  // parallel / subgraph，共 20 类），测试不再另抄一份硬编码清单。
  const ALL_TYPES = DSL_NODE_TYPES as readonly DslNodeType[];

  it('renders 18 buttons when given all 20 types', () => {
    expect(ALL_TYPES).toHaveLength(20);

    render(<NodePalette types={[...ALL_TYPES]} onAdd={() => {}} onDropNode={() => {}} />);

    // 每个类型都要有一个 flow-palette-{type} 按钮
    for (const type of ALL_TYPES) {
      const button = screen.getByTestId(`flow-palette-${type}`);
      expect(button).toBeInTheDocument();
    }

    // 总数必须正好是 18（多一个少一个都要被发现）
    const buttons = screen.getAllByTestId(/^flow-palette-/);
    expect(buttons).toHaveLength(20);
  });
});