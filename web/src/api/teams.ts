/**
 * 19 号团队能力 API 客户端。
 *
 * 只调用真实后端；不模拟成功、不复制演示数据。
 * 凭据字段一律是 `credential_ref` + `credential_configured` 布尔值，
 * 响应中不会出现任何密钥内容（19 §5）。
 */
import { request } from './client';

const BASE = '/api/teams';

export type TeamMode = 'system_managed' | 'product_native';
export type MemberState =
  | 'draft' | 'starting' | 'running' | 'blocked' | 'paused'
  | 'waiting_rework' | 'completed' | 'failed' | 'cancelled'
  | 'unknown_needs_reconciliation';
export type CapabilityState = 'verified' | 'unsupported' | 'unknown';

export interface HostCapability {
  agent_host: string;
  provider_id: string;
  capabilities: Record<string, CapabilityState>;
  supports_per_member_model: boolean;
  usage_metering: CapabilityState;
  probe_source: string;
  reason: string;
  disabled_operations: string[];
}

export interface ModelOption {
  provider_id: string;
  model_id: string;
  credential_configured: boolean;
  credential_ref: string;
  pricing_status: string;
  context_window: number;
  synthetic: boolean;
  supports_tools: boolean;
  supports_structured_output: boolean;
  supports_streaming: boolean;
  max_output_tokens: number;
  endpoint_ref: string;
  note: string;
}

export interface ProviderSummary {
  provider_id: string;
  name: string;
  credential_configured: boolean;
  credential_ref: string;
  synthetic: boolean;
  endpoint_ref: string;
  note: string;
  models: string[];
}

export interface TeamCatalog {
  templates: { id: string; name: string; mode: TeamMode; members: string[] }[];
  hosts: HostCapability[];
  catalog_version: string;
  providers: ProviderSummary[];
  models: ModelOption[];
  real_model_configured: boolean;
}

export interface TeamMemberSpec {
  role: string;
  title?: string;
  agent_host?: string;
  provider_id?: string;
  model_id?: string;
  depends_on?: string[];
  goal?: string;
}

export interface TeamSummary {
  id: string;
  name: string;
  mode: TeamMode;
  state: string;
  version: number;
  plan_version: number;
  root_task_id: string | null;
  member_roles: string[];
}

export interface BindingView {
  role: string;
  requested_model: string;
  requested_provider: string;
  inherited_from: string;
  chain: { scope: string; model_id: string }[];
  params: Record<string, unknown>;
}

export interface TeamMemberView {
  agent_instance_id: string;
  role: string;
  title: string;
  agent_host: string;
  provider_id: string;
  session_id: string;
  independent_session: boolean;
  state: MemberState;
  blocked_reason: string;
  requested_model: string;
  /** '未执行' until a batch actually runs; never a guessed version. */
  effective_model: string;
  effective_confidence: string;
  inherited_from: string;
  credential_ref: string;
  credential_configured: boolean;
  depends_on: string[];
  run_batch: number;
  current_goal: string;
  plan_version: number;
  subtask_id: string | null;
  budget_reserved_usd: number;
  budget_spent_usd: number;
  version: number;
  control: { disabled_operations: string[]; supports_per_member_model: boolean };
  capability_recorded: boolean;
  updated_at: string | null;
}

export interface TeamEventItem {
  seq: number;
  event_type: string;
  task_id: string | null;
  agent_instance_id: string | null;
  run_batch: number | null;
  source: string;
  details: Record<string, unknown>;
  evidence_refs: string[];
  created_at: string;
}

export interface ValidationBlocker {
  code: string;
  role?: string;
  message: string;
}

export interface ValidationReport {
  team_id: string;
  version: number;
  state: string;
  can_start: boolean;
  blockers: ValidationBlocker[];
  warnings: ValidationBlocker[];
  real_model_configured: boolean;
}

export interface TeamSnapshot {
  team: {
    id: string;
    name: string;
    mode: TeamMode;
    state: string;
    version: number;
    plan_version: number;
    root_task_id: string | null;
    canvas_instance_id: string | null;
    coordinator_role: string;
    default_binding: Record<string, unknown>;
    budget_ref: Record<string, unknown>;
    permission_ref: Record<string, unknown>;
    members: TeamMemberSpec[];
    last_change_reason: string;
    last_changed_by: string;
    real_model_configured: boolean;
    started_at: string | null;
  };
  members: TeamMemberView[];
  events: TeamEventItem[];
  validation: ValidationReport;
}

export interface ControlResult {
  id: string;
  operation: string;
  state: string;
  idempotent_replay: boolean;
  result: Record<string, unknown>;
}

function key(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const teamsApi = {
  catalog: () => request<TeamCatalog>(`${BASE}/catalog`),

  list: () => request<{ items: TeamSummary[]; count: number }>(`${BASE}`),

  create: (body: {
    name: string;
    mode?: TeamMode;
    template_id?: string | null;
    members?: TeamMemberSpec[] | null;
    default_binding?: Record<string, unknown> | null;
    budget_ref?: Record<string, unknown> | null;
    permission_ref?: Record<string, unknown> | null;
    reason?: string;
  }) => request<TeamSnapshot>(`${BASE}`, { method: 'POST', body }),

  get: (teamId: string) => request<TeamSnapshot>(`${BASE}/${teamId}`),

  validate: (teamId: string) => request<ValidationReport>(`${BASE}/${teamId}/validation`),

  start: (teamId: string, expectedVersion: number) =>
    request<TeamSnapshot>(`${BASE}/${teamId}/start`, {
      method: 'POST',
      body: { expected_version: expectedVersion },
    }),

  update: (teamId: string, expectedVersion: number, patch: Record<string, unknown>, reason: string) =>
    request<TeamSnapshot>(`${BASE}/${teamId}`, {
      method: 'PATCH',
      body: { expected_version: expectedVersion, patch, reason },
    }),

  resolveBinding: (teamId: string, role: string) =>
    request<BindingView>(
      `${BASE}/${teamId}/members/${encodeURIComponent(role)}/binding`,
    ),

  setMemberBinding: (
    teamId: string,
    role: string,
    body: { provider_id: string; model_id: string; reason?: string },
  ) =>
    request<BindingView>(`${BASE}/${teamId}/members/${encodeURIComponent(role)}/binding`, {
      method: 'PUT',
      body,
    }),

  setRoleBinding: (teamId: string, role: string, modelId: string) =>
    request<BindingView>(`${BASE}/${teamId}/roles/${encodeURIComponent(role)}/binding`, {
      method: 'PUT',
      body: { model_id: modelId },
    }),

  updateGoal: (teamId: string, goal: string, reason: string) =>
    request<{ plan_version: number; affected_members: string[] }>(`${BASE}/${teamId}/goal`, {
      method: 'POST',
      body: { goal, reason },
    }),

  control: (
    teamId: string,
    body: {
      operation: string;
      role?: string;
      agent_instance_id?: string;
      expected_version?: number;
      scope?: Record<string, unknown>;
      reason?: string;
      idempotency_key?: string;
    },
  ) =>
    request<ControlResult>(`${BASE}/${teamId}/control`, {
      method: 'POST',
      body: { idempotency_key: key(), ...body },
    }),

  reserveBudget: (teamId: string, role: string, amountUsd: number) =>
    request<{ reservation_id: string; amount_usd: number; root_task_id: string }>(
      `${BASE}/${teamId}/budget/reserve`,
      { method: 'POST', body: { role, amount_usd: amountUsd } },
    ),

  reportResult: (teamId: string, body: { role: string; run_batch: number; output?: string }) =>
    request<{ role: string; state: string; run_batch: number }>(
      `${BASE}/${teamId}/members/result`,
      { method: 'POST', body },
    ),

  executeMember: (
    teamId: string,
    body: { role: string; prompt: string; max_tokens?: number },
  ) =>
    request<{
      role: string;
      session_id: string;
      text: string;
      requested_model: string;
      effective_model: string;
      effective_confidence: string;
      usage: Record<string, number>;
      settled_usd: string;
      run_batch: number;
    }>(`${BASE}/${teamId}/members/execute`, { method: 'POST', body }),

  events: (teamId: string, cursor = 0) =>
    request<{ items: TeamEventItem[]; count: number; next_cursor: number }>(
      `${BASE}/${teamId}/events`,
      { query: { cursor } },
    ),
};