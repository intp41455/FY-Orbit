import { request } from './client';
import type { ExportRequest, ExportResponse, SettingsSummary } from './types';

export const dataApi = {
  export: (body: ExportRequest) =>
    request<ExportResponse>('/api/export', { method: 'POST', body }),
  settings: () => request<SettingsSummary>('/api/settings'),
};
