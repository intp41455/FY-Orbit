// P6 · 插件市场 API 客户端（与后端 P5 契约逐字对齐，见
// src/find_yourself/services/marketplace.py 与 routes/plugins.py）。
import { request } from './client';

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
}

export interface PluginListResponse {
  items: PluginCard[];
  total: number;
  limit: number;
  offset: number;
}

export interface PluginListParams {
  query?: string;
  domain?: string;
  capability?: string;
  limit?: number;
  offset?: number;
}

export const pluginsApi = {
  list: (params: PluginListParams = {}) => {
    const qs = new URLSearchParams();
    if (params.query) qs.set('query', params.query);
    if (params.domain) qs.set('domain', params.domain);
    if (params.capability) qs.set('capability', params.capability);
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
  /**
   * 安装。plugin 包（会物化文件执行）必须出示 grant_id（授权由
   * GrantService 创建）；缺失时后端 422 install_grant_required。
   */
  install: (skillId: string, grantId?: string | null) =>
    request<PluginCard & { installed: boolean; grant_id: string | null }>(
      `/api/plugins/marketplace/${encodeURIComponent(skillId)}/install`,
      { method: 'POST', body: { grant_id: grantId ?? null } },
    ),
};
