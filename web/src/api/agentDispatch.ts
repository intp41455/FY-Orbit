// P1-20 子 Agent 派发验证 API 客户端。
import { request } from './client';

export type AcceptanceType = 'output_contains' | 'tool_invoked';

export interface TaskSpec {
  capability: string;
  payload?: Record<string, unknown>;
  acceptance: Record<string, unknown>;
  env_contract?: Record<string, unknown>;
}

export interface ToolCallSpec {
  name: string;
  arguments?: Record<string, unknown>;
}

export interface ToolCallRecord {
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  call_id: string | null;
  result: unknown;
  error: string | null;
}

export interface Verification {
  verdict: 'verified' | 'rejected';
  checks: { check: string; target: string; passed: boolean }[];
  reasons: string[];
  self_report_status: string | null;
  consistent: boolean;
  verified_at: string;
}

export interface ChildTrace {
  child_task_id: string;
  parent_task_id: string;
  capability: string;
  status: 'dispatched' | 'verifying' | 'completed' | 'verified' | 'rejected';
  dispatched_at: string;
  finished_at: string | null;
  tool_calls: ToolCallRecord[];
  result_text: string | null;
  self_report: { status: string; summary: string } | null;
  verification: Verification | null;
  experience: { written_back: boolean; written_at: string; scope: string; lesson: string } | null;
}

export interface ParentTrace {
  parent_task_id: string;
  task: TaskSpec;
  status: string;
  dispatched_at: string;
  verified_at: string | null;
  children: ChildTrace[];
}

export const agentDispatchApi = {
  dispatch: (task: TaskSpec, tools: ToolCallSpec[]) =>
    request<ParentTrace>('/api/agent-dispatch', {
      method: 'POST',
      body: { task, tools },
    }),
  get: (parentId: string) =>
    request<ParentTrace>(`/api/agent-dispatch/${parentId}`),
  list: () =>
    request<{ count: number; parent_task_ids: string[] }>('/api/agent-dispatch'),
};
