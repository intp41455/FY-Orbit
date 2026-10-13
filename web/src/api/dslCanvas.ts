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
import { ApiError, request } from './client';

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
 * 逐字对齐（3 基础 + 13 新增 + 2 拓扑）。`DslNodeType` 由本常量派生，面板/属性
 * 表单/校验一律 import 它——前端不再有第二份节点清单可以漂移。
 *
 * 验收真源仍是 `GET /api/dsl-canvas/schema` 的 `node_types`（后端权威）；
 * 这里只在 schema 拉取失败时充当如实回落的本地清单。
 */
export const DSL_NODE_TYPES = [
  'input', 'transform', 'output',
  'llm', 'knowledge_retrieval', 'question_classifier', 'parameter_extractor',
  'iteration', 'loop', 'variable_aggregator', 'template', 'http_request',
  'code', 'tool', 'human_input', 'trigger',
  'parallel',    // 并行分支扇出（对标 LangGraph 并行边 + Send）
  'subgraph',    // 引用命名子图（对标 LangGraph 子图作为节点）
] as const;

export type DslNodeType = (typeof DSL_NODE_TYPES)[number];

/** 3 类基础之外的节点（13 类新增 + 2 类拓扑；由单一真源切出，不另写清单）。 */
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

/**
 * 三重模式（A-三重模式-01/04）：小白 / 技术 / 企业三入口，**同一套 IR**。
 *
 * 数据来自 `GET /api/dsl/modes`（后端 `dsl_sdk.mode_overview()`）——入口名、
 * 起手模板、模板真正的 DSL 文档都由后端给，前端不自建一份同名假模板。
 */
export const DSL_MODES = ['beginner', 'technical', 'enterprise'] as const;
export type DslMode = (typeof DSL_MODES)[number];

export interface DslModeEntry {
  mode: DslMode;
  entry_id: string;
  label: string;
  description: string;
  builtin: boolean;
}

export interface DslModeTemplate {
  template_id: string;
  mode: DslMode;
  label: string;
  description: string;
  /** 模板真正的 IR 文档（模板起手直接用这份图）。 */
  dsl: DslDocument;
}

export interface DslModeOverview {
  mode: DslMode;
  label: string;
  entries: DslModeEntry[];
  templates: DslModeTemplate[];
  default_template: string;
}

export interface DslSchemaResponse {
  schema: unknown;
  node_types: string[];
  transform_verbs: DslTransformVerb[];
  verb_catalog: DslVerbCatalogEntry[];
  aggregate_ops: string[];
  merge_ops: string[];
  output_formats: string[];
  /**
   * G4-j：节点级参数契约（后端 `NODE_PARAMS_SCHEMAS` 单一真源，逐字下发）。
   * `{节点类型: JSON Schema}`；前端据此驱动参数枚举，不再手抄镜像。
   * 可选：老后端/降级响应可能不含此键，此时前端如实回落到本地常量。
   */
  node_params_schema?: Record<string, Record<string, unknown>>;
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

/**
 * G4-d · 内联调参的即时重算结果。
 *
 * 与 {@link DslRunResult} 的差别只有 `preview_only: true` —— 后端跑的是**同一套**
 * 执行器、同一份文档，唯一不做的是 `_store.save()`。命中 `approval` / `confirm`
 * 时预览**必然**挂起（HTTP 202 + {@link DslSuspendedResult}），因为预览没有上一轮
 * 人工裁决可回查，也绝不代治理层做决定。
 *
 * 因此调用方判别方式是 `status === 'suspended'`，与真实运行一致。
 */
export type DslPreviewResult =
  | (DslRunResult & { preview_only: true })
  | (DslSuspendedResult & { preview_only: true });

/**
 * N3 · G2 实时推送：`POST /api/dsl-canvas/runs` 带 `stream: true` 时的 202 返回体。
 * 后端随即把执行放进后台，进度改由
 * `GET /api/dsl-canvas/runs/{run_id}/events`（SSE）推送（见 `api/dslRunStream.ts`）。
 */
export interface DslRunStreamStart {
  run_id: string;
  status: 'running';
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

/**
 * N3 · 草稿生命周期视图（`GET /api/dsl-lifecycle/flows/{flow_id}` 返回体，
 * 与后端 `services/dsl_canvas.py::_flow_view` 对齐）。
 *
 * `draft_version` 是**乐观锁的版本号**：保存草稿时作为 `expected_version`
 * 回传；服务端发现它与当前 `draft_version` 不一致就抛 409。
 */
export interface DslFlowView {
  id: string;
  owner_id?: string;
  name?: string;
  flow_type: string;
  draft_doc: DslDocument | null;
  published_doc?: DslDocument | null;
  published_version: number;
  draft_version: number;
  updated_at?: string;
}

/**
 * N3 · 草稿版本冲突（HTTP 409）。
 *
 * 为什么需要本地错误类：后端 `routes/dsl_lifecycle.py::_translate` 把领域异常
 * 转成 `HTTPException(status_code, detail=...)`，结构化的 `error.code`
 * （`dsl_draft_version_conflict`）在 HTTP 路径上**已被吞掉**，前端拿不到。
 * 唯一可靠的判据是 HTTP 状态码 409 —— 所以这里按 status 判定并抛出带
 * `status` 字段的本地类型，调用方用 `isDslDraftConflict(e)` 或
 * `e instanceof DslDraftConflictError` 判定，**绝不匹配中/英文字符串**。
 */
export class DslDraftConflictError extends Error {
  /** 恒为 409，便于调用方按数字判别（无需 `instanceof` 跨 realms 假设）。 */
  readonly status = 409;
  readonly flowId: string;
  constructor(flowId: string, message?: string) {
    super(message ?? '草稿已被其他会话修改，需重新读取后再保存');
    this.name = 'DslDraftConflictError';
    this.flowId = flowId;
  }
}

/** 类型守卫：判断任意捕获值是否为「草稿版本冲突」。按 `status === 409` 判定。 */
export function isDslDraftConflict(e: unknown): e is DslDraftConflictError {
  if (e instanceof DslDraftConflictError) return true;
  return e instanceof ApiError && e.status === 409;
}

export const DSL_DRAFT_CONFLICT_STATUS = 409;

export interface SaveDraftOptions {
  flow_type?: string;
  /**
   * 乐观锁版本号：传最近一次**读取或保存**返回的 `draft_version`。
   * 省略（undefined）= 不参与乐观锁，服务端无条件覆盖（向后兼容旧调用方）。
   */
  expected_version?: number;
}

export const dslCanvasApi = {
  schema: () => request<DslSchemaResponse>('/api/dsl-canvas/schema'),
  /** 三重模式概览（小白 / 技术 / 企业；含各自的起手模板与模板 DSL）。 */
  modes: () => request<{ modes: DslModeOverview[] }>('/api/dsl/modes'),
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
   * N3 · 流式执行：带 `stream: true`，后端返回 **202** + {@link DslRunStreamStart}
   * 并在后台执行；实时进度走 `events` SSE（`dslRunStream.ts`）。
   * 未启用流式的后端会忽略该字段、退回同步 200，调用方需按返回体判别（见画布 execute）。
   */
  runStream: (dsl: DslDocument) => request<DslRunStreamStart>('/api/dsl-canvas/runs', {
    method: 'POST', body: { dsl, stream: true } }),
  /**
   * 执行工作流。走到 `confirm` 且尚无人工裁决时后端返回 **202** +
   * {@link DslSuspendedResult}（**不是**失败）。
   */
  runWithExecution: (dsl: DslDocument, executionId: string) =>
    request<DslRunResult | DslSuspendedResult>('/api/dsl-canvas/runs', {
      method: 'POST', body: { dsl, execution_id: executionId } }),
  getRun: (runId: string) => request<DslRunResult>(`/api/dsl-canvas/runs/${runId}`),
  /**
   * G4-d · 画布内联调参的即时重算：**执行但不落库**。
   *
   * 与 {@link dslCanvasApi.run} 的唯一区别是后端不做 `_store.save()`，所以不会把
   * 用户的每一次试参挤进 run 历史（内存缓冲 `max_in_memory=200`）。
   *
   * **参数改动不走这里。** 调用方先把改动写进 `doc` 再整体提交，本方法只负责跑 ——
   * 后端没有 overrides 通道，也就不存在「doc 改了但覆盖层还是旧值」的漂移。
   *
   * 非法文档后端返 422（抛 {@link ApiError}）；含 `approval` / `confirm` 的图返
   * 202 + `status: 'suspended'`，`fetch` 的 2xx 判定不拦它，本方法照常 resolve。
   */
  preview: (dsl: DslDocument) => request<DslPreviewResult>('/api/dsl-canvas/preview', {
    method: 'POST', body: { dsl } }),
  /** 画布 → 受限 Python 代码（导出物只可能由受限动词集构成）。 */
  exportCode: (dsl: DslDocument) => request<DslCodeExport>(
    '/api/dsl-canvas/export-code', { method: 'POST', body: { dsl } }),
  /** 受限 Python 代码 → 画布（与 exportCode 往返无损）。 */
  importCode: (code: string) => request<{ dsl: DslDocument; topological_order: string[] }>(
    '/api/dsl-canvas/import-code', { method: 'POST', body: { code } }),

  // ── N3 · 草稿生命周期（持久化 + 乐观锁）──────────────────────────────

  /**
   * 新建流程（拿到 `flow_id` 的唯一入口）。
   * 不传 `doc` 时后端草稿为空；注意后端 `validate_dsl` 要求 `nodes` 非空，
   * 所以**空画布不要拿来存草稿**（会422）。
   */
  createFlow: (input: { name: string; flow_type?: string; doc?: DslDocument }) =>
    request<DslFlowView>('/api/dsl-lifecycle/flows', {
      method: 'POST', body: { name: input.name, flow_type: input.flow_type ?? 'workflow', doc: input.doc } }),

  /** 读取流程（含 `draft_doc` 与乐观锁版本号 `draft_version`）。 */
  getFlow: (flowId: string) => request<DslFlowView>(`/api/dsl-lifecycle/flows/${flowId}`),

  /**
   * 保存草稿。
   *
   * 传 `expected_version` 即启用乐观锁：服务端版本不一致 → **HTTP 409**，
   * 本方法把它转成 {@link DslDraftConflictError} 抛出（**不静默重试、不覆盖**）。
   * 调用方拿到冲突后应提示用户并给出「重新读取」/「显式覆盖」出口。
   *
   * 成功返回体含**已 +1** 的 `draft_version`，须回写到本地版本状态。
   */
  saveDraft: async (flowId: string, doc: DslDocument, options: SaveDraftOptions = {}): Promise<DslFlowView> => {
    const body: { doc: DslDocument; flow_type?: string; expected_version?: number } = { doc };
    if (options.flow_type !== undefined) body.flow_type = options.flow_type;
    if (options.expected_version !== undefined) body.expected_version = options.expected_version;
    try {
      return await request<DslFlowView>(`/api/dsl-lifecycle/flows/${flowId}/draft`, {
        method: 'PUT', body,
      });
    } catch (e) {
      if (isDslDraftConflict(e)) throw new DslDraftConflictError(flowId);
      throw e;
    }
  },
};
