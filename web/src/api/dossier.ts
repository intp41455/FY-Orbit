// 任务档案库与企业模式 API 客户端（P15 · A-三重模式-03 / A-上下文持久化-02/03）
//
// 范式照 `templates.ts`：类型从后端契约派生，不另抄枚举。
// 诚实字段原样透传：`briefing.truncated`（截断必须让用户看见）、
// `adapt.unmapped`（没映射的字段一个都不少）、`adapt.executed`（恒 false）。
import { request } from './client';

export interface ArchiveObjective {
  goal: string;
  domain: string;
  mode: string;
  strategy: string;
  status: string;
  stage: string;
  progress_percent: number | null;
  weight: number;
  critical: boolean;
  deadline: string | null;
  created_at: string | null;
  updated_at: string | null;
  blocked_reason: string | null;
  blocked_since: string | null;
  planned_start: string | null;
  planned_end: string | null;
}

export interface ArchiveMember {
  instance_id: string;
  role: string;
  title: string;
  state: string;
  requested_model: string;
  effective_model: string | null;
  steps: number;
  blocked_reason: string;
  current_goal: string;
}

export interface ArchiveDecision {
  seq: number;
  action: string;
  target: string | null;
  details: Record<string, unknown>;
  at: string | null;
}

export interface ChangeLogEntry {
  kind: string;
  from_status: string | null;
  to_status: string | null;
  detail: Record<string, unknown>;
  at: string | null;
}

export interface TaskArchive {
  task_id: string;
  objective: ArchiveObjective;
  milestones: ChangeLogEntry[];
  roster: {
    teams: { team_id: string; name: string; mode: string; state: string; coordinator_role: string }[];
    members: ArchiveMember[];
  };
  decisions: ArchiveDecision[];
  artifacts: {
    version: number;
    status: string;
    checkpoint_ref: string | null;
    started_at: string | null;
    ended_at: string | null;
  }[];
  dependencies: { blocked_by: string[]; blocks: string[]; subtasks: string[] };
  change_log: ChangeLogEntry[];
  failure: Record<string, unknown> | null;
  notes: string[];
  sources: Record<string, string>;
  generated_at: string;
}

export interface BriefingResult {
  task_id: string;
  briefing: string;
  chars: number;
  limit: number;
  truncated: boolean;
  archive_endpoint: string;
  note: string;
}

export interface RetrospectiveResult {
  task_id: string;
  report: string;
  timeline: ChangeLogEntry[];
  decisions: ArchiveDecision[];
  metrics: {
    change_events: number;
    decision_count: number;
    member_count: number;
    team_count: number;
    attempt_versions: number;
    span_minutes: number | null;
  };
  lessons: { kind: string; text: string }[];
  generated_at: string;
}

export interface DistillResult {
  knowledge_id: string;
  title: string;
  path: string;
  source_task_id: string;
  lessons: { kind: string; text: string }[];
  report: string;
}

export interface KnowledgeItem {
  knowledge_id: string;
  title: string;
  source_task_id: string;
  tags: string[];
  created_at: string;
}

export interface AdapterMapping {
  from: string;
  to: string;
  via?: string;
}

export interface EnterpriseAdapterInfo {
  target: string;
  label: string;
  mappings: AdapterMapping[];
  notes: string[];
}

export interface EnterpriseCatalog {
  spec_version: string;
  adapters: EnterpriseAdapterInfo[];
  governance: Record<string, unknown>;
  custom_mappings: string[];
}

export interface AdaptResult {
  target: string;
  spec: Record<string, unknown>;
  mapped: AdapterMapping[];
  /** 没被映射到的外部字段——一个都不少，全部列在这里。 */
  unmapped: string[];
  warnings: string[];
  executed: boolean;
  note: string;
}

export function fetchArchive(taskId: string): Promise<TaskArchive> {
  return request<TaskArchive>(`/api/dossier/tasks/${encodeURIComponent(taskId)}/archive`);
}

export function fetchBriefing(taskId: string, limit = 4000): Promise<BriefingResult> {
  return request<BriefingResult>(
    `/api/dossier/tasks/${encodeURIComponent(taskId)}/briefing`,
    { query: { limit } },
  );
}

export function fetchRetrospective(taskId: string): Promise<RetrospectiveResult> {
  return request<RetrospectiveResult>(
    `/api/dossier/tasks/${encodeURIComponent(taskId)}/retrospective`,
  );
}

export function distill(taskId: string, body: { name?: string; tags?: string[] } = {}): Promise<DistillResult> {
  return request<DistillResult>(
    `/api/dossier/tasks/${encodeURIComponent(taskId)}/distill`,
    { method: 'POST', body },
  );
}

export function listKnowledge(): Promise<{ items: KnowledgeItem[]; total: number }> {
  return request('/api/dossier/knowledge');
}

export function knowledgeDetail(knowledgeId: string): Promise<{
  knowledge_id: string;
  meta: Record<string, unknown>;
  markdown: string;
}> {
  return request(`/api/dossier/knowledge/${encodeURIComponent(knowledgeId)}`);
}

export function enterpriseCatalog(): Promise<EnterpriseCatalog> {
  return request<EnterpriseCatalog>('/api/dossier/enterprise');
}

export function enterpriseAdapt(
  target: string,
  external: Record<string, unknown>,
): Promise<AdaptResult> {
  return request<AdaptResult>(
    `/api/dossier/enterprise/${encodeURIComponent(target)}/adapt`,
    { method: 'POST', body: { external } },
  );
}

export function enterpriseOnboarding(target: string): Promise<{
  target: string;
  markdown: string;
  mappings: AdapterMapping[];
  governance: Record<string, unknown>;
}> {
  return request(`/api/dossier/enterprise/${encodeURIComponent(target)}/onboarding`);
}

export function registerMapping(
  target: string,
  mappings: AdapterMapping[],
): Promise<{ target: string; registered: number; total_mappings: number }> {
  return request(`/api/dossier/enterprise/${encodeURIComponent(target)}/mappings`, {
    method: 'POST',
    body: { mappings: mappings.map((m) => ({ from: m.from, to: m.to, via: m.via ?? 'identity' })) },
  });
}
