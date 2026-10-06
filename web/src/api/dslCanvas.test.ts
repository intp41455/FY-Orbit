// B3/B5 · 动词单一真源回归测试。
//
// 后端 `services/dsl_canvas.py` 的 `VERB_REGISTRY` 是唯一真源（10 个动词，
// 含治理类 approval）；前端唯一合法的动词清单是本目录 `dslCanvas.ts` 的
// `DSL_TRANSFORM_VERBS` 常量——DslCanvas 的下拉、FlowEditor 的清单（包5）
// 都必须 import 它，任何一方再自抄一份都会让动词集重新漂移，本测试钉住。
import { describe, it, expect } from 'vitest';
import { DSL_TRANSFORM_VERBS, type DslTransformVerb } from './dslCanvas';

describe('DSL_TRANSFORM_VERBS 单一真源', () => {
  it('包含后端注册表全部 10 个动词，顺序逐字一致', () => {
    // 顺序 = services/dsl_canvas.py VERB_REGISTRY 的插入序（TRANSFORM_VERBS）。
    expect([...DSL_TRANSFORM_VERBS]).toEqual([
      'map', 'filter', 'template',
      'branch', 'aggregate', 'merge',
      'agent', 'confirm', 'artifact',
      'approval',
    ]);
  });

  it('B3 回归：治理动词 approval 必须在列', () => {
    expect(DSL_TRANSFORM_VERBS).toContain('approval');
  });

  it('清单无重复（封闭白名单不允许同名词重复注册）', () => {
    expect(new Set(DSL_TRANSFORM_VERBS).size).toBe(DSL_TRANSFORM_VERBS.length);
  });

  it('DslTransformVerb 类型由常量派生（类型与清单不可能漂移）', () => {
    const verbs: readonly DslTransformVerb[] = DSL_TRANSFORM_VERBS;
    expect(verbs.length).toBe(10);
    // 反向：一个清单外的动词不能赋给 DslTransformVerb（编译期保证，这里
    // 用 @ts-expect-error 钉住——若类型漂移成 string，这行会因「无错误」而红）。
    // @ts-expect-error 清单外的动词不是合法 DslTransformVerb
    const outside: DslTransformVerb = 'exec';
    expect(outside).toBe('exec');
  });
});
