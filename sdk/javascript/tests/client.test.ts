import { describe, it, expect, vi, beforeEach } from 'vitest';
import { FindYourselfClient, generateIframeEmbedUrl } from '../src/index';

describe('FindYourself JavaScript SDK', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('生成正确的企业 WebApp iframe 嵌入 URL', () => {
    const url = generateIframeEmbedUrl('http://localhost:8000', {
      page: 'marketplace',
      tenantId: 'tenant-corp',
      userId: 'emp-101',
      token: 'jwt-abc',
      theme: 'dark',
    });

    expect(url).toContain('http://localhost:8000/plugins?');
    expect(url).toContain('tenant_id=tenant-corp');
    expect(url).toContain('user_id=emp-101');
    expect(url).toContain('theme=dark');
    expect(url).toContain('embed=true');
  });

  it('FindYourselfClient 发送带 Bearer Token 的请求', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [{ skill_id: 'tool-1', name: 'search', rating: { score: 4.9 } }],
        total: 1,
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const client = new FindYourselfClient({
      baseUrl: 'http://test.local',
      token: 'token-xyz',
    });

    const res = await client.listPlugins({ query: 'search', sortBy: 'score' });
    expect(res.total).toBe(1);
    expect(mockFetch).toHaveBeenCalledOnce();
    const [callUrl, callOpts] = mockFetch.mock.calls[0];
    expect(callUrl).toContain('http://test.local/api/plugins/marketplace?query=search&sort_by=score');
    expect(callOpts.headers['Authorization']).toBe('Bearer token-xyz');
  });

  it('模板分层与安全审查接口调用', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        passed: true,
        risk_level: 'low',
        can_import: true,
        dangerous_tools: [],
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const client = new FindYourselfClient();
    const report = await client.securityReviewTemplate({ magic: 'FYTEMPLATE_V1' });
    expect(report.passed).toBe(true);
    expect(report.risk_level).toBe('low');
  });
});
