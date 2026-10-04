/**
 * P1-16 — 把不同来源的 trace 文档归一化为时序视图渲染模型。
 *
 * 支持两种格式：
 * 1. PMI Trace Schema v1.0（样例集 trace_events / 单事件 / 事件数组）
 * 2. evidence/process-traces 的 canvas 往返 trace（timeline_events）
 *
 * 泳道（lane）= 参与方：
 * - PMI 事件：发起方 actor_id、执行 agent_id、目标存储层（L1 规则层 / L2 记忆层 / L3 资料层）
 * - canvas 事件：event.agent_id 为空时归入 canvas 泳道，否则归入对应 agent 泳道
 *
 * 排序：按 (timestamp, seq) 时间轴排列——ts 相同（如样例集同刻）时以 seq 定序。
 */

export interface TimelineTick {
  lane: string;
  role: 'actor' | 'target';
  label: string;
}

export interface TimelineNode {
  id: string;
  lane: string;
  ts: string;
  seq: number | null;
  label: string;
  subtitle: string;
  color: string;
  raw: unknown;
  /** 该事件同时涉及的其它参与方（在同 x 位置画联动刻度）。 */
  related: TimelineTick[];
}

export interface NormalizedTrace {
  format: 'pmi-trace' | 'canvas-timeline';
  title: string;
  traceId: string | null;
  lanes: string[];
  nodes: TimelineNode[];
}

const LAYER_COLORS: Record<string, string> = {
  L1: '#2563eb',
  L2: '#7c3aed',
  L3: '#0891b2',
};

const LAYER_LANE_NAMES: Record<string, string> = {
  L1: 'L1 规则层',
  L2: 'L2 记忆层',
  L3: 'L3 资料层',
};

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function detectFormat(doc: unknown): 'pmi-trace' | 'canvas-timeline' | 'unknown' {
  if (isObj(doc) && Array.isArray(doc.trace_events)) return 'pmi-trace';
  if (Array.isArray(doc) && doc.length > 0 && isObj(doc[0]) && 'trace_id' in doc[0]) return 'pmi-trace';
  if (isObj(doc) && 'trace_version' in doc && 'layer' in doc) return 'pmi-trace';
  if (isObj(doc) && Array.isArray(doc.timeline_events)) return 'canvas-timeline';
  return 'unknown';
}

export function normalizeTrace(doc: unknown): NormalizedTrace {
  const format = detectFormat(doc);
  if (format === 'pmi-trace') return normalizePmi(doc);
  if (format === 'canvas-timeline') return normalizeCanvas(doc);
  return { format: 'canvas-timeline', title: '无法识别的 trace 格式', traceId: null, lanes: [], nodes: [] };
}

function normalizePmi(doc: unknown): NormalizedTrace {
  let events: Record<string, unknown>[];
  let title = 'PMI Trace';
  if (isObj(doc) && Array.isArray(doc.trace_events)) {
    events = doc.trace_events.filter(isObj);
    title = str(doc.sample_set_id, 'PMI Trace 样例集');
  } else if (Array.isArray(doc)) {
    events = doc.filter(isObj);
  } else if (isObj(doc)) {
    events = [doc];
  } else {
    events = [];
  }

  const traceId = events.length > 0 ? str(events[0].trace_id, null as unknown as string) || null : null;
  const nodes: TimelineNode[] = [];
  const laneSet: string[] = [];

  const pushLane = (lane: string) => {
    if (!laneSet.includes(lane)) laneSet.push(lane);
  };

  const sorted = [...events].sort((a, b) => {
    const ta = str(a.ts);
    const tb = str(b.ts);
    if (ta !== tb) return ta < tb ? -1 : 1;
    return (Number(a.seq) || 0) - (Number(b.seq) || 0);
  });

  for (const ev of sorted) {
    const layer = str(ev.layer);
    const agent = isObj(ev.agent) ? ev.agent : {};
    const actor = isObj(ev.actor) ? ev.actor : {};
    const qt = isObj(ev.query_target) ? ev.query_target : {};
    const wb = isObj(ev.writeback) ? ev.writeback : {};
    const agentId = str(agent.agent_id, 'unknown-agent');
    const actorId = str(actor.actor_id, 'unknown-actor');
    const seq = typeof ev.seq === 'number' ? ev.seq : null;
    const layerLane = LAYER_LANE_NAMES[layer] ?? layer;

    pushLane(actorId);
    pushLane(agentId);
    pushLane(layerLane);

    const action = str(wb.action, 'none');
    const label =
      action !== 'none'
        ? `seq${seq ?? '?'} ${action}`
        : `seq${seq ?? '?'} ${str(qt.kind, 'query')}.${str(qt.record_kind, '')}`;
    const subtitle = `${str(qt.record_id, '')} · ${layer}`;

    nodes.push({
      id: `pmi-${traceId ?? 'x'}-${seq ?? nodes.length}`,
      lane: agentId,
      ts: str(ev.ts),
      seq,
      label,
      subtitle,
      color: LAYER_COLORS[layer] ?? '#475569',
      raw: ev,
      related: [
        { lane: actorId, role: 'actor', label: `发起 ${actorId}` },
        { lane: layerLane, role: 'target', label: `${action === 'none' ? '查询' : '写回'} ${layerLane}` },
      ],
    });
  }

  return { format: 'pmi-trace', title, traceId, lanes: laneSet, nodes };
}

function normalizeCanvas(doc: unknown): NormalizedTrace {
  if (!isObj(doc)) {
    return { format: 'canvas-timeline', title: 'canvas trace', traceId: null, lanes: [], nodes: [] };
  }
  const events = (doc.timeline_events as unknown[]).filter(isObj);
  const nodes: TimelineNode[] = [];
  const laneSet: string[] = [];

  const sorted = [...events].sort((a, b) => {
    const ta = str(a.created_at);
    const tb = str(b.created_at);
    if (ta !== tb) return ta < tb ? -1 : 1;
    return (Number(a.seq) || 0) - (Number(b.seq) || 0);
  });

  for (const ev of sorted) {
    const agentId = str(ev.agent_id) || 'canvas';
    if (!laneSet.includes(agentId)) laneSet.push(agentId);
    const details = isObj(ev.details) ? ev.details : {};
    const seq = typeof ev.seq === 'number' ? ev.seq : null;
    nodes.push({
      id: `canvas-${seq ?? nodes.length}`,
      lane: agentId,
      ts: str(ev.created_at),
      seq,
      label: `seq${seq ?? '?'} ${str(ev.event_type)}`,
      subtitle: str(details.stage, '') || str(ev.task_id, ''),
      color: '#475569',
      raw: ev,
      related: [],
    });
  }

  return {
    format: 'canvas-timeline',
    title: str(doc.title, 'Canvas 往返 trace'),
    traceId: str(doc.canvas_instance_id) || null,
    lanes: laneSet,
    nodes,
  };
}
