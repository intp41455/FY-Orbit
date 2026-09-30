import { request } from './client';

export interface CanvasTemplate {
  id: string;
  name: string;
  domain: string;
  center: string;
  workers: string[];
  description: string;
  default_budget: number;
  max_depth: number;
}

export interface ConnectorProbe {
  name: string;
  protocol: string;
  role: string;
  stage: '仅设计' | '发现接口' | '本机握手通过' | '合成任务往返' | '真实授权任务往返' | '生产可用';
  healthy: boolean;
  binary_path: string | null;
  blocking_reason: string | null;
  domains: string[];
}

export interface CanvasInstance {
  id: string;
  owner_id: string;
  project_name: string;
  domain: string;
  template_id: string;
  orchestrator_id: string;
  state: string;
  config: Record<string, unknown>;
  created_at: string;
}

export interface CanvasDispatch {
  id: string;
  subtask_id: string;
  orchestrator_id: string;
  worker_id: string;
  goal: string;
  state: string;
  budget_slice: number;
  created_at: string;
}

export interface CanvasHandoff {
  id: string;
  stage: string;
  from: string;
  to: string;
  completed_items: string[];
  created_at: string;
}

export interface CanvasEventItem {
  seq: number;
  event_type: string;
  task_id: string | null;
  agent_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface CanvasSnapshot {
  instance: CanvasInstance;
  connectors: ConnectorProbe[];
  dispatches: CanvasDispatch[];
  handoffs: CanvasHandoff[];
  events: CanvasEventItem[];
}

export const canvasApi = {
  templates: () => request<{ items: CanvasTemplate[] }>('/api/canvas/templates'),
  connectors: () => request<{ items: ConnectorProbe[] }>('/api/canvas/connectors'),
  createInstance: (body: { project_name: string; template_id?: string; orchestrator_id?: string }) =>
    request<CanvasInstance>('/api/canvas/instances', { method: 'POST', body }),
  listInstances: () => request<{ items: CanvasInstance[]; count: number }>('/api/canvas/instances'),
  getInstance: (id: string) => request<CanvasInstance>(`/api/canvas/instances/${id}`),
  getSnapshot: (id: string) => request<CanvasSnapshot>(`/api/canvas/instances/${id}/snapshot`),
  getEvents: (id: string, cursor: number = 0) =>
    request<{ items: CanvasEventItem[]; count: number }>(`/api/canvas/instances/${id}/events?cursor=${cursor}`),
  dispatchSubtask: (
    instanceId: string,
    body: {
      root_task_id: string;
      worker_id: string;
      goal: string;
      acceptance_criteria?: string;
      budget_slice?: number;
    }
  ) => request<CanvasDispatch>(`/api/canvas/instances/${instanceId}/dispatch`, { method: 'POST', body }),
  recordHandoff: (
    instanceId: string,
    body: {
      stage: string;
      goal: string;
      source_worker_id: string;
      target_worker_id: string;
      source_task_id: string;
      completed_items: string[];
      artifact_refs?: string[];
      evidence_refs?: string[];
      unresolved_issues?: string[];
      risks?: string[];
      next_steps?: string[];
    }
  ) => request<CanvasHandoff>(`/api/canvas/instances/${instanceId}/handoff`, { method: 'POST', body }),
};
