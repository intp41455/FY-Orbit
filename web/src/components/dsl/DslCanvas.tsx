/**
 * P1-18 受限 DSL 画布（全栈工单的前端半）。
 *
 * 蓝本（claw-dialogue-extraction §1.2/§1.3）：
 *  - 视图/模型分离：DslDocument（语义模型）与 LayoutState（坐标）是两个
 *    独立的 state；生成 DSL 时只序列化模型，坐标永不进入 DSL。
 *  - 受限动词集：input / transform(9 个受限 verb) / output，节点面板只暴露
 *    这三类；连线表示数据流（from → to）。动词清单以
 *    `api/dslCanvas.ts` 的 `DslTransformVerb` 为准（与后端注册表逐字对齐）。
 *  - 「生成 DSL」实时显示 JSON；「执行」调后端真实执行并回显逐步日志；
 *    「导出代码」把画布导出成受限 Python（`dslCanvasApi.exportCode`）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  dslCanvasApi,
  type DslDiagnostic,
  type DslDocument,
  type DslEdge,
  type DslNode,
  type DslNodeType,
  type DslRunResult,
  type DslTransformVerb,
  type LayoutState,
} from '../../api/dslCanvas';
import { paramsForVerb } from '../workflow/FlowEditor';
import { DslDiagnostics, diagnosticsForNode, nodeDiagnosticTitle } from './DslDiagnostics';

/** 编辑后防抖再调后端 IR 校验的间隔（ms）；测试里可用真实定时器 + waitFor。 */
export const IR_CHECK_DEBOUNCE_MS = 300;

let seq = 0;
function nextId(prefix: string): string {
  seq += 1;
  return `${prefix}${seq}`;
}

const NODE_LABELS: Record<DslNodeType, string> = {
  input: '输入 input',
  transform: '变换 transform',
  output: '输出 output',
};

const NODE_COLORS: Record<DslNodeType, string> = {
  input: '#2f6f4f',
  transform: '#3a5f8a',
  output: '#7a4a8a',
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

function defaultParams(type: DslNodeType): Record<string, unknown> {
  if (type === 'input') return { kind: 'literal', value: [{ name: '张三', age: 34 }] };
  if (type === 'transform') return { template: '你好，{name}！你今年 {age} 岁。' };
  return { format: 'text' };
}

export function DslCanvas() {
  // 模型状态（语义）与布局状态（视图）严格分离。
  const [nodes, setNodes] = useState<DslNode[]>([]);
  const [edges, setEdges] = useState<DslEdge[]>([]);
  const [layout, setLayout] = useState<LayoutState>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  const [dslText, setDslText] = useState('');
  const [run, setRun] = useState<DslRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** P1 · 后端收集式 IR 校验的全部诊断（画布红点 + 诊断面板共用）。 */
  const [irDiagnostics, setIrDiagnostics] = useState<DslDiagnostic[]>([]);

  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null, [nodes, selectedId]);

  /** 生成 DSL：只序列化语义模型，layout 坐标不参与。 */
  const buildDsl = useCallback((): DslDocument => ({
    version: '1',
    nodes: nodes.map((n) => ({
      id: n.id, type: n.type,
      ...(n.type === 'transform' ? { verb: n.verb } : {}),
      ...(n.params ? { params: n.params } : {}),
    })),
    edges: edges.map((e) => ({ from: e.from, to: e.to })),
  }), [nodes, edges]);

  // P1 · 模型一变就防抖调收集式 IR 校验。校验请求失败不阻塞编辑
  // （权威拦截仍在执行路径：后端 compile_dsl 内 assert_ir_valid）。
  useEffect(() => {
    const t = window.setTimeout(() => {
      dslCanvasApi.validateIr(buildDsl())
        .then((r) => setIrDiagnostics(r.diagnostics))
        .catch(() => undefined);
    }, IR_CHECK_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [buildDsl]);

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
    if (!type || !['input', 'transform', 'output'].includes(type)) return;
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

  const generate = () => {
    setError(null);
    setDslText(JSON.stringify(buildDsl(), null, 2));
  };

  const execute = async () => {
    setBusy(true); setError(null); setRun(null);
    try {
      const doc = buildDsl();
      setDslText(JSON.stringify(doc, null, 2));
      setRun(await dslCanvasApi.run(doc));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const pos = (id: string) => layout[id] ?? { x: 0, y: 0 };

  return (
    <div style={styles.root} data-testid="dsl-canvas-root">
      <div style={styles.palette} data-testid="dsl-palette">
        <strong>节点面板（拖入画布或点击添加）</strong>
        {(['input', 'transform', 'output'] as DslNodeType[]).map((t) => (
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
                        // 动词集是后端封闭白名单；下拉项与paramsForVerb 一一对应，
                        // 切换时整体替换 params，绝不残留上一个动词的字段。
                        const verb = e.target.value as DslTransformVerb;
                        updateSelected({ verb, params: paramsForVerb(verb) });
                      }}
                      data-testid="prop-verb"
                    >
                      <option value="template">template（模板插值）</option>
                      <option value="map">map（逐条映射）</option>
                      <option value="filter">filter（条件过滤）</option>
                      <option value="branch">branch（条件分支）</option>
                      <option value="aggregate">aggregate（循环聚合）</option>
                      <option value="merge">merge（并行汇聚）</option>
                      <option value="artifact">artifact（产出产物）</option>
                      <option value="agent">agent（调用 Agent，未接解析器会失败）</option>
                      <option value="confirm">confirm（人工确认，HITL 未接入会失败）</option>
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
            <button className="primary" onClick={generate} disabled={!nodes.length}
              data-testid="dsl-generate">生成 DSL</button>
            <button onClick={() => void execute()} disabled={busy || !nodes.length}
              data-testid="dsl-run">{busy ? '执行中…' : '执行'}</button>
          </div>
          {/* P1 · 后端 IR 的全部类型诊断：一次全量显示（含每条的 code/message）。 */}
          <div style={{ marginTop: 8 }}>
            <DslDiagnostics diagnostics={irDiagnostics} />
          </div>
          {error && <div className="error-text" role="alert">{error}</div>}
          {dslText && (
            <>
              <div className="muted" style={{ marginTop: 6 }}>DSL JSON（布局坐标不进入 DSL）</div>
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
