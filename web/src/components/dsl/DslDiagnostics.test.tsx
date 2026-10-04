/**
 * P1 · 诊断面板组件单测（收集式 IR 的前端消费契约）。
 *
 * 核心契约：传入多少条 Diagnostic，就一次全量渲染多少条——
 * 绝不截断（这正是 IR 相对旧 fail-fast 校验器的核心价值）。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  DslDiagnostics,
  diagnosticsForNode,
  nodeDiagnosticTitle,
} from './DslDiagnostics';
import type { DslDiagnostic } from '../../api/dslCanvas';

const D1: DslDiagnostic = {
  node_id: 'a', field_path: 'params.bogus', code: 'extra_field',
  message: 'Extra inputs are not permitted',
};
const D2: DslDiagnostic = {
  node_id: 'b', field_path: 'params.nope', code: 'extra_field',
  message: 'Extra inputs are not permitted',
};
const D3: DslDiagnostic = {
  node_id: '', field_path: '', code: 'edge_loop',
  message: '图中存在环',
};

describe('DslDiagnostics', () => {
  it('0 条诊断：显示通过态，不渲染任何列表项，也没有 alert 角色', () => {
    render(<DslDiagnostics diagnostics={[]} />);
    expect(screen.getByTestId('dsl-ir-count').textContent).toBe('0');
    expect(screen.getByTestId('dsl-ir-diagnostics').textContent).toContain('类型校验通过');
    expect(screen.queryAllByTestId('dsl-ir-item')).toHaveLength(0);
    expect(screen.getByTestId('dsl-ir-diagnostics').getAttribute('role')).toBeNull();
  });

  it('2 条诊断：一次全量渲染 2 条（门禁核心用例的组件层验证）', () => {
    render(<DslDiagnostics diagnostics={[D1, D2]} />);
    const items = screen.getAllByTestId('dsl-ir-item');
    expect(items).toHaveLength(2);
    expect(screen.getByTestId('dsl-ir-count').textContent).toBe('2');
    expect(screen.getByTestId('dsl-ir-diagnostics').getAttribute('role')).toBe('alert');
    // 每条都可见且含 code / node / field_path / message
    items.forEach((el) => expect(el).toBeVisible());
    expect(items[0].textContent).toContain('a');
    expect(items[0].textContent).toContain('params.bogus');
    expect(items[1].textContent).toContain('b');
    expect(items[1].textContent).toContain('params.nope');
  });

  it('3 条（含文档级 node_id 为空）诊断：全部显示，空 node_id 显示「(文档)」', () => {
    render(<DslDiagnostics diagnostics={[D1, D2, D3]} />);
    expect(screen.getAllByTestId('dsl-ir-item')).toHaveLength(3);
    expect(screen.getByTestId('dsl-ir-diagnostics').textContent).toContain('(文档)');
    expect(screen.getByTestId('dsl-ir-diagnostics').textContent).toContain('图中存在环');
  });

  it('悬停 title 暴露 code + message（按字段定位）', () => {
    render(<DslDiagnostics diagnostics={[D1]} />);
    const item = screen.getByTestId('dsl-ir-item');
    expect(item.getAttribute('title')).toBe('extra_field: Extra inputs are not permitted');
  });
});

describe('diagnosticsForNode / nodeDiagnosticTitle', () => {
  it('按 node_id 过滤；空串 node_id 只匹配文档级诊断', () => {
    expect(diagnosticsForNode([D1, D2, D3], 'a')).toEqual([D1]);
    expect(diagnosticsForNode([D1, D2, D3], '')).toEqual([D3]);
    expect(diagnosticsForNode([D1, D2], 'zzz')).toEqual([]);
  });

  it('title 文本逐条拼接「字段 错误码: 说明」', () => {
    expect(nodeDiagnosticTitle([D1, D3]))
      .toBe('params.bogus extra_field: Extra inputs are not permitted\n(文档) edge_loop: 图中存在环');
    expect(nodeDiagnosticTitle([])).toBe('');
  });
});
