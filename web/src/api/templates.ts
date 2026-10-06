// 开箱模板 API 客户端（P13 · A-开箱模板-02/03/05/07/08/09 前端面）
//
// 范式照 `kanban.ts`：类型从后端契约派生，**不在前端另抄一份枚举**。
// 三层供给 / 质量档位 / 八类必备项 / 七项配置的字面量全部由后端下发
// （GET /api/templates/schema、/layers、/quality-tiers），前端只消费。
import { request } from './client';

/** 模板 schema 版本。与后端 `scaffold.TEMPLATE_SCHEMA_VERSION` 对齐（消费用）。 */
export const TEMPLATE_SCHEMA_VERSION = '1.0.0';

export interface TemplateSchema {
  schema_version: string;
  entity: string;
  min_members: number;
  controller_id: string;
  controller_forbidden_rule: string;
  controller_duties: string[];
  scenarios: string[];
  layers: string[];
  quality_tiers: string[];
  config_items: string[];
  essential_keys: string[];
  required_top_level: string[];
}

export interface LayerInfo {
  id: string;
  label: string;
  hides_technical: boolean;
}

export interface LayerResponse {
  schema_version: string;
  layers: LayerInfo[];
  single_source: boolean;
  note: string;
}

export interface TierInfo {
  id: string;
  label: string;
}

export interface TierResponse {
  tiers: TierInfo[];
  default: string;
  paired_with_layers: boolean;
  note: string;
}

export interface UsageEstimate {
  /** 恒为 true：这是估算，不是账单。前端必须据此标注「预估」。 */
  estimated: boolean;
  basis: string;
  steps: number;
  member_count: number;
  approx_model_calls: number;
  approx_tokens: number;
  cost_note: string;
}

export interface OverviewMember {
  id: string;
  role: string;
  responsibilities: string[];
  tools: string[];
}

export interface SystemOverview {
  template_id: string;
  name: string;
  scenario: string;
  scenario_label: string;
  layer: string;
  quality_tier: string;
  controller: { id: string; role: string; duties: string[] };
  member_count: number;
  members: OverviewMember[];
  artifact_rule: Record<string, unknown>;
  estimate: UsageEstimate;
}

export interface TemplateCard {
  template_id: string;
  name: string;
  scenario: string;
  scenario_label: string;
  layer: string;
  quality_tier: string;
  summary: string;
  member_count: number;
  overview: SystemOverview;
}

export interface EssentialItem {
  key: string;
  label: string;
  value: unknown;
  explain: string;
  overridable: boolean;
  factory_value: unknown;
}

export interface ControllerSpec {
  id: string;
  role: string;
  duties: string[];
  forbidden_rules: string[];
  system_prompt: string;
}

export interface TemplateMember {
  id: string;
  role: string;
  system_prompt: string;
  responsibilities: string[];
  tool_allowlist: string[];
  imported?: boolean;
}

export interface TemplateDetail {
  schema_version: string;
  template_id: string;
  name: string;
  scenario: string;
  layer: string;
  quality_tier: string;
  summary?: string;
  controller: ControllerSpec;
  members: TemplateMember[];
  example_task: { goal: string; steps?: number; prompt?: string };
  overview: SystemOverview;
  essentials_view: EssentialItem[];
  /** 结构问题清单：非空即内容包不完整，界面必须逐条显示（需求 -03④）。 */
  problems: string[];
  controller_warnings: string[];
  _hidden_technical?: boolean;
}

export interface InstantiateResult {
  template_id: string;
  created_from: string;
  quality_tier: string;
  system: Record<string, unknown>;
  /** 缺失的必填项（e.g. `termination` / `member_prompt:drafter`）。空 = 可跑通。 */
  unresolved: string[];
  warnings: string[];
  runnable: boolean;
  message: string;
}

export interface ControllerCheckResult {
  template_id: string;
  warnings: string[];
  blocking: boolean;
  note: string;
}

export interface RestoreFactoryResult {
  template_id: string;
  quality_tier: string;
  controller_prompt: string;
  essentials: Record<string, { value: unknown; explain: string }>;
  restored_from: string;
}

export interface ExpandToCodeResult {
  filename: string;
  language: string;
  code: string;
  template_id: string;
  node_count: number;
  edge_count: number;
  verbs: string[];
  runtime_dirname: string;
  roundtrip_consistent: boolean;
  note: string;
}

export interface ExampleRunResult {
  template_id: string;
  quality_tier: string;
  example_task: { goal: string; steps?: number; prompt?: string };
  steps: number;
  /** 恒为 false：本响应只交付试跑规格，未执行任何模型调用。 */
  executed: boolean;
  note: string;
}

export interface ManualQualityResult {
  ok: boolean;
  fault_scenario_count: number;
  min_required: number;
  example_block_count: number;
  zero_basis_path: boolean;
  gaps: string[];
  checked_at?: string;
}

export interface InstantiateBody {
  name?: string;
  tier?: string;
  overrides?: Record<string, unknown>;
}

export function fetchSchema(): Promise<TemplateSchema> {
  return request<TemplateSchema>('/api/templates/schema');
}

export function fetchLayers(): Promise<LayerResponse> {
  return request<LayerResponse>('/api/templates/layers');
}

export function fetchQualityTiers(): Promise<TierResponse> {
  return request<TierResponse>('/api/templates/quality-tiers');
}

export function listTemplates(params: {
  scenario?: string;
  layer?: string;
  tier?: string;
} = {}): Promise<{ items: TemplateCard[]; total: number; quality_tier: string }> {
  return request('/api/templates', { query: { ...params } });
}

export function templateDetail(
  templateId: string,
  params: { tier?: string; layer?: string } = {},
): Promise<TemplateDetail> {
  return request<TemplateDetail>(`/api/templates/${encodeURIComponent(templateId)}`, {
    query: { ...params },
  });
}

export function instantiate(templateId: string, body: InstantiateBody = {}): Promise<InstantiateResult> {
  return request<InstantiateResult>(
    `/api/templates/${encodeURIComponent(templateId)}/instantiate`,
    { method: 'POST', body },
  );
}

export function controllerCheck(templateId: string, draftPrompt: string): Promise<ControllerCheckResult> {
  return request<ControllerCheckResult>(
    `/api/templates/${encodeURIComponent(templateId)}/controller-check`,
    { method: 'POST', body: { draft_prompt: draftPrompt } },
  );
}

export function restoreFactory(templateId: string, tier = 'novice'): Promise<RestoreFactoryResult> {
  return request<RestoreFactoryResult>(
    `/api/templates/${encodeURIComponent(templateId)}/restore-factory`,
    { method: 'POST', query: { tier } },
  );
}

export function expandToCode(templateId: string, tier = 'novice'): Promise<ExpandToCodeResult> {
  return request<ExpandToCodeResult>(
    `/api/templates/${encodeURIComponent(templateId)}/expand-to-code`,
    { method: 'POST', query: { tier } },
  );
}

export function importCode(body: {
  code: string;
  base_template_id?: string;
  name?: string;
}): Promise<{ template: TemplateDetail; written_to: string; source_template: string | null }> {
  return request('/api/templates/import-code', { method: 'POST', body });
}

export function exampleRun(templateId: string, tier = 'novice'): Promise<ExampleRunResult> {
  return request<ExampleRunResult>(
    `/api/templates/${encodeURIComponent(templateId)}/example-run`,
    { query: { tier } },
  );
}

export function manualQuality(markdown: string): Promise<ManualQualityResult> {
  return request<ManualQualityResult>('/api/templates/manual-quality', {
    method: 'POST',
    body: { markdown },
  });
}
