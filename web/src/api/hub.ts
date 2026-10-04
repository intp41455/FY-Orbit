// Typed client for the W6 超级中台适配器中心 API (mirrors api/routes/hub.py).
//
// Honesty contract (FROZEN_CONTRACT §11 / 交接总纲铁律 3):
//  - 探活/调用失败由后端返回 200 + {ok:false, error/detail}，页面必须原样展示，
//    绝不把失败渲染成「已连接」；
//  - 凭证永远拿不到明文：后端 public_connection 只回掩码，本层不做任何拼接；
//  - 预置里 implemented=false 的条目必须显示「内置骨架 · 未接入」，不能当可用项。
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
};
