import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { hubApi } from './hub';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  document.head.innerHTML = '<meta name="csrf-token" content="tok-123" />';
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.innerHTML = '';
});

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message } }, status);
}

describe('hubApi', () => {
  it('GET 列表带 same-origin Cookie，不带 CSRF 头（读操作）', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ connections: [], count: 0 }));
    await hubApi.listConnections();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/hub/connections');
    expect(init.method).toBeUndefined();
    expect(init.credentials).toBe('same-origin');
    expect(init.headers?.['X-CSRF-Token']).toBeUndefined();
  });

  it('写操作必发 X-CSRF-Token 与 JSON 头', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ connection: { id: 'c1' } }));
    await hubApi.createConnection({ name: 'x', kind: 'openai_chat' });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe('POST');
    expect(init.headers['X-CSRF-Token']).toBe('tok-123');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(init.body).name).toBe('x');
  });

  it('非 2xx 抛出的错误带上后端 code 与 message（页面可原样展示）', async () => {
    fetchMock.mockResolvedValue(errorResponse(403, 'hub_connection_forbidden', '无权访问该连接'));
    await expect(hubApi.getConnection('c9')).rejects.toMatchObject({
      code: 'hub_connection_forbidden',
      status: 403,
      message: '无权访问该连接',
    });
  });

  it('探活失败（200 + ok:false）不抛异常，如实返回给页面', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        connection: { id: 'c1' },
        report: { ok: false, detail: 'Connection refused', latency_ms: 0 },
      }),
    );
    const res = await hubApi.healthCheck('c1');
    expect(res.report.ok).toBe(false);
    expect(res.report.detail).toBe('Connection refused');
  });

  it('调用失败（200 + ok:false）同样不抛，error 字段透传', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        connection_id: 'c1',
        action: 'invoke',
        result: { ok: false, output: null, error: 'invalid api key', latency_ms: 2, meta: {} },
      }),
    );
    const res = await hubApi.invoke('c1', { action: 'invoke' });
    expect(res.result.ok).toBe(false);
    expect(res.result.error).toBe('invalid api key');
  });

  it('删除走 DELETE 方法', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'c1', deleted: true }));
    await hubApi.deleteConnection('c1');
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe('DELETE');
  });

  it('移除能力时能力名做 URL 编码', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ connection_id: 'c1', capabilities: [] }));
    await hubApi.unregisterCapability('c1', 'a/b c');
    expect(String(fetchMock.mock.calls[0][0])).toContain('a%2Fb%20c');
  });

  it('能力清单在无 kind 时不带空查询参数', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ capabilities: [], count: 0 }));
    await hubApi.listCapabilities();
    expect(String(fetchMock.mock.calls[0][0])).not.toContain('kind=');
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(jsonResponse({ capabilities: [], count: 0 }));
    await hubApi.listCapabilities('mcp_server');
    expect(String(fetchMock.mock.calls[0][0])).toContain('kind=mcp_server');
  });

  it('非 JSON 响应体也不会炸，落到 HTTP 状态文案', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      text: async () => '<html>bad gateway</html>',
    } as unknown as Response);
    await expect(hubApi.listPresets()).rejects.toThrow('请求失败（HTTP 502）');
  });
});
