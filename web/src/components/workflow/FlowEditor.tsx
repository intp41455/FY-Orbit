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
  dslCanvasApi,
  type DslDiagnostic,
  type DslDocument,
  type DslEdge,
  type DslNode,
  type DslNodeType,
  type DslTransformVerb,
} from '../../api/dslCanvas';
import {
  workflowGenApi,
  type WorkflowExportResponse,
} from '../../api/workflowGen';
import { NodePalette } from './NodePalette';
import { PropertyPanel } from './PropertyPanel';

export const CANVAS_W = 760;
export const CANVAS_H = 420;
const NODE_W = 148;
const NODE_H = 52;

/** 受限动词集（与后端 services/dsl_canvas.py 一致；面板另有动态 schema 拉取）。 */
export const FLOW_NODE_TYPES: DslNodeType[] = ['input', 'transform', 'output'];
export const FLOW_VERBS: DslTransformVerb[] = ['map', 'filter', 'template'];

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

/** 图（节点+边+坐标）→ DSL 文档。**坐标被丢弃**。 */
export function serializeGraph(nodes: EditorNode[], edges: EditorEdge[]): DslDocument {
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
  if (type === 'input') return { kind: 'literal', value: ['示例行'] };
  if (type === 'transform') return { template: '处理：{value}' };
  return { format: 'text' };
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
}

/** 画布顶部错误条（含行列级信息）。 */
function ErrorBanner({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="fy-flow-error" role="alert" data-testid="flow-error">
      <strong>DSL 校验未通过</strong>
      <span>{message}</span>
      {detail && <code data-testid="flow-error-pos">{detail}</code>}
    </div>
  );
}

export function FlowEditor({ initialDoc, sourcePrompt }: FlowEditorProps) {
  const [nodes, setNodes] = useState<EditorNode[]>([]);
  const [edges, setEdges] = useState<EditorEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; detail: string } | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [schemaTypes, setSchemaTypes] = useState<DslNodeType[]>(FLOW_NODE_TYPES);
  const [dslText, setDslText] = useState('');
  const [exported, setExported] = useState<WorkflowExportResponse | null>(null);
  /** P1 · 后端收集式 IR 校验的全部诊断（按 field_path 落到属性面板字段）。 */
  const [irDiagnostics, setIrDiagnostics] = useState<DslDiagnostic[]>([]);

  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);

  // 文档（DSL 语义）—— 视图坐标永不进入。
  const doc = useMemo(() => serializeGraph(nodes, edges), [nodes, edges]);
  const dslTextValue = useMemo(() => JSON.stringify(doc, null, 2), [doc]);
  const selected = useMemo(() => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId]);

  // P1 · 文档一变就防抖调收集式 IR 校验；请求失败不阻塞编辑
  // （权威拦截仍在执行路径与「载入」的 validate 调用上）。
  useEffect(() => {
    const t = window.setTimeout(() => {
      dslCanvasApi.validateIr(doc)
        .then((r) => setIrDiagnostics(r.diagnostics))
        .catch(() => undefined);
    }, 300);
    return () => window.clearTimeout(t);
  }, [doc]);

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

  // 左侧面板的节点类型：动态取后端 schema；失败时如实回落到本地受限集。
  useEffect(() => {
    let alive = true;
    void dslCanvasApi.schema()
      .then((s) => {
        if (!alive) return;
        const types = (s.node_types ?? []).filter((t): t is DslNodeType =>
          FLOW_NODE_TYPES.includes(t as DslNodeType));
        if (types.length) setSchemaTypes(types);
      })
      .catch(() => { /* schema 不可用不是致命错误：用本地受限集并保持可用 */ });
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
      // 服务端权威校验（含环检测）；失败时把真实错误与行列号显示在顶部。
      await dslCanvasApi.validate(parsed);
    } catch (e) {
      const err = e as { body?: { message?: string; details?: Record<string, unknown> } };
      const line = err.body?.details?.line;
      const column = err.body?.details?.column;
      setError({
        message: err.body?.message ?? 'DSL 校验未通过',
        detail: typeof line === 'number' ? `第 ${line} 行${typeof column === 'number' ? ` 第 ${column} 列` : ''}` : '',
      });
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

  return (
    <div className="fy-flow" data-testid="flow-editor-root">
      <NodePalette types={schemaTypes} onAdd={addNode} onDropNode={addNode} />

      <div className="fy-flow-center">
        {error && <ErrorBanner message={error.message} detail={error.detail} />}
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
          onChange={(patch) => selected && updateNode(selected.id, patch)}
          onChangeParams={(patch) =>
            selected && updateNode(selected.id, { params: { ...selected.params, ...patch } })}
        />

        <div className="card">
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
            <details className="fy-flow-exported">
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
        </div>
      </div>
    </div>
  );
}