import { request } from './client';
import type { MemorySearchResponse } from './types';

export const memoryApi = {
  search: (params: { q: string; domain?: string; limit?: number; cursor?: string }) =>
    request<MemorySearchResponse>('/api/memory/search', {
      query: { q: params.q, domain: params.domain, limit: params.limit, cursor: params.cursor },
    }),
};
