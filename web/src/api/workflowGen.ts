// W5 工作流工坊 · API 客户端（对应 api/routes/workflow_gen.py）。
//
// 三层视图共用同一个 DSL 源：这里同时提供「自然语言入口」（generate）与
// 「导出出口」（export），图↔DSL 互转走服务端，保证与后端 validate 是同一套
// 语义——前端本地快校验只是体验，后端才是权威。
//
// 诚实契约（总纲铁律 3 / FROZEN_CONTRACT §11）：
//  - 未配置模型 → 后端 503 model_not_configured，前端**如实展示**该错误，
//    绝不用预置模板冒充「已生成」；
//  - 非法 DSL → 后端 422 workflow_dsl_invalid，details 带行列级信息，
//    页面顶部如实显示。
import { request } from './client';
// DSL 语义模型的**唯一真源**是 api/dslCanvas.ts（与后端 services/dsl_canvas.py 对齐）。
// 这里直接复用，避免第二份结构漂移导致两层类型不兼容。
import type { DslDocument } from './dslCanvas';

export type { DslDocument };

export interface WorkflowGenerateResponse {
  dsl: DslDocument;
  model: string;
  provider_id: string;
  attempts: { attempt: number; ok: boolean; error: string }[];
  usage: Record<string, number>;
  settled_usd: string;
  /** 降级可见：非空表示实际由 fallback provider 应答。 */
  degraded_from: string;
  degraded_reason: string;
}

export interface WorkflowExportResponse {
  filename: string;
  language: string;
  requires_python: string;
  base_url_placeholder: string;
  source_prompt: string;
  generated_at: string;
  script: string;
}

export interface WorkflowGraphNode {
  id: string;
  type: string;
  verb: string | null;
  params: Record<string, unknown>;
  x: number;
  y: number;
}

export interface WorkflowGraphEdge {
  from: string;
  to: string;
  condition: Record<string, unknown> | null;
}

export interface WorkflowGraph {
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}

export const workflowGenApi = {
  status: () => request<{ model_configured: boolean }>('/api/workflow/status'),

  generate: (prompt: string) => request<WorkflowGenerateResponse>('/api/workflow/generate', {
    method: 'POST', body: { prompt } }),

  exportScript: (dsl: DslDocument, prompt?: string) =>
    request<WorkflowExportResponse>('/api/workflow/export', {
      method: 'POST', body: { dsl, prompt: prompt ?? null } }),

  /** DSL → 图。传 dsl_text 时后端会返回行列级错误。 */
  graphFromDsl: (dsl: DslDocument, layout?: Record<string, { x: number; y: number }>) =>
    request<{ graph: WorkflowGraph }>('/api/workflow/graph/from-dsl', {
      method: 'POST', body: { dsl, layout: layout ?? null } }),

  graphToDsl: (graph: WorkflowGraph) =>
    request<{ dsl: DslDocument }>('/api/workflow/graph/to-dsl', {
      method: 'POST', body: graph }),
};