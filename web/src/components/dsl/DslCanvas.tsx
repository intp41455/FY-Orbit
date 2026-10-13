/**
 * P1-18 受限 DSL 画布（全栈工单的前端半）。
 *
 * 蓝本（claw-dialogue-extraction §1.2/§1.3）：
 *  - 视图/模型分离：DslDocument（语义模型）与 LayoutState（坐标）是两个
 *    独立的 state；生成 DSL 时只序列化模型，坐标永不进入 DSL。
 *  - 受限动词集：input / transform(10 个受限 verb，含治理类 approval) /
 *    output，节点面板只暴露这三类；连线表示数据流（from → to）。
 *    动词清单单一真源是 `api/dslCanvas.ts` 的 `DSL_TRANSFORM_VERBS` 常量
 *    （B5 修复：本组件不再自抄一份下拉清单，杜绝与后端注册表三方漂移）。
 *  - 同源实时：代码视图的 JSON 由画布那份 nodes/edges 直接派生（不另存
 *    一份 state），序列化复用 FlowEditor 的 `serializeGraph` 单一实现，
 *    所以「画布 / 代码视图 / 执行」三条路径产出逐字段相同的 DSL。
 *    「执行」调后端真实执行并回显逐步日志。
 *  - G4-f：「生成 DSL」不再是空壳——它打开一句话生成面板（复用工坊
 *    同一个 `GeneratePanel`，走后端真实 `/api/workflow/generate`），并把
 *    焦点交给需求输入框。面板默认收起，**展开它不会碰代码视图那份 JSON**
 *    （同源闸门不许被按钮重新引入）；只有生成成功时才用返回的 DSL 替换
 *    画布，那一刻代码视图随之自动更新。
 *  - G4-i：本组件也分三重模式（R10）。此前 `DslCanvasPage` 只把 `mode`
 *    传给 `FlowEditor`，文本画布这边不接，于是同一个小白用户在「DSL 文本
 *    模式」页签仍能一眼看到那份同源 JSON —— 模式在页面上被绕过。小白模式
 *    下把它收进 `<details>` 折叠区，**折叠而非卸载**：收起态内容仍在 DOM，
 *    页面级同源断言与读屏/搜索都还能命中（语义同既有「已导出脚本」折叠区）。
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
  type DslRunLog,
  type DslRunResult,
  type DslRunStreamStart,
  type DslTransformVerb,
  type LayoutState,
} from '../../api/dslCanvas';
import { openDslRunStream, type DslRunStream } from '../../api/dslRunStream';
import { defaultParams, graphFromDsl, paramsForVerb, serializeGraph } from '../workflow/FlowEditor';
import { GeneratePanel } from '../workflow/GeneratePanel';
import { DslDiagnostics, diagnosticsForNode, nodeDiagnosticTitle } from './DslDiagnostics';
import { useBase } from '../../hooks/useAutosave';

/** 编辑后防抖再调后端 IR 校验的间隔（ms）；测试里可用真实定时器 + waitFor。 */
export const IR_CHECK_DEBOUNCE_MS = 300;

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}${seq}`;
}

/**
 * 节点类型展示名（A-画布搭建器-01：认全 `NODE_TYPES` 的 18 类）。
 * `Record<DslNodeType, string>` 是穷尽式的：后端新增一类而前端漏文案即 tsc 报错。
 */
const NODE_LABELS: Record<DslNodeType, string> = {
  input: '输入 input',
  transform: '变换 transform',
  output: '输出 output',
  llm: '模型 llm',
  knowledge_retrieval: '知识检索',
  question_classifier: '问题分类',
  parameter_extractor: '参数抽取',
  iteration: '迭代 iteration',
  loop: '循环 loop',
  variable_aggregator: '变量汇聚',
  template: '模板 template',
  http_request: 'HTTP 请求',
  code: '代码 code',
  tool: '工具 tool',
  human_input: '人工输入',
  trigger: '触发器 trigger',
  parallel: '并行分支 parallel',
  subgraph: '子图引用 subgraph',
};

/**
 * 动词的中文说明（下拉项文案；键由 `Record<DslTransformVerb, string>` 强制
 * 与单一真源 `DSL_TRANSFORM_VERBS` 穷尽对齐——新增动词漏文案即 tsc 报错）。
 */
const VERB_LABELS: Record<DslTransformVerb, string> = {
  map: '逐条映射',
  filter: '条件过滤',
  template: '模板插值',
  branch: '条件分支',
  aggregate: '循环聚合',
  merge: '并行汇聚',
  agent: '调用 Agent，未接解析器会失败',
  confirm: '人工确认，HITL 未接入会失败',
  artifact: '产出产物',
  approval: '治理审批，挂起待裁决（ADR-04）',
};

/**
 * 节点类型配色。原先 input 用 #2f6f4f（绿），违反基线「禁止绿色」，
 * 已换成九档令牌里的琥珀；三种类型仍保持色相分离（琥珀 / 天蓝 / 紫灰），
 * 不会因去绿而变得难以区分。走 var() 顺带清掉三处裸色。
 */
/**
 * 节点类型配色（九档令牌；只吃 var() 语义色，不写裸色）。
 *
 * 新增 13 类沿用同一套九档令牌：不新增色相、不写裸色，只按「数据 / 流程 /
 * 外部依赖 / 人机」分组取色，保证色相分离且全部来自 tokens.css。
 */
const NODE_COLORS: Record<DslNodeType, string> = {
  input: 'var(--ui-st-waiting)',
  transform: 'var(--ui-sky-600)',
  output: 'var(--ui-st-external)',
  llm: 'var(--ui-st-external)',
  knowledge_retrieval: 'var(--ui-st-external)',
  question_classifier: 'var(--ui-st-external)',
  parameter_extractor: 'var(--ui-st-external)',
  iteration: 'var(--ui-sky-600)',
  loop: 'var(--ui-sky-600)',
  variable_aggregator: 'var(--ui-sky-600)',
  template: 'var(--ui-sky-600)',
  http_request: 'var(--ui-st-external)',
  code: 'var(--ui-sky-600)',
  tool: 'var(--ui-st-external)',
  human_input: 'var(--ui-st-waiting)',
  trigger: 'var(--ui-st-waiting)',
  parallel: 'var(--ui-sky-600)',
  subgraph: 'var(--ui-sky-600)',
};

const CANVAS_W = 640;
const CANVAS_H = 380;

const styles: Record<string, React.CSSProperties> = {
  root: { display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' },
  palette: { display: 'flex', flexDirection: 'column', gap: 8, minWidth: 150 },
  paletteItem: {
    border: '1px solid #888', borderRadius: 6, padding: '8px 10px',
    cursor: 'grab', background: '#fff', textAlign: 'center', userSelect: 'none',
  },
  canvas: {
    position: 'relative', width: CANVAS_W, height: CANVAS_H,
    border: '1px solid #999', borderRadius: 8, background:
      'repeating-linear-gradient(0deg, transparent, transparent 23px, #eef 23px, #eef 24px),' +
      'repeating-linear-gradient(90deg, transparent, transparent 23px, #eef 23px, #eef 24px)',
    overflow: 'hidden', touchAction: 'none',
  },
  node: {
    position: 'absolute', minWidth: 120, border: '1px solid #555', borderRadius: 6,
    background: '#fff', boxShadow: '0 1px 4px rgba(0,0,0,.2)', userSelect: 'none',
  },
  nodeHead: { padding: '2px 8px', color: '#fff', fontSize: 12, borderRadius: '5px 5px 0 0', cursor: 'move' },
  nodeBody: { padding: '4px 8px', fontSize: 12 },
  port: {
    position: 'absolute', width: 12, height: 12, borderRadius: '50%',
    background: '#ddd', border: '1px solid #555', top: 14, cursor: 'crosshair',
  },
  side: { flex: '1 1 280px', minWidth: 260, maxWidth: 480 },
  pre: {
    background: '#101826', color: '#d6e2f0', padding: 8, borderRadius: 6,
    fontSize: 12, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap',
  },
  logItem: { borderBottom: '1px dashed #ccc', padding: '4px 0', fontSize: 12 },
};

/**
 * 本画布的调色板 / 拖放白名单：派生自 `DSL_NODE_TYPES` 真源（18 类），
 * 基础三类型（input/transform/output）排在最前。
 *
 * 依据：后端 `services/dsl_canvas.py` 的 `NODE_TYPES` 与本前端真源同为一个
 * 封闭集，`compile_dsl` 对全部 18 类都能编译；需要外部 resolver 的节点
 * （llm / knowledge_retrieval / question_classifier / parameter_extractor /
 * http_request / tool / human_input）在**执行**时才会因未接通而如实失败，
 * 但**创建与序列化是支持的**，因此不在此处 fail-closed 剔除——否则本画布
 * 与 FlowEditor 的 NodePalette（早已暴露 18 类）能力不一致。执行期的
 * 「未接通即失败」由后端负责，前端不代为隐藏。
 */
const CANVAS_PALETTE_TYPES: readonly DslNodeType[] = [
  'input', 'transform', 'output',
  ...DSL_NODE_TYPES.filter((t) => t !== 'input' && t !== 'transform' && t !== 'output'),
];

export interface DslCanvasProps {
  /**
   * 外部（页面级单一状态源）载入的文档。仅在**对象引用变化**时载入，
   * 这样本组件内部的拖拽（只改 layout）不会被回滚。
   */
  initialDoc?: DslDocument | null;
  /**
   * 本组件本地编辑后把文档回传给页面（与 FlowEditor 共享同一条状态通道）。
   * 由 `buildDsl` 的变化触发——它是 nodes/edges 的唯一派生，语义等价于
   * 「模型变过」，且不含 layout 变化。
   *
   * 为什么用 useEffect 而不是直接在渲染里调：渲染期调 setState 会在
   * React 里触发级联更新警告。这里的链条是
   * `buildDsl 变 → 回传 → 页面 setDoc → initialDoc 引用变 → 本组件载入
   * → buildDsl 变`，环靠页面侧的单一状态源 + 子组件「仅引用变化才载入」
   * 收敛（载入后产出的文档与传入等价，不会无限翻转）。
   */
  onDocChange?: (doc: DslDocument) => void;
  /**
   * 宿主是否可见（两个画布同时挂载、用 `hidden` 切页签）。
   *
   * 不可见时**只跳过防抖 IR 校验请求**（否则首屏两份文档各发一次，
   * 请求量翻倍），编辑与回传一律照旧 —— 切回可见时会立刻补一次校验。
   */
  visible?: boolean;
  /**
   * G4-i · R10：三重模式。默认值 `'technical'`（R4：既有 4 个 DslCanvas 测试
   * 文件都不传 mode，靠这个默认值断言「代码视图直接可见」，不得改动）。
   *
   * 只有 `beginner` 会改变可见性：把「怎么读图」那份同源 JSON 收进折叠区。
   * 节点面板 / 画布 / 运行**不受模式影响**——小白照样能搭、能跑。
   */
  mode?: DslMode;
}

/** N3 · G2 实时推送：单个节点的实时进度项（开始执行 = running，结束 = 终态）。 */
interface DslRunProgressItem {
  node_id: string;
  node_type: string;
  verb: string | null;
  status: 'running' | DslRunLog['status'];
}

export function DslCanvas({ initialDoc, onDocChange, visible = true, mode = 'technical' }: DslCanvasProps = {}) {
  useBase({ surface: 'web/src/components/dsl/DslCanvas' });
  // 模型状态（语义）与布局状态（视图）严格分离。
  const [nodes, setNodes] = useState<DslNode[]>([]);
  const [edges, setEdges] = useState<DslEdge[]>([]);
  const [layout, setLayout] = useState<LayoutState>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [run, setRun] = useState<DslRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** P1 · 后端收集式 IR 校验的全部诊断（画布红点 + 诊断面板共用）。 */
  const [irDiagnostics, setIrDiagnostics] = useState<DslDiagnostic[]>([]);
  /** P1 · IR 校验请求本身的失败（与运行错误分开，避免互相覆盖、也绝不静默吞掉）。 */
  const [irError, setIrError] = useState<string | null>(null);
  /** G4-f：一句话生成面板默认收起，点「生成 DSL」才展开（展开不碰画布与代码视图）。 */
  const [showGenerate, setShowGenerate] = useState(false);
  const generatePromptRef = useRef<HTMLTextAreaElement | null>(null);

  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  /** N3 · G2 实时推送：当前活动的 SSE 连接（组件卸载 / 重新执行时 close）。 */
  const streamRef = useRef<DslRunStream | null>(null);
  /** N3 · 实时进度：本轮各节点的开始 / 结束快照（按 node_id 去重，保持出现序）。 */
  const [progress, setProgress] = useState<DslRunProgressItem[]>([]);

  // 卸载时掐断未结束的 SSE，避免悬挂连接与对已卸载组件的 setState。
  useEffect(() => () => {
    streamRef.current?.close();
    streamRef.current = null;
  }, []);

  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId]);

  /**
   * 生成 DSL：只序列化语义模型，layout 坐标不参与。
   *
   * 复用 `FlowEditor.serializeGraph`——本组件过去自抄一份序列化，导致两条
   * 路径对同一份图产出不同结构（transform 的 verb 缺省会丢键、空 params
   * 会输出 `"params":{}`），与后端 `graph_to_dsl` 的省略规则也对不上。
   */
  const buildDsl = useCallback((): DslDocument => serializeGraph(nodes, edges), [nodes, edges]);

  /**
   * 代码视图的 JSON：**从同一份 nodes/edges 派生**，不另存一份 state。
   * 过去要点「生成 DSL」按钮才更新，画布改完代码视图是陈旧的——真正的
   * 双源（同源要求 N4 的核心）。
   */
  const dslText = useMemo(() => JSON.stringify(buildDsl(), null, 2), [buildDsl]);

  /**
   * 载入外部文档（与 FlowEditor 同一套 `graphFromDsl` 解析，不自抄一份）。
   * layout 第二参传 `{}`：DSL 里没有坐标，让它按 index 走网格兜底，
   * 不臆造来自别处的坐标。
   */
  const loadDoc = useCallback((next: DslDocument) => {
    const parsed = graphFromDsl(next, {});
    setNodes(parsed.nodes);
    setEdges(parsed.edges);
    // 网格坐标只进视图状态（layout），语义与视图分离的约定不变。
    setLayout(
      Object.fromEntries(parsed.nodes.map((n) => [n.id, { x: n.x, y: n.y }])),
    );
    setSelectedId(null);
    setError(null);
  }, []);

  // 外部载入：仅在文档**对象引用**变化时载入，避免本组件的拖拽被回滚。
  useEffect(() => {
    if (initialDoc) loadDoc(initialDoc);
  }, [initialDoc, loadDoc]);

  // 本地模型变化 → 回传给页面（共享状态通道）。
  //
  // 防循环：`onDocChange(buildDsl())` 走页面 → `setDoc` → `initialDoc` 引用变 →
  // 本组件 `loadDoc` 重建 nodes/edges → buildDsl 再变，看起来会绕圈。收敛靠页面侧
  // 那道「内容等价就不换引用」的闸：载入后产出的文档与传进来的内容逐字段相同，
  // 页面因此不再 setDoc，环就在第二圈断开。拖拽只改 layout，buildDsl 不变，
  // 既不回传也不会被回滚。
  useEffect(() => {
    if (onDocChange) onDocChange(buildDsl());
  }, [buildDsl, onDocChange]);

  // P1 · 模型一变就防抖调收集式 IR 校验。校验请求失败不阻塞编辑
  // （权威拦截仍在执行路径：后端 compile_dsl 内 assert_ir_valid），
  // 但**绝不静默吞掉**：请求失败会在诊断面板旁如实显示，否则「面板没红点」
  // 会被误当成「没问题」。
  useEffect(() => {
    // 不可见的那一半不校验：两个画布同时挂载且共用同一份文档，不加这道闸
    // 首屏就会对同一份图发两次 `validateIr`。切回可见时依赖变化会补一次。
    if (!visible) return undefined;
    const t = window.setTimeout(() => {
      dslCanvasApi.validateIr(buildDsl())
        .then((r) => { setIrDiagnostics(r.diagnostics); setIrError(null); })
        .catch((e: unknown) => {
          setIrError(e instanceof Error ? e.message : String(e));
        });
    }, IR_CHECK_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [buildDsl, visible]);

  const addNode = useCallback((type: DslNodeType, at?: { x: number; y: number }) => {
    const id = nextId(type);
    const pos = at ?? {
      x: 40 + (nodes.length % 4) * 150,
      y: 30 + Math.floor(nodes.length / 4) * 110,
    };
    setNodes((ns) => [...ns, {
      id, type,
      ...(type === 'transform' ? { verb: 'template' as const } : {}),
      params: defaultParams(type),
    }]);
    setLayout((l) => ({ ...l, [id]: pos }));
    setSelectedId(id);
  }, [nodes.length]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData('text/fy-dsl-node') as DslNodeType;
    if (!type || !CANVAS_PALETTE_TYPES.includes(type)) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    const x = rect ? e.clientX - rect.left - 60 : 40;
    const y = rect ? e.clientY - rect.top - 16 : 40;
    addNode(type, {
      x: Math.max(0, Math.min(CANVAS_W - 130, x)),
      y: Math.max(0, Math.min(CANVAS_H - 60, y)),
    });
  }, [addNode]);

  const onNodePointerDown = (e: React.PointerEvent, id: string) => {
    const pos = layout[id];
    if (!pos) return;
    dragRef.current = { id, dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    setSelectedId(id);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setLayout((l) => ({
      ...l,
      [d.id]: {
        x: Math.max(0, Math.min(CANVAS_W - 130, e.clientX - d.dx)),
        y: Math.max(0, Math.min(CANVAS_H - 60, e.clientY - d.dy)),
      },
    }));
  };
  const onPointerUp = () => { dragRef.current = null; };

  const handlePortClick = (id: string, port: 'in' | 'out') => {
    if (port === 'out') {
      setConnectFrom((cur) => (cur === id ? null : id));
      return;
    }
    if (!connectFrom || connectFrom === id) { setConnectFrom(null); return; }
    setEdges((es) => es.some((e) => e.from === connectFrom && e.to === id)
      ? es : [...es, { from: connectFrom, to: id }]);
    setConnectFrom(null);
  };

  const removeNode = (id: string) => {
    setNodes((ns) => ns.filter((n) => n.id !== id));
    setEdges((es) => es.filter((e) => e.from !== id && e.to !== id));
    setLayout(({ [id]: _drop, ...rest }) => rest);
    setSelectedId((cur) => (cur === id ? null : cur));
  };

  const updateSelected = (patch: Partial<DslNode>) => {
    if (!selected) return;
    setNodes((ns) => ns.map((n) => (n.id === selected.id ? { ...n, ...patch } : n)));
  };

  const updateParams = (patch: Record<string, unknown>) => {
    if (!selected) return;
    updateSelected({ params: { ...selected.params, ...patch } });
  };

  /** G4-f：展开一句话生成面板并把焦点交给需求输入框；顺带清掉上一次的运行错误提示。 */
  const generate = () => {
    setError(null);
    setShowGenerate(true);
  };

  // 面板是条件挂载的，等它真正渲染出来再把焦点送过去，用户可以直接打字。
  useEffect(() => {
    if (showGenerate) generatePromptRef.current?.focus();
  }, [showGenerate]);

  const execute = async () => {
    // 重新执行：先掐断上一轮 SSE（若有），避免旧事件串入新进度。
    streamRef.current?.close();
    streamRef.current = null;
    setBusy(true); setError(null); setRun(null); setProgress([]);
    const dsl = buildDsl();

    /** 降级路径：后端未启用流式 / 网络错误时，退回原同步执行，功能不退化。 */
    const runSync = async () => {
      try {
        setRun(await dslCanvasApi.run(dsl));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };

    let start: DslRunStreamStart | DslRunResult;
    try {
      start = await dslCanvasApi.runStream(dsl);
    } catch {
      await runSync();
      setBusy(false);
      return;
    }
    // 旧后端会忽略 stream:true 并同步返回完整结果（非 'running'）→ 直接使用。
    if (start.status !== 'running') {
      setRun(start);
      setBusy(false);
      return;
    }

    const runId = start.run_id;
    const outcome = await new Promise<'finished' | 'error'>((resolve) => {
      const stream = openDslRunStream(runId, {
        onNodeStarted: (e) => setProgress((p) => [
          ...p.filter((x) => x.node_id !== e.node_id),
          { node_id: e.node_id, node_type: e.node_type, verb: e.verb, status: 'running' },
        ]),
        onNodeFinished: (e) => setProgress((p) => [
          ...p.filter((x) => x.node_id !== e.node_id),
          { node_id: e.node_id, node_type: e.node_type, verb: e.verb, status: e.status },
        ]),
        onRunFinished: () => { stream.close(); resolve('finished'); },
        onError: () => { stream.close(); resolve('error'); },
      });
      streamRef.current = stream;
    });
    streamRef.current = null;

    if (outcome === 'error') {
      // SSE 在 run_finished 前断流：退回同步执行（否则拿不到含 logs 的完整结果）。
      await runSync();
      setBusy(false);
      return;
    }
    // 权威结果（含逐步日志）以 getRun 为准：run_finished 载荷本身不含 logs。
    try {
      setRun(await dslCanvasApi.getRun(runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  const pos = (id: string) => layout[id] ?? { x: 0, y: 0 };

  return (
    <div style={styles.root} data-testid="dsl-canvas-root">
      <div style={styles.palette} data-testid="dsl-palette">
        <strong>节点面板（拖入画布或点击添加）</strong>
        {CANVAS_PALETTE_TYPES.map((t) => (
          <div
            key={t}
            style={{ ...styles.paletteItem, borderLeft: `6px solid ${NODE_COLORS[t]}` }}
            draggable
            onDragStart={(e) => e.dataTransfer.setData('text/fy-dsl-node', t)}
            onClick={() => addNode(t)}
            data-testid={`palette-${t}`}
          >
            {NODE_LABELS[t]}
          </div>
        ))}
        <div style={{ fontSize: 12, color: '#666' }}>
          连线：点击节点右侧「出」圆点，再点击目标节点左侧「入」圆点。
        </div>
      </div>

      <div
        ref={canvasRef}
        style={styles.canvas}
        data-testid="dsl-canvas-area"
        onDrop={onDrop}
        onDragOver={(e) => e.preventDefault()}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <svg width={CANVAS_W} height={CANVAS_H} style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          {edges.map((e, i) => {
            const a = pos(e.from); const b = pos(e.to);
            const x1 = a.x + 130; const y1 = a.y + 20; const x2 = b.x; const y2 = b.y + 20;
            const mx = (x1 + x2) / 2;
            return (
              <path
                key={i}
                d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                stroke="#3a5f8a" strokeWidth={2} fill="none"
                markerEnd="url(#dsl-arrow)"
              />
            );
          })}
          <defs>
            <marker id="dsl-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L7,3 L0,6 z" fill="#3a5f8a" />
            </marker>
          </defs>
        </svg>

        {nodes.map((n) => {
          const p = pos(n.id);
          const nodeDiags = diagnosticsForNode(irDiagnostics, n.id);
          return (
            <div
              key={n.id}
              style={{ ...styles.node, left: p.x, top: p.y,
                outline: selectedId === n.id
                  ? '2px solid #3a5f8a'
                  : nodeDiags.length > 0 ? '2px solid #c0392b' : undefined }}
              data-testid={`dsl-node-${n.id}`}
            >
              <div
                style={{ ...styles.nodeHead, background: NODE_COLORS[n.type] }}
                onPointerDown={(e) => onNodePointerDown(e, n.id)}
              >
                {n.type}{n.verb ? ` · ${n.verb}` : ''}
                {nodeDiags.length > 0 && (
                  <span
                    className="fy-ir-dot"
                    data-testid={`dsl-node-diag-${n.id}`}
                    title={nodeDiagnosticTitle(nodeDiags)}
                    aria-label={`节点 ${n.id} 有 ${nodeDiags.length} 条类型诊断`}
                  >● {nodeDiags.length}</span>
                )}
              </div>
              <div style={styles.nodeBody}>
                {n.id}
                <button
                  className="small danger"
                  style={{ marginLeft: 8, fontSize: 10 }}
                  onClick={() => removeNode(n.id)}
                  data-testid={`delete-${n.id}`}
                >删</button>
              </div>
              <div
                title="入"
                onClick={() => handlePortClick(n.id, 'in')}
                style={{ ...styles.port, left: -6 }}
                data-testid={`port-in-${n.id}`}
              />
              <div
                title="出"
                onClick={() => handlePortClick(n.id, 'out')}
                style={{ ...styles.port, right: -6,
                  background: connectFrom === n.id ? '#ffd54f' : '#ddd' }}
                data-testid={`port-out-${n.id}`}
              />
            </div>
          );
        })}
        {nodes.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#889' }}>
            从左侧拖入或点击添加节点
          </div>
        )}
      </div>

      <div style={styles.side}>
        <div className="card">
          <strong>属性{selected ? ` · ${selected.id}` : '（未选中节点）'}</strong>
          {!selected && <div className="muted">点击画布中的节点以编辑属性。</div>}
          {selected && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
              {selected.type === 'input' && (
                <>
                  <label>数据类型
                    <select
                      value={String(selected.params?.kind ?? 'literal')}
                      onChange={(e) => updateParams({ kind: e.target.value })}
                      data-testid="prop-input-kind"
                    >
                      <option value="literal">literal（JSON 值）</option>
                      <option value="text_lines">text_lines（按行拆分文本）</option>
                    </select>
                  </label>
                  {selected.params?.kind === 'text_lines' ? (
                    <label>文本
                      <textarea
                        rows={3}
                        value={String(selected.params?.value ?? '')}
                        onChange={(e) => updateParams({ value: e.target.value })}
                      />
                    </label>
                  ) : (
                    <label>JSON 值
                      <textarea
                        rows={3}
                        value={JSON.stringify(selected.params?.value ?? null)}
                        onChange={(e) => {
                          try { updateParams({ value: JSON.parse(e.target.value || 'null') }); }
                          catch { /* 编辑中允许暂不合法 */ }
                        }}
                        data-testid="prop-input-value"
                      />
                    </label>
                  )}
                </>
              )}
              {selected.type === 'transform' && (
                <>
                  <label>动词
                    <select
                      value={selected.verb ?? 'template'}
                      onChange={(e) => {
                        // 动词集单一真源：api/dslCanvas.ts 的 DSL_TRANSFORM_VERBS
                        //（与后端 VERB_REGISTRY 对齐，含 approval）。
                        // 切换时整体替换 params，绝不残留上一个动词的字段；
                        // paramsForVerb 属包5 的 FlowEditor，approval 缺省分支
                        // 由 ?? {} 兜底（不假设它已补齐）。
                        const verb = e.target.value as DslTransformVerb;
                        updateSelected({ verb, params: paramsForVerb(verb) ?? {} });
                      }}
                      data-testid="prop-verb"
                    >
                      {DSL_TRANSFORM_VERBS.map((verb) => (
                        <option key={verb} value={verb}>
                          {verb}（{VERB_LABELS[verb]}）
                        </option>
                      ))}
                    </select>
                  </label>
                  {selected.verb === 'template' && (
                    <label>模板
                      <input
                        value={String(selected.params?.template ?? '')}
                        onChange={(e) => updateParams({ template: e.target.value })}
                        data-testid="prop-template"
                      />
                    </label>
                  )}
                  {selected.verb === 'filter' && (
                    <>
                      <label>字段
                        <input
                          value={String(selected.params?.field ?? '')}
                          onChange={(e) => updateParams({ field: e.target.value })}
                        />
                      </label>
                      <label>比较
                        <select
                          value={String(selected.params?.op ?? 'gt')}
                          onChange={(e) => updateParams({ op: e.target.value })}
                        >
                          {['eq', 'ne', 'gt', 'lt', 'contains'].map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      </label>
                      <label>阈值（JSON）
                        <input
                          value={JSON.stringify(selected.params?.value ?? null)}
                          onChange={(e) => {
                            try { updateParams({ value: JSON.parse(e.target.value || 'null') }); }
                            catch { /* 编辑中 */ }
                          }}
                        />
                      </label>
                    </>
                  )}
                  {selected.verb === 'map' && (
                    <>
                      <label>操作
                        <select
                          value={String(selected.params?.op ?? 'set')}
                          onChange={(e) => updateParams({ op: e.target.value })}
                        >
                          {['set', 'upper', 'lower'].map((o) => (
                            <option key={o} value={o}>{o}</option>
                          ))}
                        </select>
                      </label>
                      {selected.params?.op === 'set' && (
                        <>
                          <label>目标字段
                            <input
                              value={String(selected.params?.field ?? '')}
                              onChange={(e) => updateParams({ field: e.target.value })}
                            />
                          </label>
                          <label>新值（支持 {'{field}'} 插值）
                            <input
                              value={String(selected.params?.value ?? '')}
                              onChange={(e) => updateParams({ value: e.target.value })}
                            />
                          </label>
                        </>
                      )}
                    </>
                  )}
                </>
              )}
              {selected.type === 'output' && (
                <label>输出格式
                  <select
                    value={String(selected.params?.format ?? 'text')}
                    onChange={(e) => updateParams({ format: e.target.value })}
                    data-testid="prop-output-format"
                  >
                    <option value="text">text（文本行）</option>
                    <option value="json">json（原样）</option>
                  </select>
                </label>
              )}
            </div>
          )}
        </div>

        <div className="card">
          <div style={{ display: 'flex', gap: 8 }}>
            {/* G4-f：不再要求先手搭节点——一句话生成本来就是替用户搭节点。
                「生成 DSL」展开下面那块面板；「执行」才需要画布上有东西可跑。 */}
            <button className="primary" onClick={generate}
              data-testid="dsl-generate">生成 DSL</button>
            <button onClick={() => void execute()} disabled={busy || !nodes.length}
              data-testid="dsl-run">{busy ? '执行中…' : '执行'}</button>
          </div>
          {showGenerate && (
            <div style={{ marginTop: 8 }}>
              <GeneratePanel
                testIdPrefix="canvas"
                promptRef={generatePromptRef}
                onGenerated={(next) => { setShowGenerate(false); loadDoc(next); }}
              />
            </div>
          )}
          {/* P1 · 后端 IR 的全部类型诊断：一次全量显示（含每条的 code/message）。 */}
          <div style={{ marginTop: 8 }}>
            <DslDiagnostics diagnostics={irDiagnostics} />
          </div>
          {irError && (
            <div className="error-text" role="alert" data-testid="dsl-ir-error">
              IR 校验请求失败：{irError}
            </div>
          )}
          {error && <div className="error-text" role="alert">{error}</div>}
          {/* N3 · 实时进度：流式执行期间各节点陆续点亮的进度列表。 */}
          {progress.length > 0 && (
            <div style={{ marginTop: 8 }} data-testid="dsl-run-progress">
              <div className="muted">实时进度</div>
              {progress.map((p) => (
                <div key={p.node_id} style={styles.logItem}>
                  <code>{p.node_id}</code>（{p.node_type}{p.verb ? `/${p.verb}` : ''}）{' '}
                  <span className="badge accent">{p.status}</span>
                </div>
              ))}
            </div>
          )}
          {/* 同源实时：JSON 由上面那份 nodes/edges 直接派生，无需点任何按钮。
              G4-i：小白模式收进折叠区（折叠而非卸载——收起态仍在 DOM）。 */}
          {mode === 'beginner' ? (
            <details style={{ marginTop: 6 }} data-testid="dsl-json-details">
              <summary style={{ cursor: 'pointer' }} data-testid="dsl-json-summary">
                DSL JSON（同源实时，布局坐标不进入 DSL）
              </summary>
              <pre style={styles.pre} data-testid="dsl-json">{dslText}</pre>
            </details>
          ) : (
            <>
              <div className="muted" style={{ marginTop: 6 }}>DSL JSON（同源实时，布局坐标不进入 DSL）</div>
              <pre style={styles.pre} data-testid="dsl-json">{dslText}</pre>
            </>
          )}
          {run && (
            <div style={{ marginTop: 8 }} data-testid="dsl-run-result">
              <div>
                运行 <code>{run.run_id}</code> ·
                状态 <span className="badge accent">{run.status}</span>
              </div>
              {run.error && <div className="notice danger">{run.error}</div>}
              <div className="muted">输出</div>
              <pre style={styles.pre} data-testid="dsl-run-output">
                {typeof run.output === 'string' ? run.output : JSON.stringify(run.output, null, 2)}
              </pre>
              <div className="muted">逐步日志</div>
              <div data-testid="dsl-run-logs">
                {run.logs.map((l, i) => (
                  <div key={i} style={styles.logItem}>
                    <code>{l.node_id}</code>（{l.node_type}{l.verb ? `/${l.verb}` : ''}）{' '}
                    <span className="badge accent">{l.status}</span>
                    {l.error && <span style={{ color: '#c0392b' }}> {l.error}</span>}
                    <div style={{ color: '#667' }}>
                      输入: {JSON.stringify(l.input)?.slice(0, 120)}
                    </div>
                    <div style={{ color: '#667' }}>
                      输出: {JSON.stringify(l.output)?.slice(0, 160)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
