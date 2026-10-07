// P1-18 受限 DSL 画布 API 客户端。
//
// 动词单一真源：本文件的 `DSL_TRANSFORM_VERBS` 常量（10 个动词，含治理类
// `approval`），顺序与后端 `services/dsl_canvas.py` 的 `VERB_REGISTRY` 插入序
// 逐字对齐；`DslTransformVerb` 类型由该常量派生，类型与清单不可能漂移。
// 后端动词集是**封闭白名单**，多写一个字后端就422，所以这里只列真实存在的动词。
// （B3 修复：旧版注释自称「逐字对齐」却漏了 approval——只有 9 个动词，
// 三方画布动词集不一致。现在本常量是前端唯一动词清单，画布组件一律 import 它。）
// 完整契约（分类/ 参数 JSON Schema / 可执行性）走 `schema()` 拿 `verb_catalog`，
// 不在前端另抄一份。
import { request } from './client';

/**
 * 受限动词集单一真源（前端）：顺序 = 后端 `VERB_REGISTRY` 插入序
 * （map, filter, template, branch, aggregate, merge, agent, confirm, artifact, approval）。
 * 新增动词时只改这一处：类型、画布下拉、校验全部随之更新。
 */
export const DSL_TRANSFORM_VERBS = [
  'map',                                 // 数据变换
  'filter',                              // 数据变换
  'template',                            // 数据变换
  'branch',                              // 流程控制
  'aggregate',                           // 流程控制
  'merge',                               // 流程控制
  'agent',                               // Agent / 工具调用
  'confirm',                             // 人机协作（HITL 未接入，执行必定失败）
  'artifact',                            // 输出产物
  'approval',                            // 治理（委托 proposal.py，挂起等裁决）
] as const;

export type DslTransformVerb = (typeof DSL_TRANSFORM_VERBS)[number];

/**
 * 节点类型单一真源（前端）：顺序与后端 `services/dsl_canvas.py` 的 `NODE_TYPES`
 * 逐字对齐（3 基础 + 13 新增）。`DslNodeType` 由本常量派生，面板/属性表单/校验
 * 一律 import 它——前端不再有第二份节点清单可以漂移。
 *
 * 验收真源仍是 `GET /api/dsl-canvas/schema` 的 `node_types`（后端权威）；
 * 这里只在 schema 拉取失败时充当如实回落的本地清单。
 */
export const DSL_NODE_TYPES = [
  'input', 'transform', 'output',
  'llm', 'knowledge_retrieval', 'question_classifier', 'parameter_extractor',
  'iteration', 'loop', 'variable_aggregator', 'template', 'http_request',
  'code', 'tool', 'human_input', 'trigger',
] as const;

export type DslNodeType = (typeof DSL_NODE_TYPES)[number];

/** 13 类新增节点（对标 Dify 节点库，A-画布搭建器-01）。 */
export const EXTENDED_NODE_TYPES: readonly DslNodeType[] =
  DSL_NODE_TYPES.slice(3);

/** 动词集元数据（后端 verb_catalog 的一项）。 */
export interface DslVerbCatalogEntry {
  name: DslTransformVerb;
  category: string;
  summary: string;
  params_schema: Record<string, unknown>;
  /** false 表示动词位已占但后端未接入（如 confirm），UI 应显式提示而非假装可用。 */
  executable: boolean;
}

export interface DslSchemaResponse {
  schema: unknown;
  node_types: string[];
  transform_verbs: DslTransformVerb[];
  verb_catalog: DslVerbCatalogEntry[];
  aggregate_ops: string[];
  merge_ops: string[];
  output_formats: string[];
}

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
  /** suspended = 走到confirm 且尚无人工裁决（需求 12 的接入点），非失败。 */
  status: 'succeeded' | 'skipped' | 'failed' | 'suspended';
  input: unknown;
  output: unknown;
  error: string | null;
  started_at: string;
  finished_at: string;
}

/** 挂起载荷：上层据此建 HITL interrupt，人裁决后带 execution_id 重开一轮。 */
export interface DslSuspended {
  checkpoint: string;
  dsl_digest: string;
  context: { prompt: string; role: string; node_id: string };
  options: { value: string; label: string }[];
  node_id: string;
}

export interface DslSuspendedResult {
  status: 'suspended';
  suspended: DslSuspended;
}

export interface DslRunResult {
  run_id: string;
  status: 'succeeded' | 'failed';
  dsl: DslDocument;
  output: unknown;
  error: string | null;
  /** 由调用方显式传入的稳定执行标识（挂起时用于关联 interrupt）。 */
  execution_id: string | null;
  created_at: string;
  logs: DslRunLog[];
}

export interface DslCodeExport {
  filename: string;
  language: string;
  requires_python: string;
  code: string;
  node_count: number;
  edge_count: number;
  verbs: DslTransformVerb[];
  /** B4 自包含导出：随导出物交付的 find_yourself_dsl 运行库源码（相对路径 → 文本）。 */
  runtime_dirname?: string;
  runtime_files?: Record<string, string>;
}

/**
 * P1 · 后端类型化 IR 的一条诊断（与 `services/dsl_ir.py::Diagnostic` 逐字对齐）。
 * `field_path` 是点分路径（如 `params.op`）；文档级错误 `node_id` 为空串、
 * 无具体字段时 `field_path` 为空串。
 */
export interface DslDiagnostic {
  node_id: string;
  field_path: string;
  code: string;
  message: string;
}

export interface DslIrValidation {
  valid: boolean;
  /** 收集式：一次给出**全部**诊断（绝不只报第一条）。 */
  diagnostics: DslDiagnostic[];
}

export const dslCanvasApi = {
  schema: () => request<DslSchemaResponse>('/api/dsl-canvas/schema'),
  validate: (dsl: DslDocument) => request<{ valid: boolean; topological_order: string[] }>(
    '/api/dsl-canvas/validate', { method: 'POST', body: { dsl } }),
  /**
   * P1 · 收集式 IR 校验：一次返回全部 `DslDiagnostic`（不 fail-fast）。
   * 画布红点 / 属性面板字段级错误都以此为准。
   */
  validateIr: (dsl: DslDocument) => request<DslIrValidation>(
    '/api/dsl-canvas/validate-ir', { method: 'POST', body: { dsl } }),
  run: (dsl: DslDocument) => request<DslRunResult>('/api/dsl-canvas/runs', {
    method: 'POST', body: { dsl } }),
  /**
   * 执行工作流。走到 `confirm` 且尚无人工裁决时后端返回 **202** +
   * {@link DslSuspendedResult}（**不是**失败）。
   */
  runWithExecution: (dsl: DslDocument, executionId: string) =>
    request<DslRunResult | DslSuspendedResult>('/api/dsl-canvas/runs', {
      method: 'POST', body: { dsl, execution_id: executionId } }),
  getRun: (runId: string) => request<DslRunResult>(`/api/dsl-canvas/runs/${runId}`),
  /** 画布 → 受限 Python 代码（导出物只可能由受限动词集构成）。 */
  exportCode: (dsl: DslDocument) => request<DslCodeExport>(
    '/api/dsl-canvas/export-code', { method: 'POST', body: { dsl } }),
  /** 受限 Python 代码 → 画布（与 exportCode 往返无损）。 */
  importCode: (code: string) => request<{ dsl: DslDocument; topological_order: string[] }>(
    '/api/dsl-canvas/import-code', { method: 'POST', body: { code } }),
};
