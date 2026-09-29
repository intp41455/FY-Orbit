import { request } from './client';
import type { DevTokenRequest, DevTokenResponse, LoginRedirectResponse, MeResponse } from './types';

export const authApi = {
  me: () => request<MeResponse>('/auth/me'),
  loginRedirect: () => request<LoginRedirectResponse>('/auth/login'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  // Local-only convenience; backend must reject outside 127.0.0.1 (§5.1).
  localDevToken: (body: DevTokenRequest) =>
    request<DevTokenResponse>('/auth/local/dev-token', { method: 'POST', body }),
};
