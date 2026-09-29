// Core fetch wrapper for the Find Yourself backend.
// Principles (FROZEN_CONTRACT §1, §5, §11):
//  - Session is an HttpOnly cookie; the JS layer NEVER reads or stores tokens.
//  - Writes send an Origin/CSRF header when the backend exposes one via meta.
//  - Every non-2xx is normalized to ApiError with a machine code.
//  - Network/offline failures are surfaced explicitly (never faked as success).
import type { ApiErrorBody } from './types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

type Unsubscribe = () => void;
type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
export function onUnauthorized(h: UnauthorizedHandler): Unsubscribe {
  unauthorizedHandler = h;
  return () => {
    if (unauthorizedHandler === h) unauthorizedHandler = null;
  };
}

export class NetworkError extends Error {
  constructor(public kind: 'offline' | 'failed' | 'aborted', message: string) {
    super(message);
  }
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

function readCsrfToken(): string | null {
  if (typeof document === 'undefined') return null;
  const el = document.querySelector('meta[name="csrf-token"]');
  return el?.getAttribute('content') || null;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET';
  const url = buildUrl(path, opts.query);

  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  // CSRF header only when the backend chose to expose a token to the page.
  const csrf = readCsrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new NetworkError('aborted', 'Request aborted');
    }
    // navigator.onLine === false is the explicit offline signal.
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      throw new NetworkError('offline', 'You are offline. Changes cannot be submitted.');
    }
    throw new NetworkError('failed', 'Network request failed. Is the backend reachable?');
  }

  if (res.status === 401 || res.status === 403) {
    unauthorizedHandler?.();
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!res.ok) {
    // Unified error envelope: {"error":{"code","message","details"}} (§1).
    const wrapped = parsed as { error?: ApiErrorBody } | ApiErrorBody | null;
    const body =
      wrapped && typeof wrapped === 'object' && 'error' in wrapped && (wrapped as { error?: ApiErrorBody }).error
        ? (wrapped as { error: ApiErrorBody }).error
        : (wrapped as ApiErrorBody | null);
    throw new ApiError(res.status, body, `Request failed with status ${res.status}`);
  }
  return parsed as T;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const base = API_BASE.replace(/\/$/, '');
  const full = path.startsWith('/') ? path : `/${path}`;
  let url = `${base}${full}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== '') params.set(k, String(v));
    }
    const qs = params.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }
  return url;
}
