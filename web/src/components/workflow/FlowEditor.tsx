/**
 * W5 工作流工坊 · 三栏拖拽编辑器（任务的「进阶」层）。
 *
 * 三层视图共用同一个 DSL 源：
 *  - 小白层：GeneratePanel 一句话生成 → 这里的画布；
 *  - 进阶层（本文件）：拖拽节点 / 连线 / 改属性，**实时**序列化出 DSL 文本；
 *  - 开发者层：右侧只读代码视图 + 导出独立 Python 脚本。
 *
 * 关键约定
 *  - **模型 / 视图分离**：节点语义（type/verb/params）与坐标（x/y）是两套
 *    state；序列化出的 DSL **永不包含坐标**（沿用 dsl_canvas 的蓝本）。
 *  - **环检测不静默**：连线一旦成环，画布顶部立刻显示错误（本地快检），
 *    同时调用后端 `/api/dsl-canvas/validate` 做权威判定（任务书 §4）。
 *  - **非法 DSL 不半解析**：手改 DSL 文本点「载入」时，非法就整份拒绝，
 *    并把后端返回的行列级错误显示在顶部。
 *  - jsdom 没有 PointerEvent，所以几何/序列化逻辑抽成本文件的纯函数导出，
 *    组件测试用 fire 兼容写法测行为，纯函数直接单测（任务书 §4 坑）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DSL_NODE_TYPES,
  DSL_TRANSFORM_VERBS,
  dslCanvasApi,
  type DslDiagnostic,
  type DslDocument,
  type DslEdge,
  type DslMode,
  type DslNode,
  type DslNodeType,
  type DslRunResult,
  type DslSchemaResponse,
  type DslSuspendedResult,
  type DslTransformVerb,
} from '../../api/dslCanvas';
import {
  workflowGenApi,
  type WorkflowExportResponse,
} from '../../api/workflowGen';
import { NodePalette } from './NodePalette';
import { PropertyPanel } from './PropertyPanel';
import { useBase } from '../../hooks/useAutosave';

export const CANVAS_W = 760;
export const CANVAS_H = 420;
const NODE_W = 148;
const NODE_H = 52;

/**
 * 受限节点集（与后端 services/dsl_canvas.py 的 `NODE_TYPES` 一致）。
 * 从 `DSL_NODE_TYPES` 派生，前端只有一份清单。
 */
export const FLOW_NODE_TYPES: DslNodeType[] = [...DSL_NODE_TYPES];
/**
 * transform 动词门禁：与后端 `services/dsl_canvas.py` 的 `TRANSFORM_VERBS`
 * 一致，直接用 `DSL_TRANSFORM_VERBS` 真源，不再在前端另抄一份短清单。
 * （曾硬编码为 ['map','filter','template']，导致另外 7 个后端认得的动词被
 *  本地 quickValidate / runFlow 执行门禁误拒。）
 */
export const FLOW_VERBS: readonly DslTransformVerb[] = DSL_TRANSFORM_VERBS;

let seq = 0;
/** 节点 id 生成：必须匹配后端 `^[A-Za-z0-9_-]{1,64}$`。 */
export function nextNodeId(type: DslNodeType): string {
  seq += 1;
  return `${type}${seq}`;
}

/** 重置id 计数器（测试用，保证断言可预期）。 */
export function __resetNodeIdSeq(): void {
  seq = 0;
}

// --------------------------------------------------------------------------- //
// 纯函数区（无 DOM 依赖，可直接单测）
// --------------------------------------------------------------------------- //

export interface EditorNode extends DslNode {
  x: number;
  y: number;
}

export interface EditorEdge extends DslEdge {}

/**
 * 图（节点+边+坐标）→ DSL 文档。**坐标被丢弃**。
 *
 * 入参声明为 `DslNode[]` 而非 `EditorNode[]`：序列化只读语义字段
 * （id/type/verb/params），坐标有没有都无所谓。这是 DslCanvas 与
 * FlowEditor **唯一的图→DSL 序列化实现**（单一真源），两个画布必须
 * 产出逐字段相同的 DSL，否则「同一份工作流两条路径结构不一致」。
 */
export function serializeGraph(nodes: DslNode[], edges: DslEdge[]): DslDocument {
  return {
    version: '1',
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      ...(n.type === 'transform' ? { verb: n.verb ?? 'template' } : {}),
      ...(n.params && Object.keys(n.params).length ? { params: n.params } : {}),
    })),
    edges: edges.map((e) => ({
      from: e.from,
      to: e.to,
      ...(e.condition ? { condition: e.condition } : {}),
    })),
  };
}

/** DSL 文档 → 图（带坐标）。**调用方必须先校验合法性**——这里不做半解析。 */
export function graphFromDsl(doc: DslDocument, layout: Record<string, { x: number; y: number }> = {}):
  { nodes: EditorNode[]; edges: EditorEdge[] } {
  const nodes: EditorNode[] = doc.nodes.map((raw, index) => {
    const pos = layout[raw.id] ?? autoPosition(index);
    return {
      id: raw.id,
      type: raw.type,
      ...(raw.type === 'transform' ? { verb: raw.verb ?? 'template' } : {}),
      params: { ...(raw.params ?? {}) },
      x: pos.x,
      y: pos.y,
    };
  });
  const edges: EditorEdge[] = doc.edges.map((e) => ({
    from: e.from,
    to: e.to,
    ...(e.condition ? { condition: e.condition } : {}),
  }));
  return { nodes, edges };
}

/** 未给坐标时的网格排布（每行 3 个）。 */
export function autoPosition(index: number): { x: number; y: number } {
  return { x: 30 + (index % 3) * 200, y: 30 + Math.floor(index / 3) * 110 };
}

/** 把坐标夹在画布内，避免节点被拖丢。 */
export function clampPos(x: number, y: number): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(CANVAS_W - NODE_W, Math.round(x))),
    y: Math.max(0, Math.min(CANVAS_H - NODE_H, Math.round(y))),
  };
}

/** 连线几何：有向贝塞尔控制点，与 DslCanvas 的画法一致。 */
export function bezierPath(x1: number, y1: number, x2: number, y2: number): string {
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

/** 边的起止锚点（从节点右侧出、左侧入）。 */
export function edgeAnchors(
  from: EditorNode | undefined,
  to: EditorNode | undefined,
): { x1: number; y1: number; x2: number; y2: number } | null {
  if (!from || !to) return null;
  return {
    x1: from.x + NODE_W,
    y1: from.y + NODE_H / 2,
    x2: to.x,
    y2: to.y + NODE_H / 2,
  };
}

/**
 * 本地快检：这条边会不会成环？（DFS 查from 是否已可达 to）
 * 用于连线瞬间给反馈；权威判定仍以后端 validate 为准。
 */
export function wouldCreateCycle(edges: EditorEdge[], from: string, to: string): boolean {
  if (from === to) return true;
  const adj = new Map<string, string[]>();
  for (const e of [...edges, { from, to }]) {
    const list = adj.get(e.from) ?? [];
    list.push(e.to);
    adj.set(e.from, list);
  }
  const seen = new Set<string>();
  const stack = [to];
  while (stack.length) {
    const cur = stack.pop() as string;
    if (cur === from) return true;
    if (seen.has(cur)) continue;
    seen.add(cur);
    for (const next of adj.get(cur) ?? []) stack.push(next);
  }
  return false;
}

/** 与后端 validate_dsl 对齐的最小本地校验（仅用于即时反馈，权威判定在后端）。 */
export function quickValidate(doc: DslDocument): { ok: boolean; message: string } {
  if (!doc.nodes.length) return { ok: false, message: '画布为空：至少需要一个节点' };
  const ids = new Set<string>();
  for (const n of doc.nodes) {
    if (ids.has(n.id)) return { ok: false, message: `节点 id 重复: ${n.id}` };
    ids.add(n.id);
    if (!FLOW_NODE_TYPES.includes(n.type)) {
      return { ok: false, message: `节点 ${n.id} type 非法: ${n.type}` };
    }
    if (n.type === 'transform' && !FLOW_VERBS.includes(n.verb ?? 'template')) {
      return { ok: false, message: `transform 节点 ${n.id} verb 非法: ${n.verb}` };
    }
  }
  for (const e of doc.edges) {
    if (!ids.has(e.from) || !ids.has(e.to)) {
      return { ok: false, message: `边 ${e.from}->${e.to} 引用了未定义节点` };
    }
  }
  // 整图环检测（与后端 compile_dsl 的 Kahn 环检测等价）。
  const order = topoOrder(doc);
  if (!order) {
    const cyclic = doc.nodes.filter((n) => !reachesAll(doc, n.id)).map((n) => n.id);
    return { ok: false, message: `DSL 存在环，涉及节点: ${[...new Set(cyclic)].join(', ')}` };
  }
  return { ok: true, message: '' };
}

/** Kahn 拓扑排序；存在环时返回 null。 */
export function topoOrder(doc: DslDocument): string[] | null {
  const indeg = new Map<string, number>(doc.nodes.map((n) => [n.id, 0]));
  const adj = new Map<string, string[]>();
  for (const e of doc.edges) {
    adj.set(e.from, [...(adj.get(e.from) ?? []), e.to]);
    indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1);
  }
  const ready = [...indeg.entries()].filter(([, d]) => d === 0).map(([id]) => id).sort();
  const order: string[] = [];
  while (ready.length) {
    const cur = ready.shift() as string;
    order.push(cur);
    for (const next of adj.get(cur) ?? []) {
      const left = (indeg.get(next) ?? 0) - 1;
      indeg.set(next, left);
      if (left === 0) {
        ready.push(next);
        ready.sort();
      }
    }
  }
  return order.length === doc.nodes.length ? order : null;
}

/** 从 from 出发能否到达全部节点（环检测辅助，仅用于错误信息）。 */
function reachesAll(doc: DslDocument, from: string): boolean {
  const adj = new Map<string, string[]>();
  for (const e of doc.edges) adj.set(e.from, [...(adj.get(e.from) ?? []), e.to]);
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const cur = stack.pop() as string;
    if (seen.has(cur)) continue;
    seen.add(cur);
    for (const n of adj.get(cur) ?? []) stack.push(n);
  }
  return seen.size === doc.nodes.length;
}

/** 新节点的默认参数（与 DslCanvas 保持一致，避免两种模式产出不同 DSL）。 */
export function defaultParams(type: DslNodeType): Record<string, unknown> {
  switch (type) {
    case 'input':
      return { kind: 'literal', value: ['示例行'] };
    case 'transform':
      return { template: '处理：{value}' };
    case 'output':
      return { format: 'text' };
    case 'llm':
      return { model: 'dummy', prompt: '测试提示' };
    case 'knowledge_retrieval':
      return { query: '测试查询', top_k: 10, mode: 'lexical' };
    case 'question_classifier':
      return { classes: ['是', '否'], model: 'dummy' };
    case 'parameter_extractor':
      return { fields: [{ name: 'test', type: 'string' }], model: 'dummy' };
    case 'iteration':
      return {
        subflow: {
          version: '1',
          nodes: [
            { id: 'input1', type: 'input', params: { kind: 'literal', value: ['test'] } },
            { id: 'output1', type: 'output', params: { format: 'text' } }
          ],
          edges: [{ from: 'input1', to: 'output1' }]
        }
      };
    case 'loop':
      return {
        subflow: {
          version: '1',
          nodes: [
            { id: 'input1', type: 'input', params: { kind: 'literal', value: ['test'] } },
            { id: 'output1', type: 'output', params: { format: 'text' } }
          ],
          edges: [{ from: 'input1', to: 'output1' }]
        },
        max_iterations: 10
      };
    case 'variable_aggregator':
      return { strategy: 'first_non_null' };
    case 'template':
      return { template: '处理：{value}' };
    case 'http_request':
      return { url: 'https://example.com', method: 'GET' };
    case 'code':
      return { language: 'python', code: 'print("hello world")' };
    case 'tool':
      return { tool: 'example_tool' };
    case 'human_input':
      return { prompt: '请输入' };
    case 'trigger':
      return { kind: 'manual', config: {} };
    case 'parallel':
      // 分支数硬闸门在后端（PARALLEL_MAX_BRANCHES=8）；这里给两个最小可跑的
      // 内联分支，节点 id 必须互不相同（否则同一个 DSL 文档里 id 撞车）。
      return {
        branches: [
          { name: '分支1', inline: minSubflow('1') },
          { name: '分支2', inline: minSubflow('2') },
        ],
      };
    case 'subgraph':
      return { name: 'shared' };
    default:
      return {};
  }
}

/** 最小可用的内联子图文档：一个 input 入口 + 一个 output 出口。 */
function minSubflow(tag: string): Record<string, unknown> {
  return {
    version: '1',
    nodes: [
      { id: `input_${tag}`, type: 'input', params: { kind: 'literal', value: ['test'] } },
      { id: `output_${tag}`, type: 'output', params: { format: 'text' } },
    ],
    edges: [{ from: `input_${tag}`, to: `output_${tag}` }],
  };
}

/**
 * 切换 transform 动词时的配套参数（避免留下上一个动词的残留字段）。
 *
 * 每个分支都必须给出**能过后端参数契约**的完整参数：动词集是封闭的，
 * 少一个必填字段就是 422，所以这里宁可多给一个合法的默认值。
 */
export function paramsForVerb(verb: DslTransformVerb): Record<string, unknown> {
  switch (verb) {
    case 'map': return { op: 'set', field: 'tag', value: '已处理' };
    case 'filter': return { field: 'value', op: 'contains', value: '示例' };
    case 'template': return { template: '处理：{value}' };
    case 'branch':
      return { field: 'value', op: 'eq', value: '示例', then_label: '是', else_label: '否' };
    case 'aggregate': return { op: 'count', field: 'value' };
    case 'merge': return { mode: 'concat' };
    case 'agent': return { agent: 'summarizer' };
    case 'confirm': return { prompt: '请确认是否继续', role: 'owner' };
    case 'artifact': return { name: '产物', kind: 'generic' };
    default: return {};
  }
}

// --------------------------------------------------------------------------- //
// 组件
// --------------------------------------------------------------------------- //

export interface FlowEditorProps {
  /** 外部（如生成器）载入的文档；变化时载入画布。 */
  initialDoc?: DslDocument | null;
  /** 生成来源 prompt，用于导出脚本头部注释。 */
  sourcePrompt?: string;
  /**
   * 创作模式（A-三重模式-01/04）。三种模式**共用同一张图 / 同一套 IR**，
   * 差别只在复杂度开关：
   *
   * * `beginner`："小白层" —— 隐藏代码视图 / DSL 手改 / 导出（那是技术层的活），
   *   保留全部 18 类节点可拖（藏的是"怎么读图"，不是"能搭什么"）；
   * * `technical`（默认）：现有全量视图（代码、手改、导出都在）；
   * * `enterprise`：同技术层 + 治理提示（approval 动词）。
   */
  mode?: DslMode;
  /**
   * 本地文档变化后回传给页面（与 DslCanvas 共享同一条状态通道）。
   * 载荷复用下面那份唯一的 `doc`（`serializeGraph(nodes, edges)`），
   * 不造第二份序列化。
   */
  onDocChange?: (doc: DslDocument) => void;
  /**
   * 宿主是否可见（两个画布同时挂载、用 `hidden` 切页签）。
   *
   * 不可见时**只跳过防抖 IR 校验请求**（否则首屏两份文档各发一次，
   * 请求量翻倍），编辑与回传一律照旧 —— 切回可见时会立刻补一次校验。
   */
  visible?: boolean;
}

/**
 * 把收集式 IR 的 `diagnostics[]` 压成一行可定位摘要（`节点 x · 字段 path`）。
 *
 * G4-g2：`/validate` 的统一错误信封里 `details` 恒为 `{}`（见
 * `api/errors.py::_envelope`），所以 `details.line/column` 永远拿不到；
 * 真正带定位的是 `/validate-ir` 的 `diagnostics[]`（`node_id` + `field_path`）。
 *
 * 一次全量拼接，绝不只报第一条（与 `DslDiagnostics` 的铁律一致）。
 * 无诊断时返回空串 —— **宁可没有位置，也不编造位置**。
 */
export function formatDiagnosticLocations(diagnostics: DslDiagnostic[]): string {
  return diagnostics
    .map((d) => `${d.node_id || '文档'} · ${d.field_path || '(整份文档)'}`)
    .join('；');
}

/** 防抖 IR 校验「请求失败」用的错误条标题。独立常量：既让标题如实说明是
 * 「请求失败」而非「DSL 不合法」，也让下一次校验成功时只清自己挂上去的那一条。
 */
const IR_CHECK_FAILED = 'DSL 校验未完成（校验请求失败）';

/** G4-d · 文档变化后等多久发起即时重算。
 *
 * 属性面板是**逐击键**提交的（`onChangeParams` 每敲一个字符就改一次 `doc`），
 * 没有防抖就是每个键一次请求。所以防抖必须落在画布这一层，绝不能下推到面板。
 * 300ms 与 IR 校验一致：比人的连续击键间隔长，比「改完看一眼」的间隔短。
 */
const PREVIEW_DEBOUNCE_MS = 300;

/** 即时重算「请求失败」用的错误条标题。与运行结果分开存，不互相顶掉。 */
const PREVIEW_FAILED = '即时重算未完成（预览请求失败）';

/** 画布顶部错误条（含行列级信息）。`title` 可覆盖标题：请求失败 ≠ 校验不通过。 */
function ErrorBanner({
  title = 'DSL 校验未通过',
  message,
  detail,
}: {
  title?: string;
  message: string;
  detail?: string;
}) {
  return (
    <div className="fy-flow-error" role="alert" data-testid="flow-error">
      <strong>{title}</strong>
      <span>{message}</span>
      {detail && <code data-testid="flow-error-pos">{detail}</code>}
    </div>
  );
}

export function FlowEditor({ initialDoc, sourcePrompt, mode = 'technical', onDocChange, visible = true }: FlowEditorProps) {
  // A-基座质保-01：声明式接入基座（声明 + 保存指示器由 useBase 统一产出）。
  useBase({ surface: 'web/src/components/workflow/FlowEditor' });
  const [nodes, setNodes] = useState<EditorNode[]>([]);
  const [edges, setEdges] = useState<EditorEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [error, setError] = useState<{ title?: string; message: string; detail: string } | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [schemaTypes, setSchemaTypes] = useState<DslNodeType[]>(FLOW_NODE_TYPES);
  /** G4-j · 后端 schema 原始返回体（含 node_params_schema / verb_catalog），
   * 注入属性面板驱动参数枚举下拉；拉取失败时保持 null，面板如实回落本地常量。 */
  const [schema, setSchema] = useState<DslSchemaResponse | null>(null);
  const [dslText, setDslText] = useState('');
  const [exported, setExported] = useState<WorkflowExportResponse | null>(null);
  /** A-三重模式-01：运行结果（小白全流程的最后一环）。 */
  const [run, setRun] = useState<DslRunResult | null>(null);
  const [runError, setRunError] = useState('');
  /** 挂起**不是失败**：走到人工裁决点的运行要如实显示为「等裁决」。 */
  const [suspended, setSuspended] = useState<DslSuspendedResult['suspended'] | null>(null);
  /** P1 · 后端收集式 IR 校验的全部诊断（按 field_path 落到属性面板字段）。 */
  const [irDiagnostics, setIrDiagnostics] = useState<DslDiagnostic[]>([]);
  /** G4-d · 即时重算结果。与 {@link run} 分开存：混在一起会让「预览里改了参数」
   * 顺手把上一次真实运行的结果覆盖掉，用户点运行前后看到的东西对不上号。 */
  const [preview, setPreview] = useState<DslRunResult | null>(null);
  /** 预览挂起也是控制流信号，不是失败 —— 但**不是**运行挂起，不共用同一处展示。 */
  const [previewSuspended, setPreviewSuspended] = useState<DslSuspendedResult['suspended'] | null>(null);

  /**
   * G4-i · R10：「怎么读图」这一层技术面（代码 / 手改 / 导出）在**所有模式
   * 下都渲染**，小白模式只是收进折叠区——折叠而非卸载，收起态内容仍在 DOM，
   * 页面级同源断言与读屏都还能命中。
   *
   * （此前这里是 `const showAdvanced = mode !== 'beginner'` 直接不渲染，
   *  与 `DslCanvasPage.test.tsx` 里 beginner 模式的同源断言冲突——已实测红。）
   */

  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);

  // 文档（DSL 语义）—— 视图坐标永不进入。
  const doc = useMemo(() => serializeGraph(nodes, edges), [nodes, edges]);
  const dslTextValue = useMemo(() => JSON.stringify(doc, null, 2), [doc]);
  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId]);

  // P1 · 文档一变就防抖调收集式 IR 校验；请求失败**不阻塞编辑**，但绝不静默：
  // 「校验请求失败」与「校验通过」必须在界面上可区分，否则属性面板的诊断区
  // 会因为「没报错」而被当成「没问题」（权威拦截仍在执行路径与「载入」的
  // validate 调用上）。
  useEffect(() => {
    // 不可见的那一半不校验：两个画布同时挂载且共用同一份文档，不加这道闸
    // 首屏就会对同一份图发两次 `validateIr`。切回可见时依赖变化会补一次。
    if (!visible) return undefined;
    const t = window.setTimeout(() => {
      if (typeof dslCanvasApi?.validateIr === 'function') {
        dslCanvasApi.validateIr(doc)
          .then((r) => {
            setIrDiagnostics(r?.diagnostics ?? []);
            // 只清掉本项自己挂上去的错误，不动用户其他操作产生的错误。
            setError((prev) => (prev?.title === IR_CHECK_FAILED ? null : prev));
          })
          .catch((e: unknown) => {
            const err = e as { body?: { message?: string } };
            setError({
              title: IR_CHECK_FAILED,
              message: `${err.body?.message ?? (e as Error).message ?? '未知原因'}；属性面板的诊断可能不完整`,
              detail: '',
            });
          });
      }
    }, 300);
    return () => window.clearTimeout(t);
  }, [doc, visible]);

  // G4-d · 改参数后即时重算：文档一变就防抖打一次 `POST /preview`。
  //
  // 后端跑的是**同一套**执行器、同一份文档，唯一不做的是 `_store.save()`；所以
  // 「预览里能跑通」与「点运行也能跑通」在语义上同源，不存在旁路近似。
  //
  // 三条边界都在这守住：
  // 1. 不在属性面板做防抖 —— 面板逐击键提交，防抖只能落在画布这层。
  // 2. 请求失败**不阻塞编辑**，但要如实显示：失败与「跑通了」在界面上必须可区分，
  //    否则用户会把过期结果当成新参数的结论。旧结果同时清掉，不拿旧值冒充新值。
  // 3. 图还没成形（空画布 / 本地快检不通过）不发请求：后端 `validate_dsl` 要求
  //    `nodes` 非空，空图会 422，白白在界面上弹一个红条。
  useEffect(() => {
    if (!visible) return undefined;
    if (!nodes.length) {
      setPreview(null);
      setPreviewSuspended(null);
      return undefined;
    }
    const local = quickValidate(doc);
    if (!local.ok) {
      setPreview(null);
      setPreviewSuspended(null);
      return undefined;
    }
    const t = window.setTimeout(() => {
      if (typeof dslCanvasApi?.preview !== 'function') return;
      dslCanvasApi.preview(doc)
        .then((res) => {
          setPreviewSuspended(res.status === 'suspended' ? res.suspended : null);
          setPreview(res.status === 'suspended' ? null : res);
          setError((prev) => (prev?.title === PREVIEW_FAILED ? null : prev));
        })
        .catch((e: unknown) => {
          const err = e as { body?: { message?: string } };
          // 旧结果必须同时作废：留着上一份参数的输出，界面就成了假账。
          setPreview(null);
          setPreviewSuspended(null);
          setError({
            title: PREVIEW_FAILED,
            message: `${err.body?.message ?? (e as Error).message ?? '未知原因'}；下面显示的即时结果可能不是当前参数的`,
            detail: '',
          });
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, visible, nodes.length]);

  const loadDoc = useCallback((next: DslDocument) => {
    const parsed = graphFromDsl(next);
    setNodes(parsed.nodes);
    setEdges(parsed.edges);
    setSelectedId(null);
    setError(null);
  }, []);

  // 外部（生成器）载入文档：仅在文档**对象引用**变化时载入，避免拖拽被回滚。
  useEffect(() => {
    if (initialDoc) loadDoc(initialDoc);
  }, [initialDoc, loadDoc]);

  /**
   * 本地图变化 → 回传给页面（与 DslCanvas 共享同一条状态通道）。
   *
   * 载荷复用上面那份唯一的 `doc`，不造第二份序列化。
   *
   * 防循环：`onDocChange(doc)` 走页面 → `setDoc` → `initialDoc` 引用变 →
   * 本组件 `loadDoc` 重建 nodes/edges → doc 再变，看起来会绕圈。收敛同样靠页面侧
   * 那道「内容等价就不换引用」的闸（载入后产出的文档与传进来的逐字段相同）。
   * 拖拽只改 x/y：doc 会换引用但内容不变，页面因此不 setDoc，拖拽既不会被回滚，
   * 也不会顺着通道在两个画布间反复重载。
   */
  useEffect(() => {
    if (onDocChange) onDocChange(doc);
  }, [doc, onDocChange]);

  // 左侧面板的节点类型：动态取后端 schema；失败时如实回落到本地受限集。
  useEffect(() => {
    let alive = true;
    void dslCanvasApi.schema()
      .then((s) => {
        if (!alive) return;
        // G4-j · 保留整份 schema（含 node_params_schema / verb_catalog），注入
        // 属性面板作参数枚举的真源；面板内部的本地常量只是拉取失败时的回落。
        setSchema(s);
        // 受限集只认 FLOW_NODE_TYPES：认得的类型**全部保留**（不多不少），
        // 认不出的（旧前端 / 认领错位）不再是静默丢弃——它们会被点名报给用户，
        // 免得面板少了一类却什么都不说。
        const known = (s.node_types ?? []).filter((t): t is DslNodeType =>
          FLOW_NODE_TYPES.includes(t as DslNodeType));
        const unknown = (s.node_types ?? []).filter((t) => !FLOW_NODE_TYPES.includes(t as DslNodeType));
        if (known.length) setSchemaTypes(known);
        if (unknown.length) {
          setError({
            title: '节点类型清单未全部接入',
            message: `有 ${unknown.length} 个节点类型当前画布不支持：${unknown.join('、')}`,
            detail: '',
          });
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        // 不是致命错误（面板仍可用），但必须让用户知道它在用本地回落清单，
        // 否则「面板少了一类」和「后端真的没有这一类」看起来一模一样。
        const err = e as { body?: { message?: string } };
        setError({
          title: '节点类型清单拉取失败',
          message: `已回落到本地清单：${err.body?.message ?? (e as Error).message ?? '未知原因'}`,
          detail: '',
        });
      });
    return () => { alive = false; };
  }, []);

  const addNode = useCallback((type: DslNodeType, at?: { x: number; y: number }) => {
    const id = nextNodeId(type);
    const pos = clampPos(at?.x ?? 30 + (nodes.length % 3) * 200, at?.y ?? 30 + Math.floor(nodes.length / 3) * 110);
    setNodes((ns) => [...ns, {
      id, type,
      ...(type === 'transform' ? { verb: 'template' as DslTransformVerb } : {}),
      params: defaultParams(type),
      ...pos,
    }]);
    setSelectedId(id);
    setError(null);
  }, [nodes.length]);

  const onNodePointerDown = (e: React.PointerEvent, id: string) => {
    const target = nodes.find((n) => n.id === id);
    if (!target) return;
    dragRef.current = { id, dx: e.clientX - target.x, dy: e.clientY - target.y };
    setSelectedId(id);
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const pos = clampPos(e.clientX - d.dx, e.clientY - d.dy);
    setNodes((ns) => ns.map((n) => (n.id === d.id ? { ...n, ...pos } : n)));
  };

  const onPointerUp = () => { dragRef.current = null; };

  const connect = (from: string, to: string) => {
    if (edges.some((e) => e.from === from && e.to === to)) {
      setConnectFrom(null);
      return;
    }
    // 成环 → 立刻在顶部报错，绝不静默接受（任务书 §4）。
    if (wouldCreateCycle(edges, from, to)) {
      setError({ message: `连线 ${from} → ${to} 会形成环，已拒绝`, detail: '' });
      setConnectFrom(null);
      return;
    }
    setEdges((es) => [...es, { from, to }]);
    setError(null);
    setConnectFrom(null);
  };

  const handlePort = (id: string, port: 'in' | 'out') => {
    if (port === 'out') {
      setConnectFrom((cur) => (cur === id ? null : id));
      return;
    }
    if (!connectFrom || connectFrom === id) { setConnectFrom(null); return; }
    connect(connectFrom, id);
  };

  const removeNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.from !== id && e.to !== id));
    setSelectedId((cur) => (cur === id ? null : cur));
    setError(null);
  };

  const updateNode = (id: string, patch: Partial<EditorNode>) => {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, ...patch } : n)));
  };

  // --- 载入 DSL（反向解析，非法整份拒绝 + 行列级错误） --------------------- //
  const loadFromText = useCallback(async () => {
    setError(null);
    setNotice('');
    const text = dslText || dslTextValue;
    let parsed: DslDocument;
    try {
      parsed = JSON.parse(text) as DslDocument;
    } catch (e) {
      setError({ message: `DSL 文本不是合法 JSON：${(e as Error).message}`, detail: '' });
      return;
    }
    try {
      // 服务端权威校验（含环检测）；失败时把真实错误与可定位信息显示在顶部。
      await dslCanvasApi.validate(parsed);
    } catch (e) {
      const err = e as { body?: { message?: string } };
      const message = err.body?.message ?? 'DSL 校验未通过';
      // G4-g2：`/validate` 的统一错误信封 `details` 恒为 `{}`（`api/errors.py::_envelope`），
      // 读不到行列；改用收集式 `/validate-ir` 的 `diagnostics[]`（node_id + field_path）
      // 补一次可定位诊断。权威闸门仍是 `/validate`（fail-fast、含环检测），这里只补信息。
      let detail = '';
      try {
        const ir = await dslCanvasApi.validateIr(parsed);
        detail = formatDiagnosticLocations(ir?.diagnostics ?? []);
      } catch {
        // 连收集式校验都拿不到 —— 宁可没有位置，也不编造位置。
        detail = '';
      }
      setError({ message, detail });
      return;
    }
    loadDoc(parsed);
    setNotice('已载入 DSL');
  }, [dslText, dslTextValue, loadDoc]);

  // --- 导出脚本 ------------------------------------------------------------ //
  const doExport = useCallback(async () => {
    setBusy(true);
    setError(null);
    setNotice('');
    const local = quickValidate(doc);
    if (!local.ok) {
      setError({ message: local.message, detail: '' });
      setBusy(false);
      return;
    }
    try {
      const res = await workflowGenApi.exportScript(doc, sourcePrompt);
      setExported(res);
      setNotice(`已生成 ${res.filename}（不含密钥，平台地址为占位符 ${res.base_url_placeholder}）`);
      downloadScript(res.filename, res.script);
    } catch (e) {
      const err = e as { body?: { message?: string } };
      setError({ message: err.body?.message ?? '导出失败', detail: '' });
    } finally {
      setBusy(false);
    }
  }, [doc, sourcePrompt]);

  // --- 运行（小白全流程的最后一环）--------------------------------------- //
  //
  // 本地快检只是体验；权威判定在后端。后端 422（DSL 不合法）与 500（执行失败）
  // 都原样显示，**绝不拿旧结果冒充「跑通了」**。
  const runFlow = useCallback(async () => {
    setBusy(true);
    setError(null);
    setNotice('');
    setRun(null);
    setRunError('');
    setSuspended(null);
    const local = quickValidate(doc);
    if (!local.ok) {
      setError({ message: local.message, detail: '' });
      setBusy(false);
      return;
    }
    try {
      // `/api/dsl-canvas/runs` 有两条**都是成功**的分支：200 直达结果 / 202
      // 挂起（走到 confirm|approval 且尚无人工裁决）。202 也算 res.ok，所以
      // 这里必须自己分流；否则企业模式的 approval 与 confirm 会被红字误报成
      // 「执行失败」——把等裁决说成失败，就是拿假状态糊弄用户。
      const res = (await dslCanvasApi.run(doc)) as DslRunResult | DslSuspendedResult;
      if (res.status === 'suspended') {
        setSuspended(res.suspended);
        return;
      }
      setRun(res);
      if (res.status !== 'succeeded') {
        setRunError(res.error ?? '执行失败');
      }
    } catch (e) {
      const err = e as { body?: { message?: string } };
      setRunError(err.body?.message ?? '运行失败');
    } finally {
      setBusy(false);
    }
  }, [doc]);

  /** 触发浏览器下载（jsdom 下无 URL.createObjectURL，静默跳过即可）。 */
  function downloadScript(filename: string, script: string): void {
    if (typeof document === 'undefined' || typeof URL.createObjectURL !== 'function') return;
    const blob = new Blob([script], { type: 'text/x-python;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  /** 把生成的脚本原文塞进可复制的textarea（下载之外的第二条路）。 */
  const onScriptText = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setExported((prev) => (prev ? { ...prev, script: e.target.value } : prev));
  };

  /**
   * G4-i：「怎么读图」这一层技术面（代码视图 / 手改 DSL / 导出脚本）。
   * 抽成变量是因为外层容器要按模式在 `<div class="card">` 与
   * `<details class="card">` 之间切换，而**内容必须逐字共用一份**——
   * 复制两份就等于给「两个画布同源」埋一个只差一个字符的坑。
   */
  const advancedBody = (
    <>
      <div className="fy-flow-code-head">
        <strong>代码视图（只读，实时同步）</strong>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => void doExport()}
          disabled={busy || !nodes.length}
          data-testid="flow-export"
        >{busy ? '导出中…' : '导出 Python 脚本'}</button>
      </div>
      <textarea
        className="fy-flow-code"
        readOnly
        rows={10}
        value={dslTextValue}
        data-testid="flow-code"
        aria-label="DSL 代码视图"
      />
      <div className="fy-flow-load">
        <textarea
          className="fy-flow-code"
          rows={6}
          value={dslText}
          placeholder="在此粘贴 / 手改 DSL JSON，点「载入 DSL」反向解析成图。非法会整份拒绝并给出行列级错误。"
          onChange={(e) => setDslText(e.target.value)}
          data-testid="flow-dsl-input"
          aria-label="可编辑的 DSL 文本"
        />
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => void loadFromText()}
          data-testid="flow-load"
        >载入 DSL</button>
      </div>
      {notice && <div className="fy-flow-notice" data-testid="flow-notice">{notice}</div>}
      {exported && (
        <details className="fy-flow-exported" data-testid="flow-exported">
          <summary>已导出脚本（{exported.filename}）</summary>
          <div className="muted">
            来源需求：{exported.source_prompt}｜生成时间：{exported.generated_at}
          </div>
          <textarea
            className="fy-flow-code"
            rows={10}
            value={exported.script}
            onChange={onScriptText}
            data-testid="flow-export-script"
            aria-label="导出的 Python 脚本"
          />
        </details>
      )}
    </>
  );

  return (
    <div className="fy-flow" data-testid="flow-editor-root">
      <NodePalette types={schemaTypes} onAdd={addNode} onDropNode={addNode} />

      <div className="fy-flow-center">
        {error && <ErrorBanner title={error.title} message={error.message} detail={error.detail} />}

        {/* A-三重模式-01：运行是三种模式共有的一步（小白也要能跑到底）。 */}
        <div className="fy-flow-runbar" data-testid="flow-runbar" data-mode={mode}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => void runFlow()}
            disabled={busy || !nodes.length}
            data-testid="flow-run"
          >{busy ? '运行中…' : '运行'}</button>
          {run && run.status === 'succeeded' && (
            <span className="fy-flow-run-ok" data-testid="flow-run-ok">
              运行 <code>{run.run_id}</code> 成功
            </span>
          )}
          {suspended && (
            <span className="fy-flow-run-suspended" data-testid="flow-run-suspended">
              已挂起，等待人工裁决（节点 <code>{suspended.node_id}</code>）：
              {suspended.context.prompt || '需要你的裁决'}
            </span>
          )}
          {runError && (
            <span className="fy-flow-run-err" role="alert" data-testid="flow-run-error">
              {runError}
            </span>
          )}
          {run && run.status === 'succeeded' && (
            <pre className="fy-flow-run-out" data-testid="flow-run-output">
              {JSON.stringify(run.output, null, 2)}
            </pre>
          )}
        </div>

        <div
          className="fy-flow-canvas"
          style={{ width: CANVAS_W, height: CANVAS_H }}
          data-testid="flow-canvas"
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        >
          <svg width={CANVAS_W} height={CANVAS_H} className="fy-flow-edges" aria-hidden="true">
            <defs>
              <marker id="fy-flow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L7,3 L0,6 z" fill="currentColor" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const a = edgeAnchors(nodes.find((n) => n.id === e.from), nodes.find((n) => n.id === e.to));
              if (!a) return null;
              return (
                <path
                  key={`${e.from}-${e.to}-${i}`}
                  d={bezierPath(a.x1, a.y1, a.x2, a.y2)}
                  className="fy-flow-edge"
                  markerEnd="url(#fy-flow-arrow)"
                  data-testid={`flow-edge-${e.from}-${e.to}`}
                />
              );
            })}
          </svg>

          {nodes.map((n) => (
            <div
              key={n.id}
              className={`fy-flow-node type-${n.type}${selectedId === n.id ? ' is-selected' : ''}`}
              style={{ left: n.x, top: n.y, width: NODE_W, minHeight: NODE_H }}
              data-testid={`flow-node-${n.id}`}
              onPointerDown={(e) => onNodePointerDown(e, n.id)}
            >
              <div className="fy-flow-node-head">
                <span>{n.type}{n.type === 'transform' ? ` · ${n.verb ?? 'template'}` : ''}</span>
                <button
                  type="button"
                  className="fy-flow-del"
                  title={`删除 ${n.id}`}
                  onClick={(e) => { e.stopPropagation(); removeNode(n.id); }}
                  data-testid={`flow-delete-${n.id}`}
                >×</button>
              </div>
              <div className="fy-flow-node-id">{n.id}</div>
              <button
                type="button"
                className={`fy-flow-port in${connectFrom === n.id ? ' is-armed' : ''}`}
                title="入"
                onClick={(e) => { e.stopPropagation(); handlePort(n.id, 'in'); }}
                data-testid={`flow-in-${n.id}`}
              />
              <button
                type="button"
                className={`fy-flow-port out${connectFrom === n.id ? ' is-armed' : ''}`}
                title="出"
                onClick={(e) => { e.stopPropagation(); handlePort(n.id, 'out'); }}
                data-testid={`flow-out-${n.id}`}
              />
            </div>
          ))}

          {nodes.length === 0 && (
            <div className="fy-flow-empty" data-testid="flow-empty">
              从左侧点选或拖入节点开始 —— 或者用上方「一句话生成」。
            </div>
          )}
        </div>

        <div className="fy-flow-toolbar">
          <span className="muted">
            连线：点节点右侧「出」→ 点目标节点左侧「入」。成环会被拒绝并在顶部提示。
          </span>
        </div>
      </div>

      <div className="fy-flow-side">
        <PropertyPanel
          node={selected}
          diagnostics={irDiagnostics}
          schema={schema}
          onChange={(patch) => selected && updateNode(selected.id, patch)}
          onChangeParams={(patch) =>
            selected && updateNode(selected.id, { params: { ...selected.params, ...patch } })}
        />

        {/* G4-d · 即时重算结果。放在属性面板正下方：改参数的人正在那儿，
            视线不需要跳到画布顶部。标题写明「预览不落库」，避免与运行结果混淆。 */}
        {(preview || previewSuspended) && (
          <div className="card" data-testid="flow-preview">
            <strong>即时重算（预览，不落库）</strong>
            {previewSuspended && (
              <p className="muted" data-testid="flow-preview-suspended">
                此图需人工裁决后才能跑完（节点 <code>{previewSuspended.node_id}</code>）：
                {previewSuspended.context.prompt || '需要你的裁决'}。预览不会替你决定。
              </p>
            )}
            {preview && preview.status === 'succeeded' && (
              <pre className="fy-flow-run-out" data-testid="flow-preview-output">
                {JSON.stringify(preview.output, null, 2)}
              </pre>
            )}
            {preview && preview.status === 'failed' && (
              <span className="fy-flow-run-err" role="alert" data-testid="flow-preview-error">
                {preview.error ?? '执行失败'}
              </span>
            )}
            {/* 逐步日志按节点展示：调参时最需要知道的是「错在哪一步」。 */}
            {preview && preview.logs.length > 0 && (
              <ul className="fy-flow-preview-logs" data-testid="flow-preview-logs">
                {preview.logs.map((l) => (
                  <li key={l.node_id} data-status={l.status}>
                    <code>{l.node_id}</code>
                    {l.status !== 'succeeded' && <em> · {l.error ?? l.status}</em>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* G4-i：小白模式把这层技术面收进 `<details>` 折叠区——折叠而非卸载，
            收起态内容仍在 DOM（页面级同源断言与读屏都还能命中）。
            非小白模式保持原来的 `<div class="card">`，DOM 结构零变化。 */}
        {mode === 'beginner' ? (
          <details className="card" data-testid="flow-code-card">
            <summary style={{ cursor: 'pointer' }} data-testid="flow-code-summary">
              技术详情（代码视图 / 手改 DSL / 导出脚本）
            </summary>
            {advancedBody}
          </details>
        ) : (
          <div className="card" data-testid="flow-code-card">{advancedBody}</div>
        )}
      </div>
    </div>
  );
}