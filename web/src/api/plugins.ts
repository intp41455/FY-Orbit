// P12 · 插件与模板市场 API 客户端（A-工具市场-03 · A-开箱模板-04 · A-开箱模板-06）。
import { request } from './client';

/** 市场评分摘要。 */
export interface RatingSummary {
  average_rating: number;
  rating_count: number;
  score: number;
  distribution?: Record<string, number>;
}

export interface UserRating {
  id: string;
  actor_id: string;
  rating: number;
  comment?: string | null;
  updated_at: string;
}

/** 服务端扫描摘要（来自落库 scan_report，非前端计算）。 */
export interface PluginScanSummary {
  passed: boolean;
  risk_level: string;
  finding_count: number;
  scanner_version?: string | null;
  /** 仅详情接口返回完整 findings。 */
  findings?: { code: string; severity: string; location: string; message: string }[];
}

/** 市场包卡片：风险等级与扫描摘要是**必须展示**项（不得隐藏）。 */
export interface PluginCard {
  skill_id: string;
  name: string;
  version: string;
  domain: string;
  source: string;
  license: string;
  package_hash: string;
  gate_profile: 'plugin' | 'instruction';
  signature_verified: boolean;
  capabilities: string[];
  risk_level: 'low' | 'medium' | 'high';
  risk_reasons: string[];
  scan: PluginScanSummary;
  rating?: RatingSummary;
  my_rating?: UserRating | null;
}

export interface PluginListResponse {
  items: PluginCard[];
  total: number;
  limit: number;
  offset: number;
  sort_by?: string;
}

export interface PluginListParams {
  query?: string;
  domain?: string;
  capability?: string;
  sortBy?: string;
  limit?: number;
  offset?: number;
}

/** 模板卡片。 */
export interface TemplateMarketCard {
  template_id: string;
  name: string;
  scenario: string;
  scenario_label?: string;
  summary: string;
  quality_tier: string;
  member_count: number;
  is_factory: boolean;
  rating: RatingSummary;
}

export interface TemplateHierarchyResponse {
  schema_version: string;
  single_source: boolean;
  layers: Array<{
    id: 'novice_default' | 'advanced_swappable' | 'technical_removable';
    label: string;
    items?: Array<{
      template_id: string;
      name: string;
      scenario: string;
      scenario_label?: string;
      summary: string;
      complexity_hidden: boolean;
      example_task?: { goal: string; prompt: string };
    }>;
    scenarios?: Array<{
      id: string;
      label: string;
      templates: Array<{ template_id: string; name: string; summary: string; member_count: number }>;
    }>;
    portal?: {
      first_class_entry: boolean;
      description: string;
      supported_verbs: string[];
      features: Array<{ id: string; label: string }>;
    };
  }>;
}

export interface SecurityReviewReport {
  passed: boolean;
  risk_level: 'low' | 'medium' | 'high';
  checksum_verified: boolean;
  can_import: boolean;
  requested_tools: string[];
  dangerous_tools: string[];
  sensitive_tools: string[];
  findings: Array<{ severity: string; code: string; message: string }>;
  requires_user_confirmation: boolean;
}

export const pluginsApi = {
  list: (params: PluginListParams = {}) => {
    const qs = new URLSearchParams();
    if (params.query) qs.set('query', params.query);
    if (params.domain) qs.set('domain', params.domain);
    if (params.capability) qs.set('capability', params.capability);
    if (params.sortBy) qs.set('sort_by', params.sortBy);
    if (params.limit !== undefined) qs.set('limit', String(params.limit));
    if (params.offset !== undefined) qs.set('offset', String(params.offset));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<PluginListResponse>(`/api/plugins/marketplace${suffix}`);
  },
  get: (skillId: string) =>
    request<PluginCard>(`/api/plugins/marketplace/${encodeURIComponent(skillId)}`),
  /** 上架（复用 promote 门禁；UI 市场页一般不直接用，保留契约完整性）。 */
  publish: (skillId: string, evaluationId: string) =>
    request<{ skill_id: string; state: string }>(
      `/api/plugins/marketplace/${encodeURIComponent(skillId)}/publish`,
      { method: 'POST', body: { evaluation_id: evaluationId } },
    ),
  /** 安装。 */
  install: (skillId: string, grantId?: string | null) =>
    request<PluginCard & { installed: boolean; grant_id: string | null }>(
      `/api/plugins/marketplace/${encodeURIComponent(skillId)}/install`,
      { method: 'POST', body: { grant_id: grantId ?? null } },
    ),
  /** 插件评分。 */
  rate: (skillId: string, rating: number, comment?: string) =>
    request<{ summary: RatingSummary; rating: number; comment?: string }>(
      `/api/plugins/marketplace/${encodeURIComponent(skillId)}/rate`,
      { method: 'POST', body: { rating, comment } },
    ),
  /** 获取插件评分列表与分布。 */
  getRatings: (skillId: string, limit = 20, offset = 0) =>
    request<{ summary: RatingSummary; items: UserRating[]; total: number }>(
      `/api/plugins/marketplace/${encodeURIComponent(skillId)}/ratings?limit=${limit}&offset=${offset}`,
    ),

  // --- 模板分层与市场 ---
  getHierarchy: () => request<TemplateHierarchyResponse>('/api/plugins/templates/hierarchy'),
  listTemplates: (params: { query?: string; scenario?: string; layer?: string; sortBy?: string; limit?: number; offset?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.query) qs.set('query', params.query);
    if (params.scenario) qs.set('scenario', params.scenario);
    if (params.layer) qs.set('layer', params.layer);
    if (params.sortBy) qs.set('sort_by', params.sortBy);
    if (params.limit !== undefined) qs.set('limit', String(params.limit));
    if (params.offset !== undefined) qs.set('offset', String(params.offset));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<{ items: TemplateMarketCard[]; total: number }>(`/api/plugins/templates/market${suffix}`);
  },
  previewSwitchImpact: (fromId: string, toId: string) =>
    request<{
      safe_to_switch: boolean;
      snapshot_action: string;
      warning: string;
      member_changes: { added: string[]; removed: string[]; retained: string[] };
      tool_changes: { new_tools: string[]; dropped_tools: string[] };
    }>('/api/plugins/templates/impact-preview', {
      method: 'POST',
      body: { from_template_id: fromId, to_template_id: toId },
    }),
  exportTemplate: (templateId: string) =>
    request<Record<string, unknown>>(`/api/plugins/templates/${encodeURIComponent(templateId)}/export`),
  securityReview: (pkg: unknown) =>
    request<SecurityReviewReport>('/api/plugins/templates/security-review', {
      method: 'POST',
      body: pkg,
    }),
  importTemplate: (pkg: unknown, confirmedTools?: string[], customName?: string) =>
    request<{ imported: boolean; template_id: string; name: string }>(
      '/api/plugins/templates/import',
      {
        method: 'POST',
        body: { package: pkg, confirmed_tools: confirmedTools, custom_name: customName },
      },
    ),
  rateTemplate: (templateId: string, rating: number, comment?: string) =>
    request<{ summary: RatingSummary; rating: number }>(
      `/api/plugins/templates/${encodeURIComponent(templateId)}/rate`,
      { method: 'POST', body: { rating, comment } },
    ),
};
