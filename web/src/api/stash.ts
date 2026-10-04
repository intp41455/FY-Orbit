// Typed client for the work-stash (记录暂存区) API (P1-04, api/routes/stash.py).
// Reuses the shared `request` wrapper: session cookie + CSRF header + unified
// ApiError normalisation, same as the other api/* modules.
import { request } from './client';

const BASE = '/api/stash';

export interface StashRecord {
  id: string;
  title: string;
  content: string;
  content_type: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface StashListResponse {
  records: StashRecord[];
  count: number;
}

export const stashApi = {
  stage: (body: {
    content: string;
    title?: string;
    content_type?: string;
    metadata?: Record<string, unknown>;
  }) => request<{ status: string; record: StashRecord }>(BASE, { method: 'POST', body }),

  list: (limit = 200) => request<StashListResponse>(BASE, { query: { limit } }),

  read: (id: string) => request<{ record: StashRecord }>(`${BASE}/${id}`),

  remove: (id: string) => request<{ status: string; deleted: string }>(`${BASE}/${id}`, { method: 'DELETE' }),

  clear: () => request<{ status: string; deleted: number }>(BASE, { method: 'DELETE' }),
};

/** Read a string field out of a stash record's free-form metadata. */
export function metaString(record: StashRecord, key: string): string | null {
  const v = record.metadata?.[key];
  return typeof v === 'string' && v ? v : null;
}
