// Typed client for the W6 超级中台适配器中心 API (mirrors api/routes/hub.py).
//
// Honesty contract (FROZEN_CONTRACT §11 / 交接总纲铁律 3):
//  - 探活/调用失败由后端返回 200 + {ok:false, error/detail}，页面必须原样展示，
//    绝不把失败渲染成「已连接」；
//  - 凭证永远拿不到明文：后端 public_connection 只回掩码，本层不做任何拼接；
//  - 预置里 implemented=false 的条目必须显示「内置骨架 · 未接入」，不能当可用项；
//  - 编排的「降级但成功」（meta.degraded_reason）必须单独标出来 —— 推理模型
//    被 max_tokens 截断时会降级返回思考过程，此时 ok=true 且有文本，肉眼完全
//    看不出拿到的不是正式答案。不标出来就等于把静默失败讲成了成功。
import { buildApiUrl, getCsrfToken } from './client';

const BASE = '/api/hub';

/** 后端 KIND_* 常量（services/hub/__init__.py）。 */
export type HubKind =
  | 'openai_chat'
  | 'anthropic'
  | 'mcp_server'
  | 'http_webhook'
  | 'tool_plugin'
  | 'knowledge_source';

export const HUB_KIND_LABEL: Record<HubKind, string> = {
  openai_chat: 'OpenAI 兼容对话',
  anthropic: 'Anthropic 兼容对话',
  mcp_server: 'MCP Server',
  http_webhook: 'HTTP Webhook',
  tool_plugin: '工具插件',
  knowledge_source: '知识源',
};

export interface HubCredentialField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
}

export interface HubCapability {
  name: string;
  description?: string;
  tags?: string[];
}

export interface HubHealth {
  /** null = 从未探活过（不是 true，也不是 false）。 */
  ok: boolean | null;
  checked_at: string | null;
  latency_ms: number | null;
  detail: string;
}

export interface HubConnection {
  id: string;
  name: string;
  kind: HubKind;
  group: string;
  preset_id: string;
  icon: string;
  description: string;
  state: string;
  /** 凭证字段在此已被后端替换为掩码（'****' 或自定义 mask）。 */
  config: Record<string, unknown>;
  secret_fields: string[];
  capabilities: HubCapability[];
  has_manifest: boolean;
  params: Record<string, unknown> | null;
  preference: number;
  health: HubHealth;
  version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface HubPreset {
  id: string;
  name: string;
  kind: HubKind;
  group: string;
  icon: string;
  description: string;
  config: Record<string, unknown>;
  credential_fields: HubCredentialField[];
  capability_tags: string[];
  capabilities: HubCapability[];
  /** false = 内置骨架，尚未真正接入（页面必须如实标注）。 */
  implemented: boolean;
  note: string;
}

export interface HubHealthReport {
  ok: boolean;
  detail: string;
  latency_ms: number;
  meta?: Record<string, unknown>;
}

export interface HubInvokeResult {
  ok: boolean;
  output: unknown;
  error: string;
  latency_ms: number;
  meta: Record<string, unknown>;
}

export interface HubCapabilityRow {
  connection_id: string;
  connection_name: string;
  kind: HubKind;
  group: string;
  icon: string;
  healthy: boolean | null;
  state: string;
  capability: HubCapability;
}

export interface HubRouteCandidate {
  connection_id: string;
  connection_name: string;
  kind: HubKind;
  group: string;
  icon: string;
  healthy: boolean | null;
  score: number;
  reasons: string[];
  capability: HubCapability;
}

export interface HubCreatePayload {
  name: string;
  kind: HubKind;
  preset_id?: string;
  icon?: string;
  description?: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  secret_fields?: string[];
  credential_fields?: HubCredentialField[];
  capabilities?: HubCapability[];
  params?: Record<string, unknown>;
  preference?: number;
}

export interface HubUpdatePayload {
  name?: string;
  icon?: string;
  description?: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  capabilities?: HubCapability[];
  preference?: number;
  state?: string;
}

// --------------------------------------------------------------------------- //
// 编排（对齐 services/hub/orchestrator.py 的 StepResult / OrchestrationResult）
// --------------------------------------------------------------------------- //

/** 编排形态。pipeline 是显式指定每步派给谁，走的是「不路由」的分支。 */
export type HubOrchestrateMode = 'sequential' | 'parallel' | 'pipeline';

export const HUB_ORCHESTRATE_MODE_LABEL: Record<HubOrchestrateMode, string> = {
  sequential: '顺序接力',
  parallel: '并行分工',
  pipeline: '显式流水线',
};

export interface HubStepResult {
  index: number;
  step: string;
  /** pipeline 走显式分支时可能只有 id 没有 name；空串就是空串，不猜。 */
  connection_id: string;
  connection_name: string;
  capability: string;
  ok: boolean;
  output: unknown;
  /** ok=false 时一定有值；成功时是空串而不是 null。 */
  error: string;
  latency_ms: number;
  /**
   * 上游调用元信息：provider_id / usage / degraded_reason / attempts。
   * degraded_reason 非空 = 这一步拿到的是**降级产物**（如被 max_tokens
   * 截断后返回的思考过程），不是模型的正式答案。页面必须显示。
   */
  meta: Record<string, unknown>;
}

export interface HubOrchestrationResult {
  ok: boolean;
  mode: string;
  steps: HubStepResult[];
  step_count: number;
  succeeded_count: number;
  failed_count: number;
  final_output: unknown;
  error: string;
  duration_ms: number;
  pending_interrupts: boolean;
}

/** 编排前置体检（GET /orchestrate/capabilities）。 */
export interface HubOrchestratePreflight {
  agent_count: number;
  capability_count: number;
  kinds: string[];
  can_orchestrate: boolean;
  max_workers: number;
}

/**
 * 自动派单请求体。
 *
 * 注意 `steps` 与 `aspects` 是按 mode 二选一传的：`sequential` 用 steps
 * （前一步输出拼进后一步上下文），`parallel` 用 aspects（各侧面互不知情）。
 * 两边都传时后端只按 mode 取一边，所以这里不替调用方做选择。
 */
export interface HubOrchestratePayload {
  task: string;
  mode?: Exclude<HubOrchestrateMode, 'pipeline'>;
  steps?: string[];
  aspects?: string[];
  kind?: string;
  max_workers?: number;
  timeout_seconds?: number;
  summarize?: boolean;
  summary_instruction?: string;
  llm_rerank?: boolean;
  llm_model?: string;
}

/** 显式流水线的一步。connection_id 必填，否则后端拿不到派工对象。 */
export interface HubPipelineStage {
  connection_id: string;
  /** 只影响文案，可省；省了后端会按连接 id 反查，查不到就留空。 */
  connection_name?: string;
  capability?: string;
  /** 只有「必须指名工具」的适配器（如 mcp_server）需要。 */
  tool?: string;
  step?: string;
  instruction?: string;
  /**
   * 透传给适配器的额外参数，`max_tokens` / `model` 走这里。
   * 这是让多 Agent 产出真东西的关键：/orchestrate 不透传 max_tokens，
   * 推理型模型光思考就吃光默认预算，拿回来的全是思考过程。
   */
  params_extra?: Record<string, unknown>;
}

export interface HubPipelinePayload {
  stages: HubPipelineStage[];
  timeout_seconds?: number;
  summarize?: boolean;
  summary_instruction?: string;
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  if (!res.ok) {
    const envelope = parsed as { error?: { code?: string; message?: string } } | null;
    const err = new Error(envelope?.error?.message ?? `请求失败（HTTP ${res.status}）`) as Error & {
      code?: string;
      status?: number;
    };
    err.code = envelope?.error?.code;
    err.status = res.status;
    throw err;
  }
  return parsed as T;
}

function writeHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const csrf = getCsrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;
  return headers;
}

async function send<T>(method: string, path: string, body?: unknown, query?: Record<string, string | number>): Promise<T> {
  const res = await fetch(buildApiUrl(path, query), {
    method,
    headers: writeHeaders(),
    credentials: 'same-origin',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return jsonOrThrow<T>(res);
}

const get = <T,>(path: string, query?: Record<string, string | number>) =>
  fetch(buildApiUrl(path, query), { credentials: 'same-origin' }).then((r) => jsonOrThrow<T>(r));

export const hubApi = {
  listPresets: () => get<{ presets: HubPreset[]; count: number }>(`${BASE}/presets`),

  manifestExample: () =>
    get<{ schema: Record<string, unknown>; example: Record<string, unknown> }>(
      `${BASE}/manifest/example`,
    ),

  importManifest: (payload: { text: string; filename?: string; name?: string; credentials?: Record<string, unknown> }) =>
    send<{ connection: HubConnection }>('POST', `${BASE}/manifest/import`, payload),

  listConnections: (query?: { kind?: string; group?: string }) =>
    get<{ connections: HubConnection[]; count: number }>(`${BASE}/connections`, query),

  createConnection: (payload: HubCreatePayload) =>
    send<{ connection: HubConnection }>('POST', `${BASE}/connections`, payload),

  getConnection: (id: string) => get<{ connection: HubConnection }>(`${BASE}/connections/${id}`),

  updateConnection: (id: string, payload: HubUpdatePayload) =>
    send<{ connection: HubConnection }>('PATCH', `${BASE}/connections/${id}`, payload),

  deleteConnection: (id: string) =>
    send<{ id: string; deleted: boolean }>('DELETE', `${BASE}/connections/${id}`),

  healthCheck: (id: string, timeoutSeconds = 3) =>
    send<{ connection: HubConnection; report: HubHealthReport }>(
      'POST',
      `${BASE}/connections/${id}/health-check`,
      undefined,
      { timeout_seconds: timeoutSeconds },
    ),

  healthCheckAll: (timeoutSeconds = 3) =>
    send<{
      results: { connection_id: string; report: HubHealthReport }[];
      count: number;
      healthy: number;
    }>('POST', `${BASE}/connections/health-check-all`, undefined, { timeout_seconds: timeoutSeconds }),

  /** 真实调用一次。失败也是 200：调用本身成功，目标没成功。 */
  invoke: (id: string, payload: { action?: string; params?: Record<string, unknown>; timeout_seconds?: number }) =>
    send<{ connection_id: string; action: string; result: HubInvokeResult }>(
      'POST',
      `${BASE}/connections/${id}/invoke`,
      payload,
    ),

  listCapabilities: (kind?: string) =>
    get<{ capabilities: HubCapabilityRow[]; count: number }>(
      `${BASE}/capabilities`,
      kind ? { kind } : undefined,
    ),

  registerCapability: (id: string, payload: HubCapability) =>
    send<{ connection_id: string; capabilities: HubCapability[] }>(
      'POST',
      `${BASE}/connections/${id}/capabilities`,
      payload,
    ),

  unregisterCapability: (id: string, name: string) =>
    send<{ connection_id: string; capabilities: HubCapability[] }>(
      'DELETE',
      `${BASE}/connections/${id}/capabilities/${encodeURIComponent(name)}`,
    ),

  /** 路由试算是只读计算，用 GET 语义不合适（带 hint 体），后端收 POST。 */
  route: (payload: { hint: string; top_k?: number; kind?: string; include_unhealthy?: boolean }) =>
    send<{ hint: string; candidates: HubRouteCandidate[]; count: number }>('POST', `${BASE}/route`, payload),

  // -- 编排 --------------------------------------------------------------- //
  // 三条约定：
  //  1. 编排失败（含某步失败）**也是 200**：HTTP 层成功，业务层失败，
  //     和 invoke 一样如实返回给页面，不抛。
  //  2. `mode` 不显式给时默认 sequential（与后端 pydantic 默认一致），
  //     但页面必须显式选，不依赖默认值。
  //  3. steps / aspects 按 mode 二选一 —— 后端只按 mode 取一边，
  //     这里不做自动合并，避免用户填了 aspects 却按 steps 跑。

  /** 前置体检：有没有可用 Agent。零 Agent 时页面应拦在跑之前。 */
  orchestrateCapabilities: () =>
    get<HubOrchestratePreflight>(`${BASE}/orchestrate/capabilities`),

  /** 自动派单：sequential / parallel。 */
  orchestrate: (payload: HubOrchestratePayload) =>
    send<{ orchestration: HubOrchestrationResult }>('POST', `${BASE}/orchestrate`, payload),

  /** 显式流水线：每步指名连接，可用 params_extra 单独配 max_tokens。 */
  orchestratePipeline: (payload: HubPipelinePayload) =>
    send<{ orchestration: HubOrchestrationResult }>('POST', `${BASE}/orchestrate/pipeline`, payload),
};
