/**
 * 防回归：执行门禁不得比后端认得的动词集更窄。
 *
 * 背景：FlowEditor 曾把 transform 动词硬编码成 ['map','filter','template']，
 * 而后端 `services/dsl_canvas.py` 的 `TRANSFORM_VERBS` 有 10 个。结果是
 * branch/aggregate/merge/agent/confirm/artifact/approval 这 7 个后端认得的
 * 动词，在 quickValidate 与 runFlow 两处执行门禁上被前端自己判为「verb 非法」。
 *
 * 本文件遍历 `DSL_TRANSFORM_VERBS` 真源全量动词，断言 quickValidate 全绿，
 * 防止门禁再次收窄。同时钉住「真源外动词必须被拒」。
 */
import { describe, it, expect } from 'vitest';

import { DSL_TRANSFORM_VERBS, type DslDocument, type DslTransformVerb } from '../../api/dslCanvas';
import { paramsForVerb, quickValidate } from './FlowEditor';

const docFor = (verb: DslTransformVerb): DslDocument => ({
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: ['示例行'] } },
    { id: 'tf1', type: 'transform', verb, params: paramsForVerb(verb) },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [
    { from: 'in1', to: 'tf1' },
    { from: 'tf1', to: 'out1' },
  ],
});

describe('FlowEditor 执行门禁 · 覆盖后端全部 transform 动词', () => {
  it('DSL_TRANSFORM_VERBS 是 10 个动词的封闭集', () => {
    expect(DSL_TRANSFORM_VERBS).toHaveLength(10);
  });

  it.each([...DSL_TRANSFORM_VERBS])('动词 %s 通过 quickValidate（不被前端误拒）', (verb) => {
    const res = quickValidate(docFor(verb));
    expect(res.message).toBe('');
    expect(res.ok).toBe(true);
  });

  it('真源之外的动词仍被拒（门禁不是形同虚设）', () => {
    const bad = {
      version: '1',
      nodes: [{ id: 'tf1', type: 'transform', verb: 'definitely_not_a_verb' as never, params: {} }],
      edges: [],
    } as DslDocument;
    expect(quickValidate(bad).message).toContain('verb 非法');
  });
});