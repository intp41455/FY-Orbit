import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { authApi } from '../api/auth';
import { onUnauthorized, setCsrfToken } from '../api/client';
import type { GuestResponse, MeResponse, OwnerIdentity } from '../api/types';

export interface AuthState {
  owner: OwnerIdentity | null;
  loading: boolean;
  error: string | null;
  /** W8: true when the session is a local guest account (免登录默认态). */
  isGuest: boolean;
  /** W8: 'free' | 'pro' | 'unknown'. v1 不接支付，UI 不得暗示已开通。 */
  plan: string;
  login: () => void;
  loginWithToken: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  /** W8: 游客就地升级为正式账号；成功后调用方应 refresh()。 */
  upgradeGuest: (input: {
    email: string;
    password: string;
    consent_accepted: boolean;
    display_name?: string;
  }) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

// Sensitive client state that must be purged on logout (FROZEN_CONTRACT §11,
// evidence U11). Tokens themselves live in HttpOnly cookies and are NOT in JS
// storage; we still clear any app-level caches and storages defensively.
function purgeSensitiveClientState(): void {
  try {
    window.localStorage.clear();
    window.sessionStorage.clear();
  } catch {
    /* storage may be unavailable; ignore */
  }
  // Clear runtime caches (we configure NO runtime API caching, but be safe).
  if (typeof window !== 'undefined' && 'caches' in window) {
    void window.caches
      .keys()
      .then((keys) => Promise.all(keys.map((k) => window.caches.delete(k))))
      .catch(() => undefined);
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [owner, setOwner] = useState<OwnerIdentity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isGuest, setIsGuest] = useState(false);
  const [plan, setPlan] = useState('unknown');

  const applyIdentity = useCallback((res: MeResponse | GuestResponse) => {
    if (res.csrf_token) setCsrfToken(res.csrf_token);
    // Owner session only when the server says so. Both `GET /auth/me` and the
    // W8 guest endpoints return `subject_type`, so there is exactly one rule.
    if (res.subject_type === 'owner') {
      setOwner({ sub: res.owner_id, authenticated: true });
      setIsGuest(res.is_guest === true);
      setPlan(res.plan ?? 'unknown');
    } else {
      setOwner(null);
      setIsGuest(false);
      setPlan('unknown');
    }
  }, []);

  /** W8: 静默建一个本地游客会话。失败时把原因如实抛出，不假装已登录。 */
  const ensureGuest = useCallback(async () => {
    applyIdentity(await authApi.createGuest());
  }, [applyIdentity]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authApi.me();
      applyIdentity(res);
    } catch (e) {
      // 401/403 means simply not logged in. W8: that is the *normal* desktop
      // state, so silently become a guest instead of bouncing to /login —
      // this is what makes the workbench usable with no account at all.
      const status = (e as { status?: number }).status;
      if (status === 401 || status === 403) {
        try {
          await ensureGuest();
        } catch (guestErr) {
          // Honest failure: guest sessions can be refused (non-local env, or a
          // non-loopback peer). Surface that rather than faking a session.
          setOwner(null);
          const status400 = (guestErr as { status?: number })?.status;
          setError(
            status400 === 403
              ? '此环境未开放游客模式，且当前未登录。请在设置中登录正式账号。'
              : '无法连接后端以创建本地游客会话，请检查服务是否在运行。',
          );
        }
      } else {
        setOwner(null);
        setError(
          navigator.onLine === false
            ? 'You are offline. The app shell loaded, but your session could not be checked.'
            : 'Could not reach the backend to verify your session.',
        );
      }
    } finally {
      setLoading(false);
    }
  }, [applyIdentity, ensureGuest]);

  useEffect(() => {
    void refresh();
    const off = onUnauthorized(() => {
      setOwner(null);
    });
    return off;
  }, [refresh]);

  const login = useCallback(() => {
    // Hand off to the backend OIDC flow (§5.1). We never open a local token
    // prompt in production.
    window.location.href = '/auth/login';
  }, []);

  const loginWithToken = useCallback(
    async (token: string) => {
      setLoading(true);
      setError(null);
      try {
        const res = await authApi.localDevToken({ token });
        if (res.csrf_token) setCsrfToken(res.csrf_token);
        await refresh();
      } catch (err: unknown) {
        setError(
          (err as { message?: string })?.message || '本地口令校验失败，请检查口令并确保在 127.0.0.1 访问。',
        );
        setLoading(false);
      }
    },
    [refresh],
  );

  const upgradeGuest = useCallback(
    async (input: { email: string; password: string; consent_accepted: boolean; display_name?: string }) => {
      applyIdentity(await authApi.upgradeGuest(input));
    },
    [applyIdentity],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Even if the network call fails, clear local state.
    }
    purgeSensitiveClientState();
    setOwner(null);
    setIsGuest(false);
    setPlan('unknown');
    window.location.href = '/login';
  }, []);

  const value = useMemo(
    () => ({ owner, loading, error, isGuest, plan, login, loginWithToken, logout, refresh, upgradeGuest }),
    [owner, loading, error, isGuest, plan, login, loginWithToken, logout, refresh, upgradeGuest],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/**
 * W8: non-throwing sibling of {@link useAuth}, for page sections that must
 * degrade instead of taking the whole page down.
 *
 * `AuthProvider` wraps the entire app in `App.tsx`, so production always has a
 * provider here. The account card still uses the optional form because a single
 * missing section should never blank out an unrelated settings page (it would
 * also make the card untestable in isolation). When the provider really is
 * absent the card says so explicitly — it does not invent a session.
 */
export function useOptionalAuth(): AuthState | null {
  return useContext(AuthContext);
}
