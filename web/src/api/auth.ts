import { request } from './client';
import type {
  AccountInfo,
  DevTokenRequest,
  DevTokenResponse,
  GuestResponse,
  LoginRedirectResponse,
  MeResponse,
} from './types';

/**
 * W8 · 账号三层。
 *
 * 游客模式是**无感**的：应用启动时若没有会话，前端静默调用 `createGuest()`，
 * 服务端在本地环境建一个真实游客账号（`status=guest`, `email guest-<uuid>@local`）
 * 并下发 HttpOnly 会话 cookie。工作台因此免登录可用。
 *
 * 注意 `createGuest` 的失败**不会**被吞成「游客已登录」：桌面端若拒绝了游客
 * （非 loopback / 非 local 环境），`AuthContext` 会把这个错误如实呈现给用户，
 * 而不是假装一切正常。
 */
export const authApi = {
  me: () => request<MeResponse>('/auth/me'),
  loginRedirect: () => request<LoginRedirectResponse>('/auth/login'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  // Local-only convenience; backend must reject outside 127.0.0.1 (§5.1).
  localDevToken: (body: DevTokenRequest) =>
    request<DevTokenResponse>('/auth/local/dev-token', { method: 'POST', body }),

  // ---- W8 account tiers ----
  /** Create a local guest session. Refused outside local/test + loopback. */
  createGuest: () => request<GuestResponse>('/auth/guest', { method: 'POST' }),
  /** Upgrade the current guest account in place; all existing data stays. */
  upgradeGuest: (body: {
    email: string;
    password: string;
    consent_accepted: boolean;
    display_name?: string;
  }) => request<GuestResponse>('/auth/guest/upgrade', { method: 'POST', body }),
  /** Settings account card: email, tier, and the honest payment flag. */
  account: () => request<AccountInfo>('/api/auth/account'),
};
