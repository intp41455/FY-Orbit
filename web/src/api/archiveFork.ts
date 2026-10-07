/**
 * P4 · 存档回溯分支（A-存档回溯-04/05/06）后端通道。
 *
 * 对应 /api/session-state 下的分叉/对比/时间线端点。
 */
import { request } from './client';

export interface ForkPlanView {
  fork_id: string;
  source_thread_id: string;
  source_checkpoint_id: string;
  new_thread_id: string;
  overrides: Record<string, unknown>;
  snapshot_id: string;
  session_key: string;
  label: string;
}

export interface ForkRecordView extends ForkPlanView {
  state: 'active' | 'discarded';
  created_at: string;
}

export interface TimelineEvent {
  kind: 'fork' | 'discard';
  at: string;
  fork_id: string;
  thread_id: string;
  parent_thread_id?: string;
  parent_checkpoint_id?: string;
  snapshot_id?: string;
  session_key?: string;
  label?: string;
  state?: string;
  overrides?: Record<string, unknown>;
  reason?: string;
}

export interface CompareResult {
  left_label: string;
  right_label: string;
  identical: boolean;
  summary: {
    total_keys: number;
    same: number;
    changed: number;
    only_left: number;
    only_right: number;
  };
  same: string[];
  only_left: string[];
  only_right: string[];
  changed: Array<{ key: string; left: unknown; right: unknown }>;
}

/** A-存档回溯-04：从存档点改参重跑生成新分支（不覆盖原历史）。 */
export function createFork(input: {
  source_thread_id: string;
  source_checkpoint_id?: string;
  overrides?: Record<string, unknown>;
  snapshot_id?: string;
  session_key?: string;
  label?: string;
}): Promise<{ status: string; fork: ForkPlanView }> {
  return request<{ status: string; fork: ForkPlanView }>('/api/session-state/forks', {
    method: 'POST',
    body: input,
  });
}

/** 列出分支（可按源 thread / 状态过滤）。 */
export function listForks(params?: {
  source_thread_id?: string;
  state?: string;
}): Promise<{ forks: ForkRecordView[] }> {
  const q = new URLSearchParams();
  if (params?.source_thread_id) q.set('source_thread_id', params.source_thread_id);
  if (params?.state) q.set('state', params.state);
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return request<{ forks: ForkRecordView[] }>(`/api/session-state/forks${suffix}`);
}

/** 弃用分支（留档不删）。 */
export function discardFork(
  forkId: string,
  reason = '',
): Promise<{ status: string; fork_id: string; state: string }> {
  const q = reason ? `?reason=${encodeURIComponent(reason)}` : '';
  return request<{ status: string; fork_id: string; state: string }>(
    `/api/session-state/forks/${encodeURIComponent(forkId)}/discard${q}`,
    { method: 'POST' },
  );
}

/** A-存档回溯-05：分支对比（可传两侧结果即时比对）。 */
export function compareFork(
  forkId: string,
  payload?: { left?: Record<string, unknown>; right?: Record<string, unknown> },
): Promise<CompareResult> {
  return request<CompareResult>(
    `/api/session-state/forks/${encodeURIComponent(forkId)}/compare`,
    { method: 'POST', body: payload ?? {} },
  );
}

/** A-存档回溯-06：存档历史时间线（按时间升序）。 */
export function fetchTimeline(params?: {
  source_thread_id?: string;
  limit?: number;
}): Promise<{ events: TimelineEvent[]; count: number }> {
  const q = new URLSearchParams();
  if (params?.source_thread_id) q.set('source_thread_id', params.source_thread_id);
  if (params?.limit) q.set('limit', String(params.limit));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return request<{ events: TimelineEvent[]; count: number }>(
    `/api/session-state/timeline${suffix}`,
  );
}
