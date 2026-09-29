import { describe, it, expect, vi, beforeEach } from 'vitest';
import { request, ApiError, NetworkError } from './client';

describe('api client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('sends Idempotency-Key header and JSON body for POST', async () => {
    const fetchMock = vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    await request('/api/tasks', { method: 'POST', body: { goal: 'x' }, idempotencyKey: 'abc' });
    const init = fetchMock.mock.calls[0]?.[1];
    expect((init!.headers as Record<string, string>)['Idempotency-Key']).toBe('abc');
    expect((init!.headers as Record<string, string>)['Content-Type']).toContain('application/json');
  });

  it('normalizes non-2xx into ApiError with machine code', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'expired', message: 'Proposal expired' } }), { status: 400 }),
    );
    const err = await request('/api/proposals/1/decision', { method: 'POST', body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe('Proposal expired');
    expect((err as ApiError).body?.code).toBe('expired');
  });

  it('throws NetworkError(offline) when navigator.onLine is false', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(request('/api/conversations')).rejects.toBeInstanceOf(NetworkError);
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
  });

  it('does not surface 401 as a thrown success; calls unauthorized handler', async () => {
    const { onUnauthorized } = await import('./client');
    const handler = vi.fn();
    const off = onUnauthorized(handler);
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 401 }));
    await expect(request('/auth/me')).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalled();
    off();
  });
});
