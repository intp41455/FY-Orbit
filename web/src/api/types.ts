// Typed data models for the Find Yourself Web client.
// Mirrors FROZEN_CONTRACT v1.0 (§1, §3, §5, §6, §11).
// These are the shapes the real backend returns/accepts. The frontend never
// invents its own success state; every value here comes from the API.

// ---- Unified error envelope (§1) ----
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}
export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody | null;
  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    super(body?.message ?? fallback);
    this.status = status;
    this.body = body;
  }
  get isUnauthorized(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

// ---- Identity & security (§5.1) ----
export interface OwnerIdentity {
  sub: string;
  // Display name is optional; backend decides what to reveal.
  name?: string;
  authenticated: true;
}
// Real backend /auth/me shape (observed on loopback): subject_type=owner when
// authenticated; csrf_token must be echoed back on same-origin writes.
export interface MeResponse {
  subject_type: 'owner' | 'service' | '';
  owner_id: string;
  service_id: string;
  service_kind: string;
  csrf_token: string;
}
export interface LoginRedirectResponse {
  redirect_url: string;
}
export interface DevTokenRequest {
  // Strict schema: only `token`. Owner defaults server-side.
  token: string;
}
export interface DevTokenResponse {
  status: string;
  owner_id: string;
  csrf_token: string;
}

// ---- Conversations & messages (§5.2) ----
export type ConversationMode = 'listen' | 'explore' | 'research' | 'engineering' | 'creative';

export interface ConversationSummary {
  id: string;
  title: string;
  domain: string;
  mode: ConversationMode;
  version: number;
  created_at: string; // timezone-aware UTC
  updated_at: string;
  deleted_at?: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  message_count: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  source: string;
  model?: string | null;
  client_message_id?: string | null;
  created_at: string;
  version: number;
  deleted_at?: string | null;
}

export interface PostMessageInput {
  content: string;
  client_message_id: string; // client-generated idempotency key
  mode?: ConversationMode;
}

// ---- Tasks & SSE (§5.2) ----
export type TaskState =
  | 'pending'
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export interface TaskSummary {
  id: string;
  root_id: string | null;
  parent_id: string | null;
  goal: string;
  mode: ConversationMode;
  state: TaskState;
  stage: string;
  depth: number;
  steps: number;
  max_steps: number;
  result?: unknown;
  failure?: string | null;
  deadline?: string | null;
  idempotency_key: string;
  created_at: string;
  updated_at: string;
}

export interface CreateTaskInput {
  goal: string;
  mode?: ConversationMode;
  conversation_id?: string;
  idempotency_key: string;
}

// SSE event types (§5.2: only stage, refs, status, redacted artifacts).
export type TaskEvent =
  | { type: 'stage'; task_id: string; stage: string; at: string }
  | { type: 'status'; task_id: string; state: TaskState; at: string }
  | { type: 'artifact'; task_id: string; artifact_id: string; media_type: string; size: number; at: string }
  | { type: 'error'; task_id: string; code: string; message: string; at: string }
  | { type: 'done'; task_id: string; at: string };

// ---- Memory search (§5.3, §8) ----
export interface MemoryHit {
  record_id: string;
  version: number;
  kind: string;
  category: string;
  snippet: string;
  domain: string;
  sources: Array<{ source_id: string; source_kind: string }>;
  created_at: string;
  evidence_boundary: string; // e.g. "assumption", "fact", "hypothesis"
}
export interface MemorySearchResponse {
  hits: MemoryHit[];
  cursor: string | null;
}

// ---- Proposals & approval (§5.3, §6) ----
// External-side-effect proposal status machine (§6.2).
export type ProposalStatus =
  | 'pending'
  | 'approved_pending_execution'
  | 'executing'
  | 'executed'
  | 'failed'
  | 'unknown'
  | 'rejected'
  | 'expired';

export type ProposalOperation = 'task.merge' | 'task.release' | 'memory.derive' | 'skill.promote' | 'agent.register' | string;

export interface ProposalDigestInput {
  operation: ProposalOperation;
  target_id: string;
  expected_version: number;
  payload: Record<string, unknown>;
  reason: string;
  rollback: string;
  expires_at: string;
}

export interface Proposal {
  id: string;
  operation: ProposalOperation;
  target_id: string;
  expected_version: number;
  payload: Record<string, unknown>;
  reason: string;
  rollback: string;
  digest: string; // sha256 hex
  status: ProposalStatus;
  expires_at: string;
  decided_at: string | null;
  execution_id: string | null;
  version: number;
  created_at: string;
  // Bindings for merge/release (§6.3) — only present when relevant.
  evidence_ids: string[];
  artifact_ids: string[];
  environment?: string;
  cost_estimate?: { amount: string; currency: string } | null;
  out_bound?: { domains: string[] } | null;
}

export interface ProposalDecisionInput {
  decision: 'approve' | 'reject';
  digest: string; // client must re-submit the digest it reviewed
  expected_version: number;
}

// ---- Assessments (§5.3, §11.3) ----
export type AssessmentKind = 'big5' | 'four_dim' | 'enneagram' | 'custom';

export interface AssessmentItem {
  id: string;
  text: string;
  reverse_scored: boolean;
}
export interface AssessmentCatalogEntry {
  assessment_id: string;
  kind: AssessmentKind;
  title: string;
  version: string; // questionnaire version bound to scoring
  description: string;
  items: AssessmentItem[];
  // Compliance notice shown verbatim in the UI (§11.3).
  compliance_notice: string;
  scale_min: number;
  scale_max: number;
}
export interface AssessmentCatalogResponse {
  assessments: AssessmentCatalogEntry[];
}

export interface AssessmentSession {
  id: string;
  assessment_id: string;
  questionnaire_version: string;
  started_at: string;
  submitted_at: string | null;
  // Per-item raw responses; missing means unanswered (never defaulted).
  answers: Record<string, number>;
  completed: boolean;
  result: AssessmentResult | null;
}
export interface AssessmentResult {
  // Server-computed. Never fabricated client-side.
  scales: Record<string, number>;
  type_label?: string;
  interpretation: string;
  caveat: string; // compliance / evidence boundary wording
  norm_note: string; // e.g. "no population norm; no percentile"
}
export interface SubmitAssessmentInput {
  answers: Record<string, number>;
}

// ---- Agents & skills (§5.3) ----
export type AgentLifecycle = 'candidate' | 'enabled' | 'draining' | 'offline';
export interface AgentInfo {
  name: string;
  version: string;
  capabilities: string[];
  domain: string;
  state: AgentLifecycle;
  healthy: boolean;
  concurrency: number;
}
export type SkillState = 'candidate' | 'enabled' | 'disabled' | 'rolled_back';
export interface SkillInfo {
  name: string;
  version: string;
  package_hash: string;
  domain: string;
  state: SkillState;
  source: string;
  license: string;
  // Script packages require isolated sandbox; UI must surface this (BUG-07).
  requires_isolation: boolean;
}

// ---- Artifacts (§5.3) ----
export interface ArtifactInfo {
  id: string;
  task_id: string;
  domain: string;
  sha256: string;
  size: number;
  media_type: string;
  verified: boolean;
  verifier: string | null;
}

// ---- Export (§5.3) ----
export interface ExportRequest {
  scope: 'all' | 'conversations' | 'memories';
}
export interface ExportResponse {
  export_id: string;
  // Short-lived, authenticated download; never cached by SW.
  status: 'pending' | 'ready' | 'failed';
  created_at: string;
}

// ---- Settings / data ----
export interface SettingsSummary {
  model_configured: boolean;
  oidc_configured: boolean;
  local_dev_token_allowed: boolean;
  data_domains: string[];
  export_status: ExportResponse | null;
}
