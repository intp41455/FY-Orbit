import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// FlowEditor 依赖两个 API 模块。schema 决定左侧面板的节点类型（动态取自
// 后端），validate 是载入 DSL 的权威校验，exportScript 是导出出口。
vi.mock('../../api/dslCanvas', () => ({
  dslCanvasApi: { validate: vi.fn(), schema: vi.fn() },
}));
vi.mock('../../api/workflowGen', () => ({
  workflowGenApi: { exportScript: vi.fn() },
}));

import { dslCanvasApi } from '../../api/dslCanvas';
import { workflowGenApi } from '../../api/workflowGen';
import { ApiError } from '../../api/client';
import {
  CANVAS_H,
  FlowEditor,
  autoPosition,
  bezierPath,
  clampPos,
  defaultParams,
  edgeAnchors,
  graphFromDsl,
  nextNodeId,
  paramsForVerb,
  quickValidate,
  serializeGraph,
  topoOrder,
  wouldCreateCycle,
  __resetNodeIdSeq,
  type EditorNode,
} from './FlowEditor';
import type { DslDocument } from '../../api/dslCanvas';

const VALID_DSL: DslDocument = {
  version: '1',
  nodes: [
    { id: 'in1', type: 'input', params: { kind: 'literal', value: [{ text: '示例行' }] } },
    { id: 'tf1', type: 'transform', verb: 'template', params: { template: '处理：{text}' } },
    { id: 'out1', type: 'output', params: { format: 'text' } },
  ],
  edges: [
    { from: 'in1', to: 'tf1' },
    { from: 'tf1', to: 'out1' },
  ],
};

const node = (id: string, type: EditorNode['type'], extra: Partial<EditorNode> = {}): EditorNode => ({
  id, type, params: {}, x: 0, y: 0, ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  __resetNodeIdSeq();
  vi.mocked(dslCanvasApi.schema).mockResolvedValue({
    schema: {}, node_types: ['input', 'transform', 'output'],
    transform_verbs: ['map', 'filter', 'template', 'branch', 'aggregate', 'merge', 'agent', 'confirm', 'artifact'],
    verb_catalog: [],
    aggregate_ops: ['count', 'sum', 'min', 'max', 'avg', 'first', 'last', 'join', 'unique'],
    merge_ops: ['concat', 'first', 'last'],
    output_formats: ['json', 'text'],
  });
  vi.mocked(dslCanvasApi.validate).mockResolvedValue({ valid: true, topological_order: ['in1', 'tf1', 'out1'] });
});

// =========================================================================== //
// 1. 纯函数：模型 / 视图分离
// =========================================================================== //

describe('FlowEditor 纯函数 · 模型与视图严格分离', () => {
  it('serializeGraph 丢弃 x/y 坐标，DSL 里绝不含布局字段', () => {
    const nodes: EditorNode[] = [
      node('a', 'input', { x: 111, y: 222, params: { kind: 'literal', value: 1 } }),
      node('b', 'transform', { verb: 'template', x: 333, y: 444, params: { template: '{name}' } }),
    ];
    const doc = serializeGraph(nodes, [{ from: 'a', to: 'b' }]);
    // 逐字段断言：不是「没看到 x 就以为对」，而是明确断言坐标键不存在。
    expect(doc.nodes[0]).not.toHaveProperty('x');
    expect(doc.nodes[0]).not.toHaveProperty('y');
    expect(doc.nodes[1]).not.toHaveProperty('x');
    expect(JSON.stringify(doc)).not.toMatch(/"\s*(x|y)\s*"\s*:/);
    expect(doc.version).toBe('1');
    expect(doc.edges).toEqual([{ from: 'a', to: 'b' }]);
  });

  it('graphFromDsl 给节点补上坐标，DSL 往返后语义不变（坐标另存）', () => {
    const { nodes, edges } = graphFromDsl(VALID_DSL);
    expect(nodes.map((n) => n.id)).toEqual(['in1', 'tf1', 'out1']);
    // transform 节点缺 verb 时补默认 template，避免下游 serialize 产非法 DSL
    expect(nodes[1].verb).toBe('template');
    // 传入 layout 时用外部坐标，DSL 本身不带坐标
    const placed = graphFromDsl(VALID_DSL, { tf1: { x: 5, y: 6 } });
    expect(placed.nodes[1]).toMatchObject({ x: 5, y: 6 });
    // 往返：坐标丢回DSL，但节点/边语义完全一致
    expect(serializeGraph(nodes, edges)).toEqual(VALID_DSL);
  });

  it('autoPosition 网格排布；clampPos 把坐标夹在画布内', () => {
    expect(autoPosition(0)).toEqual({ x: 30, y: 30 });
    expect(autoPosition(3)).toEqual({ x: 30, y: 140 });
    const out = clampPos(-999, 99999);
    expect(out.x).toBe(0);
    expect(out.y).toBe(CANVAS_H - 52);
    // 取整：拖拽产生小数坐标时不留脏值
    expect(clampPos(12.6, 34.2)).toEqual({ x: 13, y: 34 });
  });

  it('bezierPath / edgeAnchors 产出连线几何；端点缺失时返回 null 而不是假坐标', () => {
    expect(bezierPath(0, 0, 10, 10)).toBe('M 0 0 C 5 0, 5 10, 10 10');
    const a = edgeAnchors(node('a', 'input', { x: 10, y: 20 }), node('b', 'output', { x: 200, y: 60 }));
    expect(a).toEqual({ x1: 158, y1: 46, x2: 200, y2: 86 });
    // 诚实：找不到端点就不画线，不返回 {0,0,0,0} 假装有连线
    expect(edgeAnchors(node('a', 'input'), undefined)).toBeNull();
  });

  it('nextNodeId 产出符合后端 id 正则的 id', () => {
    expect(nextNodeId('input')).toBe('input1');
    expect(nextNodeId('transform')).toBe('transform2');
    for (const id of [nextNodeId('output'), 'a_b-C9']) {
      expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
    }
  });

  it('defaultParams / paramsForVerb 换动词时整组替换，不留上一个动词的残留字段', () => {
    expect(defaultParams('input')).toEqual({ kind: 'literal', value: ['示例行'] });
    expect(defaultParams('output')).toEqual({ format: 'text' });
    expect(paramsForVerb('template')).toEqual({ template: '处理：{value}' });
    expect(paramsForVerb('map')).toEqual({ op: 'set', field: 'tag', value: '已处理' });
    // map 的参数里绝不能残留 template —— 后端会因未知字段 422
    expect(Object.keys(paramsForVerb('map'))).not.toContain('template');
    expect(Object.keys(paramsForVerb('filter'))).toEqual(['field', 'op', 'value']);
  });
});

// =========================================================================== //
// 2. 纯函数：环检测与快校验（与后端 compile_dsl 语义等价）
// =========================================================================== //

describe('FlowEditor 环检测与本地快校验', () => {
  it('wouldCreateCycle 识别自环、间接环，且不误报菱形', () => {
    const chain: { from: string; to: string }[] = [
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
    ];
    expect(wouldCreateCycle(chain, 'a', 'a')).toBe(true);// 自环
    expect(wouldCreateCycle(chain, 'c', 'a')).toBe(true);  // 间接成环
    expect(wouldCreateCycle(chain, 'a', 'c')).toBe(false); // 顺向不算
    // 菱形 a→b, a→c, b→d, c→d 本身无环：加 a→d（已可达的顺向边）不算成环
    const diamond = [
      { from: 'a', to: 'b' }, { from: 'a', to: 'c' },
      { from: 'b', to: 'd' }, { from: 'c', to: 'd' },
    ];
    expect(wouldCreateCycle(diamond, 'a', 'd')).toBe(false);
    // 但 d 能绕回 a（a→b→d→a），必须判定成环
    expect(wouldCreateCycle(diamond, 'd', 'a')).toBe(true);
  });

  it('topoOrder 无环时给出顺序，成环时返回 null', () => {
    expect(topoOrder(VALID_DSL)).toEqual(['in1', 'tf1', 'out1']);
    const cyclic: DslDocument = {
      version: '1',
      nodes: [{ id: 'a', type: 'input', params: {} }, { id: 'b', type: 'output', params: {} }],
      edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }],
    };
    expect(topoOrder(cyclic)).toBeNull();
  });

  it('quickValidate 逐条如实报错：空图 / 重复 id / 非法 type / 非法 verb / 悬空边 / 环', () => {
    expect(quickValidate({ version: '1', nodes: [], edges: [] }).ok).toBe(false);
    expect(quickValidate(VALID_DSL)).toEqual({ ok: true, message: '' });

    const dup: DslDocument = {
      version: '1',
      nodes: [node('a', 'input'), node('a', 'output')],
      edges: [],
    };
    expect(quickValidate(dup).message).toContain('id 重复');

    const badType = { version: '1', nodes: [node('a', 'sql' as never)], edges: [] } as DslDocument;
    expect(quickValidate(badType).message).toContain('type 非法');

    const badVerb = {
      version: '1',
      nodes: [node('a', 'transform', { verb: 'translate' as never })], edges: [],
    } as DslDocument;
    expect(quickValidate(badVerb).message).toContain('verb 非法');

    const dangling: DslDocument = { version: '1', nodes: [node('a', 'input')], edges: [{ from: 'a', to: 'ghost' }] };
    expect(quickValidate(dangling).message).toContain('未定义节点');

    const cyclic: DslDocument = {
      version: '1',
      nodes: [node('a', 'input'), node('b', 'output')],
      edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }],
    };
    const res = quickValidate(cyclic);
    expect(res.ok).toBe(false);
    expect(res.message).toContain('环');
  });
});

// =========================================================================== //
// 3. 组件：动态 schema / 拖拽成环拒绝 / 行列级错误 / 导出
// =========================================================================== //

describe('FlowEditor 组件 · 画布交互', () => {
  it('左侧节点面板动态取后端 schema 类型（不是硬编码列表）', async () => {
    render(<FlowEditor />);
    await waitFor(() => expect(dslCanvasApi.schema).toHaveBeenCalled());
    for (const t of ['input', 'transform', 'output']) {
      expect(await screen.findByTestId(`flow-palette-${t}`)).toBeInTheDocument();
    }
  });

  it('schema 拉取失败时如实回落到本地受限集，编辑器仍可用（不白屏）', async () => {
    vi.mocked(dslCanvasApi.schema).mockRejectedValue(new Error('500'));
    render(<FlowEditor />);
    expect(await screen.findByTestId('flow-palette-input')).toBeInTheDocument();
    expect(screen.getByTestId('flow-editor-root')).toBeInTheDocument();
  });

  it('点选添加节点并连线，代码视图实时同步且不含坐标', async () => {
    const user = userEvent.setup();
    render(<FlowEditor />);
    await screen.findByTestId('flow-palette-input');

    await user.click(screen.getByTestId('flow-palette-input'));
    await user.click(screen.getByTestId('flow-palette-transform'));
    await user.click(screen.getByTestId('flow-palette-output'));

    await user.click(screen.getByTestId('flow-out-input1'));
    await user.click(screen.getByTestId('flow-in-transform2'));
    await user.click(screen.getByTestId('flow-out-transform2'));
    await user.click(screen.getByTestId('flow-in-output3'));

    expect(await screen.findByTestId('flow-edge-input1-transform2')).toBeInTheDocument();

    const code = JSON.parse(screen.getByTestId('flow-code').textContent ?? '{}');
    expect(code.nodes.map((n: { id: string }) => n.id)).toEqual(['input1', 'transform2', 'output3']);
    expect(code.edges).toHaveLength(2);
    // 双向同步的硬要求：画布 → DSL 也不带坐标
    expect(JSON.stringify(code)).not.toMatch(/"\s*(x|y)\s*"\s*:/);
  });

  it('连成环时立刻在顶部报错并拒绝该边，不静默接受', async () => {
    const user = userEvent.setup();
    render(<FlowEditor initialDoc={VALID_DSL} />);
    await screen.findByTestId('flow-node-in1');
    // 已有 in1→tf1→out1；试连out1→in1 必然成环
    await user.click(screen.getByTestId('flow-out-out1'));
    await user.click(screen.getByTestId('flow-in-in1'));

    const banner = await screen.findByTestId('flow-error');
    expect(banner.textContent).toContain('会形成环');
    // 该边确实没被加进去
    const code = JSON.parse(screen.getByTestId('flow-code').textContent ?? '{}');
    expect(code.edges).toEqual(VALID_DSL.edges);
  });

  it('属性面板换动词会整组替换 params，换动词后 DSL 仍合法', async () => {
    const user = userEvent.setup();
    render(<FlowEditor initialDoc={VALID_DSL} />);
    const nodeCard = await screen.findByTestId('flow-node-tf1');
    await user.click(nodeCard);

    await user.selectOptions(screen.getByTestId('prop-verb'), 'map');
    const code = JSON.parse(screen.getByTestId('flow-code').textContent ?? '{}');
    const tf = code.nodes.find((n: { id: string }) => n.id === 'tf1');
    expect(tf.verb).toBe('map');
    expect(tf.params.op).toBe('set');
    // 旧动词的 template 必须消失，否则后端 validate 会因未知字段拒绝
    expect(tf.params).not.toHaveProperty('template');
    expect(quickValidate(code).ok).toBe(true);
  });

  it('载入非法 DSL：整份拒绝、显示行列级错误，画布内容不被半解析污染', async () => {
    const user = userEvent.setup();
    render(<FlowEditor />);
    await screen.findByTestId('flow-palette-input');
    // 先放一个节点，确保「拒绝后画布不变」是可观测的
    await user.click(screen.getByTestId('flow-palette-input'));

    // 用真实 ApiError 构造：request() 已把 {"error":{...}} 外层剥掉，
    // err.body 就是 {code, message, details}（见 api/client.ts:117-121）。
    vi.mocked(dslCanvasApi.validate).mockRejectedValue(
      new ApiError(422, {
        code: 'workflow_dsl_invalid',
        message: "transform 节点 b verb 必须是 ('map','filter','template')",
        details: { line: 13, column: 8 },
      }, 'Request failed with status 422'),
    );

    // paste 而非 type：userEvent.type 会把 '{' 当特殊描述符，DSL 的花括号会被吃掉
    await user.click(screen.getByTestId('flow-dsl-input'));
    await user.paste('{"version":"1","nodes":[]}');
    await user.click(screen.getByTestId('flow-load'));

    const banner = await screen.findByTestId('flow-error');
    expect(banner.textContent).toContain("必须是 ('map','filter','template')");
    expect(screen.getByTestId('flow-error-pos').textContent).toBe('第 13 行 第 8 列');
    // 画布仍是原来那一个节点——没有半解析
    expect(screen.getAllByTestId(/^flow-node-/)).toHaveLength(1);
  });

  it('DSL 文本不是合法 JSON 时本地就拦下，不打后端', async () => {
    const user = userEvent.setup();
    render(<FlowEditor />);
    await screen.findByTestId('flow-palette-input');
    await user.click(screen.getByTestId('flow-dsl-input'));
    await user.paste('not-json{');
    await user.click(screen.getByTestId('flow-load'));
    expect((await screen.findByTestId('flow-error')).textContent).toContain('不是合法 JSON');
    expect(dslCanvasApi.validate).not.toHaveBeenCalled();
  });

  it('本地快校验不通过时不调用导出接口（不发无意义的请求）', async () => {
    const user = userEvent.setup();
    // 一个孤立 output 节点：无入边，本地快校验放行，但导出时后端会拒。
    // 这里用「空图无法导出」验证前置拦截：导出按钮在无节点时禁用。
    render(<FlowEditor />);
    await screen.findByTestId('flow-palette-input');
    expect(screen.getByTestId('flow-export')).toBeDisabled();
    await user.click(screen.getByTestId('flow-export'));
    expect(workflowGenApi.exportScript).not.toHaveBeenCalled();
  });

  it('导出成功如实显示脚本名、占位符与来源需求', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.exportScript).mockResolvedValue({
      filename: 'fy_workflow.py',
      language: 'python',
      requires_python: '>=3.9',
      base_url_placeholder: '{{FY_BASE_URL}}',
      source_prompt: '每天读文件并发邮件',
      generated_at: '2026-10-04T00:00:00Z',
      script: 'print("hi")',
    });
    render(<FlowEditor initialDoc={VALID_DSL} sourcePrompt="每天读文件并发邮件" />);
    await screen.findByTestId('flow-node-in1');

    await user.click(screen.getByTestId('flow-export'));
    await waitFor(() => expect(workflowGenApi.exportScript).toHaveBeenCalled());
    // 传给后端的是纯 DSL，没有坐标
    expect(vi.mocked(workflowGenApi.exportScript).mock.calls[0][0]).toEqual(VALID_DSL);
    const notice = await screen.findByTestId('flow-notice');
    expect(notice.textContent).toContain('fy_workflow.py');
    expect(notice.textContent).toContain('{{FY_BASE_URL}}');
    expect(notice.textContent).toContain('不含密钥');
  });

  it('导出被后端 422 拒绝时如实显示后端错误，不假装成功', async () => {
    const user = userEvent.setup();
    vi.mocked(workflowGenApi.exportScript).mockRejectedValue(
      new ApiError(422, {
        code: 'workflow_dsl_invalid',
        message: '边 a->b 引用了未定义节点',
        details: {},
      }, 'Request failed with status 422'),
    );
    render(<FlowEditor initialDoc={VALID_DSL} />);
    await screen.findByTestId('flow-node-in1');
    await user.click(screen.getByTestId('flow-export'));
    expect((await screen.findByTestId('flow-error')).textContent).toContain('引用了未定义节点');
    expect(screen.queryByTestId('flow-notice')).toBeNull();
  });
});
