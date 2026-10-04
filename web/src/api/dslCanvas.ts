// P1-18 受限 DSL 画布 API 客户端。
import { request } from './client';

export type DslNodeType = 'input' | 'transform' | 'output';
export type DslTransformVerb = 'map' | 'filter' | 'template';

export interface DslNode {
  id: string;
  type: DslNodeType;
  verb?: DslTransformVerb;
  params?: Record<string, unknown>;
}

export interface DslEdgeCondition {
  field: string;
  op: 'eq' | 'ne' | 'gt' | 'lt' | 'contains';
  value: unknown;
}

export interface DslEdge {
  from: string;
  to: string;
  condition?: DslEdgeCondition;
}

/** DSL 模型（语义）。坐标等视图状态放在 LayoutState，绝不进入 DSL。 */
export interface DslDocument {
  version: '1';
  nodes: DslNode[];
  edges: DslEdge[];
}

/** 视图布局：只存节点坐标（model/layout 分离，claw §1.3）。 */
export type LayoutState = Record<string, { x: number; y: number }>;

export interface DslRunLog {
  node_id: string;
  node_type: string;
  verb: string | null;
  status: 'succeeded' | 'skipped' | 'failed';
  input: unknown;
  output: unknown;
  error: string | null;
  started_at: string;
  finished_at: string;
}

export interface DslRunResult {
  run_id: string;
  status: 'succeeded' | 'failed';
  dsl: DslDocument;
  output: unknown;
  error: string | null;
  created_at: string;
  logs: DslRunLog[];
}

export const dslCanvasApi = {
  schema: () => request<{ schema: unknown; node_types: string[]; transform_verbs: string[] }>(
    '/api/dsl-canvas/schema'),
  validate: (dsl: DslDocument) => request<{ valid: boolean; topological_order: string[] }>(
    '/api/dsl-canvas/validate', { method: 'POST', body: { dsl } }),
  run: (dsl: DslDocument) => request<DslRunResult>('/api/dsl-canvas/runs', {
    method: 'POST', body: { dsl } }),
  getRun: (runId: string) => request<DslRunResult>(`/api/dsl-canvas/runs/${runId}`),
};
