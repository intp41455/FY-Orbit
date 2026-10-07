/** 企业 WebApp iframe 嵌入辅助（A-生态兼容-01 · JS/TS）。 */

export interface EmbedOptions {
  page?: 'marketplace' | 'templates' | 'workbench' | 'observability';
  tenantId?: string;
  userId?: string;
  token?: string;
  theme?: 'light' | 'dark';
  locale?: string;
  readOnly?: boolean;
}

export function generateIframeEmbedUrl(baseUrl: string, options: EmbedOptions = {}): string {
  const {
    page = 'marketplace',
    tenantId,
    userId,
    token,
    theme = 'light',
    locale = 'zh-CN',
    readOnly = false,
  } = options;

  const cleanBase = baseUrl.replace(/\/+$/, '');
  const pathMap: Record<string, string> = {
    marketplace: '/plugins',
    templates: '/templates',
    workbench: '/workbench',
    observability: '/observability',
  };
  const route = pathMap[page] || `/${page.replace(/^\/+/, '')}`;

  const params = new URLSearchParams({
    embed: 'true',
    theme,
    locale,
  });
  if (readOnly) params.set('read_only', 'true');
  if (tenantId) params.set('tenant_id', tenantId);
  if (userId) params.set('user_id', userId);
  if (token) params.set('token', token);

  return `${cleanBase}${route}?${params.toString()}`;
}
