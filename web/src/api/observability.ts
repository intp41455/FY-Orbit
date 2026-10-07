// 可观测与性能监控 API 客户端（A-可观测-01~04 · P2 / A-成本-02 · P3）
import { request } from './client';

export interface LogItem {
  seq: number;
  level: string;
  actor: string;
  action: string;
  target?: string;
  surface?: string;
  details?: Record<string, unknown>;
  trace_id?: string;
}

export interface LogsResponse {
  items: LogItem[];
  total: number;
  facets: {
    levels: Record<string, number>;
    surfaces?: Record<string, number>;
  };
  seq_watermark: {
    min_seq: number;
    max_seq: number;
  };
}

export interface PerformanceStats {
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  throughput_hourly: Record<string, number>;
  active_span_ms: number | null;
  total_events: number;
  partial: boolean;
}

export interface AgentPerformanceStats {
  agents: Array<{
    agent_id: string;
    calls_count: number;
    avg_latency_ms: number | null;
    error_count: number;
    error_rate: number;
  }>;
}

export interface CostProgressResponse {
  task_id: string | null;
  steps_completed: number;
  max_steps: number | null;
  step_progress_pct: number | null;
  remaining_steps_upper_bound: number | null;
  estimated_remaining_ms: number | null;
  budget_tokens: {
    reserved: number;
    settled: number;
    unknown: number;
  };
}

export async function fetchLogs(params?: {
  level?: string;
  actor?: string;
  surface?: string;
  since_seq?: number;
  limit?: number;
  offset?: number;
}): Promise<LogsResponse> {
  const query = new URLSearchParams();
  if (params?.level) query.set('level', params.level);
  if (params?.actor) query.set('actor', params.actor);
  if (params?.surface) query.set('surface', params.surface);
  if (params?.since_seq !== undefined) query.set('since_seq', String(params.since_seq));
  if (params?.limit !== undefined) query.set('limit', String(params.limit));
  if (params?.offset !== undefined) query.set('offset', String(params.offset));

  const qs = query.toString();
  return request<LogsResponse>(`/api/observability/logs${qs ? `?${qs}` : ''}`);
}

export async function fetchPerformance(): Promise<PerformanceStats> {
  return request<PerformanceStats>('/api/observability/performance');
}

export async function fetchAgentPerformance(): Promise<AgentPerformanceStats> {
  return request<AgentPerformanceStats>('/api/observability/performance/agents');
}

export async function fetchCostProgress(taskId?: string): Promise<CostProgressResponse> {
  const qs = taskId ? `?task_id=${encodeURIComponent(taskId)}` : '';
  return request<CostProgressResponse>(`/api/observability/cost/progress${qs}`);
}

export async function fetchLogTrace(seq: number): Promise<{
  basis: string;
  reason?: string;
  trace_id?: string;
  events?: unknown[];
}> {
  return request(`/api/observability/logs/${seq}/trace`);
}
