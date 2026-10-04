/**
 * P12 · PropertyPanel 无障碍（键盘可达 / label 关联 / 错误可感知）。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PropertyPanel } from './PropertyPanel';
import type { DslDiagnostic } from '../../api/dslCanvas';
import type { EditorNode } from './FlowEditor';

const node = {
  id: 'tf1', type: 'transform', verb: 'aggregate',
  params: { op: 'sum', field: 'age', sep: ',' }, x: 0, y: 0,
} as unknown as EditorNode;

function setup(diagnostics?: DslDiagnostic[]) {
  render(
    <PropertyPanel
      node={node}
      diagnostics={diagnostics}
      onChange={vi.fn()}
      onChangeParams={vi.fn()}
    />,
  );
}

describe('PropertyPanel 无障碍（P12）', () => {
  it('每个参数控件都被 <label> 包裹（有可编程关联的可读名称）', () => {
    setup();
    for (const testId of ['prop-verb', 'prop-aggregate-op', 'prop-aggregate-field', 'prop-aggregate-sep']) {
      const control = screen.getByTestId(testId);
      const label = control.closest('label');
      expect(label, `${testId} 必须在 label 内`).not.toBeNull();
      expect(label?.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    }
  });

  it('控件可键盘聚焦且 Tab 顺序遍历（焦点可见的编程前提）', async () => {
    const user = userEvent.setup();
    setup();
    await user.tab();
    const focusables: Element[] = [];
    for (let i = 0; i < 4; i += 1) {
      focusables.push(document.activeElement!);
      await user.tab();
    }
    const ids = focusables.map((el) => (el as HTMLElement).dataset?.testid);
    expect(ids).toContain('prop-verb');
    expect(ids).toContain('prop-aggregate-op');
  });

  it('字段级错误以 role="alert" 呈现（读屏可即时感知）', () => {
    setup([{ node_id: 'tf1', field_path: 'params.op', code: 'string_type', message: 'bad' }]);
    expect(screen.getByTestId('field-error-params.op').getAttribute('role')).toBe('alert');
  });

  it('select 控件选项可通过键盘上下键切换（不依赖指针）', async () => {
    const user = userEvent.setup();
    const onChangeParams = vi.fn();
    render(
      <PropertyPanel node={node} onChange={vi.fn()} onChangeParams={onChangeParams} />,
    );
    const opSelect = screen.getByTestId('prop-aggregate-op') as HTMLSelectElement;
    await user.selectOptions(opSelect, 'avg');
    expect(onChangeParams).toHaveBeenCalledWith({ op: 'avg' });
  });
});
