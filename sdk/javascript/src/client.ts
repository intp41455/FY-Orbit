/** FindYourself 标准客户端（JavaScript/TypeScript）。 */

import { generateIframeEmbedUrl, type EmbedOptions } from './embed.js';
import type {
  ContextAssembly,
  ContextSlice,
  PluginCard,
  RatingSummary,
  SecurityReviewReport,
  TemplateCard,
} from './types.js';

export interface ClientConfig {
  baseUrl?: string;
  token?: string;
  csrfToken?: string;
  tenantId?: string;
}

export class FindYourselfClient {
  private baseUrl: string;
  private token?: string;
  private csrfToken?: string;
  private tenantId?: string;

  constructor(config: ClientConfig = {}) {
    this.baseUrl = (config.baseUrl || 'http://localhost:8000').replace(/\/+$/, '');
    this.token = config.token;
    this.csrfToken = config.csrfToken;
    this.tenantId = config.tenantId;
  }

  private async request<T>(
    method: string,
    path: string,
    options?: { query?: Record<string, string | number | undefined>; body?: unknown; csrf?: boolean },
  ): Promise<T> {
    const qs = new URLSearchParams();
    if (options?.query) {
      for (const [k, v] of Object.entries(options.query)) {
        if (v !== undefined) qs.set(k, String(v));
      }
    }
    const url = `${this.baseUrl}${path}${qs.toString() ? `?${qs.toString()}` : ''}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    };
    if (this.token) headers['Authorization'] = `Bearer ${this.token}`;
    if (options?.csrf && this.csrfToken) headers['X-CSRF-Token'] = this.csrfToken;
    if (this.tenantId) headers['X-Tenant-Id'] = this.tenantId;

    const resp = await fetch(url, {
      method,
      headers,
      body: options?.body ? JSON.stringify(options.body) : undefined,
    });

    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const errJson = await resp.json();
        detail = errJson.detail || detail;
      } catch {
        // fallback
      }
      throw new Error(`HTTP ${resp.status} Error: ${detail}`);
    }

    return resp.json() as Promise<T>;
  }

  // -------------------------------------------------------------------------
  // 插件市场
  // -------------------------------------------------------------------------

  async listPlugins(params: { query?: string; sortBy?: string; limit?: number; offset?: number } = {}) {
    return this.request<{ items: PluginCard[]; total: number }>('GET', '/api/plugins/marketplace', {
      query: { query: params.query, sort_by: params.sortBy ?? 'score', limit: params.limit, offset: params.offset },
    });
  }

  async getPlugin(skillId: string) {
    return this.request<PluginCard>('GET', `/api/plugins/marketplace/${encodeURIComponent(skillId)}`);
  }

  async ratePlugin(skillId: string, rating: number, comment?: string) {
    return this.request<{ summary: RatingSummary }>('POST', `/api/plugins/marketplace/${encodeURIComponent(skillId)}/rate`, {
      body: { rating, comment },
      csrf: true,
    });
  }

  // -------------------------------------------------------------------------
  // 模板体系与市场
  // -------------------------------------------------------------------------

  async getTemplateHierarchy() {
    return this.request<{ layers: Array<{ id: string; label: string; [k: string]: unknown }> }>(
      'GET',
      '/api/plugins/templates/hierarchy',
    );
  }

  async listTemplates(params: { query?: string; scenario?: string; layer?: string; sortBy?: string; limit?: number; offset?: number } = {}) {
    return this.request<{ items: TemplateCard[]; total: number }>('GET', '/api/plugins/templates/market', {
      query: {
        query: params.query,
        scenario: params.scenario,
        layer: params.layer,
        sort_by: params.sortBy ?? 'score',
        limit: params.limit,
        offset: params.offset,
      },
    });
  }

  async previewSwitchImpact(fromTemplateId: string, toTemplateId: string) {
    return this.request<Record<string, unknown>>('POST', '/api/plugins/templates/impact-preview', {
      body: { from_template_id: fromTemplateId, to_template_id: toTemplateId },
    });
  }

  async exportTemplate(templateId: string) {
    return this.request<Record<string, unknown>>('GET', `/api/plugins/templates/${encodeURIComponent(templateId)}/export`);
  }

  async securityReviewTemplate(packageDoc: unknown) {
    return this.request<SecurityReviewReport>('POST', '/api/plugins/templates/security-review', {
      body: packageDoc,
    });
  }

  async importTemplate(packageDoc: unknown, confirmedTools?: string[], customName?: string) {
    return this.request<{ imported: boolean; template_id: string }>('POST', '/api/plugins/templates/import', {
      body: { package: packageDoc, confirmed_tools: confirmedTools, custom_name: customName },
      csrf: true,
    });
  }

  async rateTemplate(templateId: string, rating: number, comment?: string) {
    return this.request<{ summary: RatingSummary }>('POST', `/api/plugins/templates/${encodeURIComponent(templateId)}/rate`, {
      body: { rating, comment },
      csrf: true,
    });
  }

  // -------------------------------------------------------------------------
  // Cursor 级代码上下文
  // -------------------------------------------------------------------------

  async analyzeCodeSymbols(filePath: string, code: string) {
    return this.request<{ symbols: unknown[] }>('POST', '/api/plugins/cursor/analyze', {
      body: { file_path: filePath, code },
    });
  }

  async searchCodeContext(query: string, files: Record<string, string>, limit = 10) {
    return this.request<{ slices: ContextSlice[] }>('POST', '/api/plugins/cursor/search', {
      body: { query, files, limit },
    });
  }

  async assembleCodeContext(query: string, files: Record<string, string>, maxChars = 4000) {
    return this.request<ContextAssembly>('POST', '/api/plugins/cursor/assemble', {
      body: { query, files, max_chars: maxChars },
    });
  }

  // -------------------------------------------------------------------------
  // 企业嵌入 Helper
  // -------------------------------------------------------------------------

  generateEmbedUrl(options: EmbedOptions = {}): string {
    return generateIframeEmbedUrl(this.baseUrl, {
      ...options,
      tenantId: options.tenantId || this.tenantId,
      token: options.token || this.token,
    });
  }
}
