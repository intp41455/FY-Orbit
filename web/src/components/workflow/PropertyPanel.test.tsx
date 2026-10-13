/**
 * P1 · PropertyPanel 字段级类型错误态（消费收集式 IR 诊断）。
 *
 * 契约：命中本节点 field_path 的诊断 → 该字段红框（fy-field-invalid）
 * + 内联错误文案（role="alert"，含 code 与 message）；
 * 多字段同时出错时**每个字段各自标错**；无诊断时无错误态。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PropertyPanel } from './PropertyPanel';
import type { DslDiagnostic, DslSchemaResponse } from '../../api/dslCanvas';
import type { EditorNode } from './FlowEditor';

const node: EditorNode = {
  id: 'tf1',
  type: 'transform',
  verb: 'aggregate',
  params: { op: 'sum', field: 'age', sep: ',' },
  x: 0, y: 0,
} as unknown as EditorNode;

const DIAG_OP: DslDiagnostic = {
  node_id: 'tf1', field_path: 'params.op', code: 'string_type',
  message: 'Input should be a valid string',
};
const DIAG_FIELD: DslDiagnostic = {
  node_id: 'tf1', field_path: 'params.field', code: 'missing',
  message: 'Field required',
};

function setup(diagnostics?: DslDiagnostic[]) {
  const onChange = vi.fn();
  const onChangeParams = vi.fn();
  render(
    <PropertyPanel
      node={node}
      diagnostics={diagnostics}
      onChange={onChange}
      onChangeParams={onChangeParams}
    />,
  );
  return { onChange, onChangeParams };
}

describe('PropertyPanel 的 IR 字段错误态（P1）', () => {
  it('无诊断：字段无红框、无内联文案', () => {
    setup(undefined);
    const field = screen.getByTestId('prop-aggregate-op').closest('label')!;
    expect(field.className).not.toContain('fy-field-invalid');
    expect(screen.queryByTestId('field-error-params.op')).toBeNull();
  });

  it('单字段诊断：该字段红框 + 内联 code/message，其余字段不受影响', () => {
    setup([DIAG_OP]);
    const opField = screen.getByTestId('prop-aggregate-op').closest('label')!;
    expect(opField.className).toContain('fy-field-invalid');
    const msg = screen.getByTestId('field-error-params.op');
    expect(msg.getAttribute('role')).toBe('alert');
    expect(msg.textContent).toContain('string_type');
    expect(msg.textContent).toContain('Input should be a valid string');
    // 相邻字段没有错误态
    const sepField = screen.getByTestId('prop-aggregate-sep').closest('label')!;
    expect(sepField.className).not.toContain('fy-field-invalid');
  });

  it('两个参数各有一条诊断：两个字段同时各自标错（不止第一个）', () => {
    setup([DIAG_OP, DIAG_FIELD]);
    expect(screen.getByTestId('prop-aggregate-op').closest('label')!.className)
      .toContain('fy-field-invalid');
    expect(screen.getByTestId('prop-aggregate-field').closest('label')!.className)
      .toContain('fy-field-invalid');
    expect(screen.getByTestId('field-error-params.field').textContent)
      .toContain('Field required');
  });

  it('其他节点的诊断不会标到本节点字段上', () => {
    setup([{ ...DIAG_OP, node_id: 'tf999' }]);
    expect(screen.getByTestId('prop-aggregate-op').closest('label')!.className)
      .not.toContain('fy-field-invalid');
  });

  it('选中节点后错误文案仍可编辑控件（onChange 正常触发）', () => {
    const { onChangeParams } = setup([DIAG_OP]);
    fireEvent.change(screen.getByTestId('prop-aggregate-op'), { target: { value: 'avg' } });
    expect(onChangeParams).toHaveBeenCalledWith({ op: 'avg' });
  });
});

// --------------------------------------------------------------------------- //
// G4-j · 参数枚举真去重：下拉选项来自后端 schema（注入），非本地硬编码镜像。
// --------------------------------------------------------------------------- //
describe('PropertyPanel 参数枚举来自后端 schema（G4-j）', () => {
  const CUSTOM = 'zzz_backend_only';

  function optionValues(testId: string): (string | null)[] {
    return Array.from(screen.getByTestId(testId).querySelectorAll('option'))
      .map((o) => o.getAttribute('value'));
  }

  // 后端 shape：node_params_schema 是 {节点类型: JSON Schema}；verb params 在
  // verb_catalog[].params_schema 里。这里只注入恰好被断言的那几个枚举。
  const schema = {
    schema: {},
    node_types: [],
    transform_verbs: [],
    verb_catalog: [
      {
        name: 'map', category: 'transform', summary: '', executable: true,
        params_schema: {
          type: 'object', additionalProperties: false,
          properties: { op: { enum: ['set', 'upper', 'lower', CUSTOM] } },
        },
      },
    ],
    node_params_schema: {
      input: {
        type: 'object', additionalProperties: false,
        properties: { kind: { enum: ['literal', 'text_lines', CUSTOM] } },
      },
    },
    aggregate_ops: [],
    merge_ops: [],
    output_formats: [],
  } as unknown as DslSchemaResponse;

  it('input.kind 下拉来自注入 schema（含后端独有值），非本地镜像', () => {
    render(
      <PropertyPanel
        node={{ id: 'in1', type: 'input', params: {}, x: 0, y: 0 } as unknown as EditorNode}
        schema={schema}
        onChange={vi.fn()}
        onChangeParams={vi.fn()}
      />,
    );
    expect(optionValues('prop-input-kind')).toContain(CUSTOM);
  });

  it('transform map 的 op 下拉来自 verb_catalog（含后端独有值）', () => {
    render(
      <PropertyPanel
        node={{ id: 'tf2', type: 'transform', verb: 'map', params: {}, x: 0, y: 0 } as unknown as EditorNode}
        schema={schema}
        onChange={vi.fn()}
        onChangeParams={vi.fn()}
      />,
    );
    expect(optionValues('prop-map-op')).toContain(CUSTOM);
  });

  it('未注入 schema 时如实回落到本地常量（不出现后端独有值）', () => {
    render(
      <PropertyPanel
        node={{ id: 'in2', type: 'input', params: {}, x: 0, y: 0 } as unknown as EditorNode}
        onChange={vi.fn()}
        onChangeParams={vi.fn()}
      />,
    );
    expect(optionValues('prop-input-kind')).toEqual(['literal', 'text_lines']);
  });
});
