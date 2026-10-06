import { useEffect, useMemo, useState } from 'react';
import { validateTraceDocument, type TraceValidationResult } from './validateTrace';
import { normalizeTrace, type NormalizedTrace } from './normalizeTrace';
import pmiSamples from './samples/pmi-sample-traces.json';
import canvasSample from './samples/canvas-hermes-roundtrip.json';

/**
 * P1-16 trace 前端视图 — PMI Trace Schema v1.0 时序可视化。
 *
 * 功能：
 * 1. 两种加载方式：文件上传（.json）+ 内置样例（L1/L2/L3 三层各一例、
 *    完整三层样例集、真实 canvas→hermes 往返 trace）。
 * 2. 加载时先做 schema 字段校验（validateTrace.ts，逐字段记录
 *    字段名/类型/缺失项），校验失败在视图中显著告警，日志同步挂到
 *    window.__TRACE_VALIDATION_LOG__ 供 E2E 提取落 evidence/。
 * 3. 时序视图：泳道 = 参与方（actor / agent / L1·L2·L3 存储层 / canvas 侧 agent），
 *    事件节点按 (timestamp, seq) 排列，事件间画调用连线，
 *    节点点击打开详情浮层展示完整字段。
 */

interface LoadedDoc {
  name: string;
  data: unknown;
}

function layerEvent(layer: 'L1' | 'L2' | 'L3'): LoadedDoc {
  const doc = pmiSamples as { trace_events?: unknown[] };
  const ev = (doc.trace_events ?? []).find(
    (e) => typeof e === 'object' && e !== null && (e as Record<string, unknown>).layer === layer,
  );
  return {
    name: `PMI 三层样例 · ${layer}`,
    data: { sample_set_id: `pmi-sample-${layer}`, trace_events: ev ? [ev] : [] },
  };
}

const BUILTIN_SAMPLES: { key: string; label: string; doc: LoadedDoc }[] = [
  { key: 'sample-l1', label: '内置样例：L1 规则层', doc: layerEvent('L1') },
  { key: 'sample-l2', label: '内置样例：L2 记忆层（含写回+冷外传）', doc: layerEvent('L2') },
  { key: 'sample-l3', label: '内置样例：L3 资料层', doc: layerEvent('L3') },
  { key: 'sample-full', label: '内置样例：完整三层样例集', doc: { name: 'PMI 三层样例集 v1', data: pmiSamples } },
  {
    key: 'sample-canvas',
    label: '内置样例：真实 Canvas→Hermes 往返 trace',
    doc: { name: 'CanvasService → Hermes CLI 本地往返', data: canvasSample },
  },
];

const NODE_STEP = 210;
const NODE_W = 168;
const NODE_H = 52;
const LANE_H = 84;
const PAD_LEFT = 150;
const PAD_TOP = 56;

export function TraceView() {
  const [doc, setDoc] = useState<LoadedDoc | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [validation, setValidation] = useState<TraceValidationResult | null>(null);
  const [normalized, setNormalized] = useState<NormalizedTrace | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [showAllIssues, setShowAllIssues] = useState(false);

  // 加载 → 先校验 → 再归一化渲染。校验日志挂 window 供 E2E 提取落 evidence/。
  useEffect(() => {
    if (!doc) return;
    setParseError(null);
    setSelected(null);
    setShowAllIssues(false);
    let result: TraceValidationResult;
    try {
      result = validateTraceDocument(doc.data);
    } catch (err) {
      result = {
        format: 'unknown',
        eventCount: 0,
        issues: [
          {
            path: '$',
            field: '(validator)',
            expected: '校验器可执行',
            actual: `异常: ${String(err)}`,
            status: 'fail',
          },
        ],
        passCount: 0,
        failCount: 1,
        ok: false,
      };
    }
    const model = normalizeTrace(doc.data);
    setValidation(result);
    setNormalized(model);
    (window as unknown as Record<string, unknown>).__TRACE_VALIDATION_LOG__ = {
      source: doc.name,
      format: result.format,
      validatedAt: new Date().toISOString(),
      schema: 'PMI Trace Schema v1.0 (opencode_pmi_trace_schema_v1.0.schema.json)',
      eventCount: result.eventCount,
      summary: { pass: result.passCount, fail: result.failCount, ok: result.ok },
      issues: result.issues,
    };
  }, [doc]);

  const loadFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const text = typeof reader.result === 'string' ? reader.result : '';
        const data = JSON.parse(text) as unknown;
        setParseError(null);
        setDoc({ name: file.name, data });
      } catch (err) {
        setDoc(null);
        setValidation(null);
        setNormalized(null);
        setParseError(`JSON 解析失败: ${err instanceof Error ? err.message : String(err)}`);
      }
    };
    reader.onerror = () => setParseError(`文件读取失败: ${file.name}`);
    reader.readAsText(file);
  };

  const laneY = useMemo(() => {
    const map = new Map<string, number>();
    (normalized?.lanes ?? []).forEach((lane, i) => map.set(lane, PAD_TOP + i * LANE_H));
    return map;
  }, [normalized]);

  const svgWidth = Math.max(760, PAD_LEFT + (normalized?.nodes.length ?? 0) * NODE_STEP + 80);
  const svgHeight = PAD_TOP + Math.max(1, normalized?.lanes.length ?? 1) * LANE_H + 24;
  const selectedNode = selected !== null ? normalized?.nodes[selected] : null;

  return (
    <div className="fy-trace" data-testid="trace-view">
      <style>{traceStyles}</style>
      <header className="fy-trace-header">
        <h2>PMI Trace 时序视图</h2>
        <p className="fy-trace-sub">
          P1-16 · schema: PMI Trace Schema v1.0（JSON Schema draft 2020-12）· 加载即校验，泳道=参与方，节点按 timestamp/seq 排列
        </p>
      </header>

      <section className="fy-trace-toolbar" aria-label="trace 加载">
        <label className="fy-trace-upload">
          上传 trace JSON
          <input
            type="file"
            accept=".json,application/json"
            data-testid="trace-file-input"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) loadFile(f);
              e.target.value = '';
            }}
          />
        </label>
        {BUILTIN_SAMPLES.map((s) => (
          <button key={s.key} type="button" data-testid={s.key} onClick={() => setDoc(s.doc)}>
            {s.label}
          </button>
        ))}
      </section>

      {parseError && (
        <div className="fy-trace-error" role="alert" data-testid="trace-parse-error">
          {parseError}
        </div>
      )}

      {validation && normalized && (
        <>
          <section
            className={`fy-trace-validation ${validation.ok ? 'ok' : 'bad'}`}
            data-testid="trace-validation-summary"
          >
            <strong>{validation.ok ? '✓ schema 校验通过' : '✗ schema 校验失败'}</strong>
            <span>
              {' '}· 来源: {doc?.name} · 格式: {validation.format} · 事件数: {validation.eventCount} · 字段检查:{' '}
              {validation.passCount + validation.failCount}（通过 {validation.passCount} / 失败 {validation.failCount}）
            </span>
            {validation.issues.length > 0 && (
              <ul className="fy-trace-issues" data-testid="trace-validation-issues">
                {(showAllIssues ? validation.issues : validation.issues.slice(0, 40)).map((iss, i) => (
                  <li key={i} className={iss.status}>
                    <code>
                      {iss.path}.{iss.field}
                    </code>{' '}
                    期望 {iss.expected} · 实际 {iss.actual} · {iss.status}
                  </li>
                ))}
              </ul>
            )}
            {validation.issues.length > 40 && (
              <button type="button" onClick={() => setShowAllIssues((v) => !v)}>
                {showAllIssues ? '收起校验明细' : `展开全部 ${validation.issues.length} 条校验明细`}
              </button>
            )}
          </section>

          <section className="fy-trace-canvas-wrap">
            <svg
              className="fy-trace-svg"
              width={svgWidth}
              height={svgHeight}
              viewBox={`0 0 ${svgWidth} ${svgHeight}`}
              role="img"
              aria-label={`trace 时序视图 ${normalized.title}`}
            >
              <text x={8} y={22} className="fy-trace-title">
                {normalized.title}
                {normalized.traceId ? ` · ${normalized.traceId}` : ''}
              </text>
              {/* 泳道 */}
              {normalized.lanes.map((lane) => {
                const y = laneY.get(lane) ?? PAD_TOP;
                return (
                  <g key={lane}>
                    <text x={8} y={y + 5} className="fy-trace-lane-label" data-testid="trace-lane">
                      {lane}
                    </text>
                    <line x1={PAD_LEFT - 12} y1={y} x2={svgWidth - 24} y2={y} className="fy-trace-lane-line" />
                  </g>
                );
              })}
              {/* 事件间调用连线（同 trace 相邻事件） */}
              {normalized.nodes.slice(0, -1).map((n, i) => {
                const next = normalized.nodes[i + 1];
                const x1 = PAD_LEFT + i * NODE_STEP + NODE_W / 2;
                const x2 = PAD_LEFT + (i + 1) * NODE_STEP + NODE_W / 2;
                const y1 = laneY.get(n.lane) ?? PAD_TOP;
                const y2 = laneY.get(next.lane) ?? PAD_TOP;
                return (
                  <line
                    key={`link-${i}`}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    className="fy-trace-link"
                    markerEnd="url(#fy-trace-arrow)"
                  />
                );
              })}
              <defs>
                <marker id="fy-trace-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                  <path d="M0,0 L8,4 L0,8 z" fill="#94a3b8" />
                </marker>
              </defs>
              {/* 事件节点 + 关联参与方刻度 */}
              {normalized.nodes.map((n, i) => {
                const x = PAD_LEFT + i * NODE_STEP + NODE_W / 2;
                const y = laneY.get(n.lane) ?? PAD_TOP;
                return (
                  <g key={n.id}>
                    {n.related.map((t, ti) => {
                      const ty = laneY.get(t.lane);
                      if (ty === undefined || ty === y) return null;
                      return (
                        <g key={ti}>
                          <line x1={x} y1={y} x2={x} y2={ty} className="fy-trace-related" />
                          <circle cx={x} cy={ty} r={4} className={`fy-trace-tick role-${t.role}`} />
                        </g>
                      );
                    })}
                    <g
                      className="fy-trace-node"
                      data-testid={`trace-node-${i + 1}`}
                      onClick={() => setSelected(i)}
                    >
                      <rect x={x - NODE_W / 2} y={y - NODE_H / 2} width={NODE_W} height={NODE_H} rx={8} fill={n.color} />
                      <text x={x} y={y - 4} textAnchor="middle" className="fy-trace-node-label">
                        {n.label}
                      </text>
                      <text x={x} y={y + 14} textAnchor="middle" className="fy-trace-node-sub">
                        {n.subtitle.length > 26 ? `${n.subtitle.slice(0, 25)}…` : n.subtitle}
                      </text>
                    </g>
                    <text x={x} y={y + NODE_H / 2 + 14} textAnchor="middle" className="fy-trace-node-ts">
                      {n.ts.replace('T', ' ').slice(0, 19)}
                    </text>
                  </g>
                );
              })}
            </svg>
          </section>
        </>
      )}

      {!doc && !parseError && (
        <div className="fy-trace-empty" data-testid="trace-empty">
          请上传 trace JSON 或点击上方内置样例加载。加载时会先按 PMI Trace Schema v1.0 做字段校验，再渲染时序视图。
        </div>
      )}

      {selectedNode && (
        <div className="fy-trace-overlay" data-testid="trace-detail" role="dialog" aria-label="事件详情">
          <div className="fy-trace-detail">
            <header>
              <h3>{selectedNode.label}</h3>
              <button type="button" data-testid="trace-detail-close" onClick={() => setSelected(null)}>
                关闭
              </button>
            </header>
            <p className="fy-trace-detail-meta">
              {selectedNode.ts} · 泳道 {selectedNode.lane}
              {selectedNode.seq !== null ? ` · seq ${selectedNode.seq}` : ''}
            </p>
            <pre>{JSON.stringify(selectedNode.raw, null, 2)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

const traceStyles = `
.fy-trace { font-family: system-ui, sans-serif; padding: 16px; color: #0f172a; }
.fy-trace-header h2 { margin: 0 0 4px; font-size: 20px; }
.fy-trace-sub { margin: 0 0 12px; color: #64748b; font-size: 13px; }
.fy-trace-toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }
.fy-trace-toolbar button { padding: 6px 12px; border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
.fy-trace-toolbar button:hover { background: #f1f5f9; }
.fy-trace-upload { padding: 6px 12px; border: 1px dashed #94a3b8; border-radius: 6px; font-size: 13px; cursor: pointer; background: #f8fafc; }
.fy-trace-upload input { display: block; margin-top: 4px; font-size: 12px; }
.fy-trace-error { padding: 10px 12px; background: #fef2f2; border: 1px solid #fca5a5; color: #b91c1c; border-radius: 6px; margin-bottom: 12px; }
.fy-trace-validation { padding: 10px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 13px; }
.fy-trace-validation.ok { background: var(--ui-st-complete-bg); border: 1px solid var(--ui-sky-300); color: var(--ui-st-complete); }
.fy-trace-validation.bad { background: #fef2f2; border: 1px solid #fca5a5; color: #b91c1c; }
.fy-trace-issues { margin: 8px 0 0; padding-left: 18px; max-height: 220px; overflow: auto; }
.fy-trace-issues li.pass { color: var(--ui-st-complete); }
.fy-trace-issues li.fail { color: #b91c1c; font-weight: 600; }
.fy-trace-canvas-wrap { overflow-x: auto; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; }
.fy-trace-svg { display: block; }
.fy-trace-title { font-size: 14px; font-weight: 600; fill: #0f172a; }
.fy-trace-lane-label { font-size: 12px; font-weight: 600; fill: #334155; }
.fy-trace-lane-line { stroke: #e2e8f0; stroke-width: 1; }
.fy-trace-link { stroke: #94a3b8; stroke-width: 1.5; stroke-dasharray: 4 3; }
.fy-trace-related { stroke: #cbd5e1; stroke-width: 1; stroke-dasharray: 2 3; }
.fy-trace-tick.role-actor { fill: var(--ui-st-running); }
.fy-trace-tick.role-target { fill: #f59e0b; }
.fy-trace-node { cursor: pointer; }
.fy-trace-node rect { stroke: rgba(15, 23, 42, 0.25); }
.fy-trace-node:hover rect { stroke: #0f172a; stroke-width: 2; }
.fy-trace-node-label { fill: #fff; font-size: 12px; font-weight: 600; }
.fy-trace-node-sub { fill: rgba(255, 255, 255, 0.85); font-size: 10px; }
.fy-trace-node-ts { fill: #64748b; font-size: 10px; }
.fy-trace-empty { padding: 24px; border: 1px dashed #cbd5e1; border-radius: 8px; color: #64748b; font-size: 14px; text-align: center; }
.fy-trace-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.fy-trace-detail { background: #fff; border-radius: 10px; width: min(680px, 92vw); max-height: 80vh; display: flex; flex-direction: column; padding: 16px; }
.fy-trace-detail header { display: flex; justify-content: space-between; align-items: center; }
.fy-trace-detail h3 { margin: 0; font-size: 15px; }
.fy-trace-detail button { padding: 4px 10px; cursor: pointer; }
.fy-trace-detail-meta { color: #64748b; font-size: 12px; margin: 6px 0; }
.fy-trace-detail pre { overflow: auto; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; font-size: 12px; margin: 0; }
`;
