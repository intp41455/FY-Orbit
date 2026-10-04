/**
 * W4 · 模型接入 API 客户端（多 Provider 目录与健康探测）。
 *
 * 原则（FROZEN_CONTRACT §1 / §11，UI_BASELINE）：
 *  - 只调用真实后端 `/api/models/*`；不模拟成功、不造假的延迟数字。
 *  - 响应里永远不会出现密钥原文：只包含 `credential_ref` 与布尔状态。
 *  - 健康探测是真实往返；后端连不上时返回 ok=false 与脱敏错误原文。
 */
import { request } from './client';

const BASE = '/api/models';

export type ProviderId = 'openai_compat' | 'ollama' | 'anthropic';

export interface ProviderHealthInfo {
  provider_id: string;
  ok: boolean;
  latency_ms: number | null;
  models: string[];
  error: string;
  endpoint_ref: string;
}

export interface ModelProviderInfo {
  provider_id: string;
  name: string;
  requires_api_key: boolean;
  local_inference: boolean;
  default_base_url: string;
  health_path: string;
  configured: boolean;
  credential_configured: boolean;
  credential_ref: string;
  /** primary = 主通道；fallback = 已加入降级链；'' = 未启用 */
  role: 'primary' | 'fallback' | '';
  model: string;
  endpoint_ref: string;
  health: ProviderHealthInfo | null;
  note: string;
}

export interface ModelChainEntry {
  provider_id: string;
  name: string;
  model: string;
  source: 'primary' | 'fallback' | 'injected';
  local_inference: boolean;
  endpoint_ref: string;
}

export interface ModelCatalogSummary {
  catalog_version: string;
  primary_provider_id: string;
  model_name: string;
  configured: boolean;
  config_error: string;
  route_errors: string[];
  chain: ModelChainEntry[];
  providers: ModelProviderInfo[];
  models: Record<string, unknown>[];
}

export interface HealthCheckReport {
  checked_at: string;
  primary_provider_id: string;
  config_error: string;
  probe_timeout_seconds: number;
  checks: ProviderHealthInfo[];
  hint: string;
}

export interface HealthCheckPayload {
  providers?: string[];
  timeout_seconds?: number;
}

export const modelsApi = {
  /** provider/模型/配置状态/健康态。probe=false 时后端不做任何出站连接。 */
  catalog: (probe = true, timeoutSeconds = 2.5) =>
    request<ModelCatalogSummary>(`${BASE}/catalog`, {
      query: { probe: probe ? 'true' : 'false', timeout_seconds: String(timeoutSeconds) },
    }),
  /** 真实探测：成功返回毫秒延迟，失败返回脱敏错误原文。 */
  healthCheck: (body: HealthCheckPayload = {}) =>
    request<HealthCheckReport>(`${BASE}/health-check`, { method: 'POST', body }),
};
