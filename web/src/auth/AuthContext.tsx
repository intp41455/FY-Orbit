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
import type { AccountInfo, GuestResponse, MeResponse, OwnerIdentity } from '../api/types';

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
  /** 注册新账号：成功后 refresh() 拿服务端权威身份（register 响应无 subject_type）。 */
  register: (input: {
    email: string;
    password: string;
    consent_accepted: boolean;
    display_name?: string;
  }) => Promise<void>;
  /** W8: 游客就地升级为正式账号；成功后调用方应 refresh()。 */
  upgradeGuest: (input: {
    email: string;
    password: string;
    consent_accepted: boolean;
    display_name?: string;
  }) => Promise<void>;
  /** R2/R11：账号读模型（/api/auth/account）：email/昵称/短号来源。
      `/auth/me` 只答“谁在调用”（冻结契约），展示层一律读这里。 */
  account: AccountInfo | null;
}

// R1.4：客户端留存的访客标记（用户 id）。换浏览器/清数据/无痕下没有它，
// 允许产生新访客；有它则先试找回，找回失败才新建。
const GUEST_MARK_KEY = 'fy.guest_id';

function readGuestMark(): string | null {
  try {
    return window.localStorage.getItem(GUEST_MARK_KEY);
  } catch {
    return null;
  }
}

function writeGuestMark(ownerId: string): void {
  try {
    window.localStorage.setItem(GUEST_MARK_KEY, ownerId);
  } catch {
    /* 隐私模式写不进去：找回兜底缺席，但不影响本次会话 */
  }
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
  const [account, setAccount] = useState<AccountInfo | null>(null);

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

  const refreshAccount = useCallback(async () => {
    try {
      setAccount(await authApi.account());
    } catch {
      setAccount(null);
    }
  }, []);

  /** W8: 静默建一个本地游客会话。失败时把原因如实抛出，不假装已登录。 */
  const ensureGuest = useCallback(async () => {
    const created = await authApi.createGuest();
    applyIdentity(created);
    // R1.4：记下访客标记，会话过期后凭它找回（同 id 不丢数据）。
    if (created.owner_id) writeGuestMark(created.owner_id);
    await refreshAccount();
  }, [applyIdentity, refreshAccount]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authApi.me();
      applyIdentity(res);
      await refreshAccount();
    } catch (e) {
      // 401/403 means simply not logged in. W8: that is the *normal* desktop
      // state, so silently become a guest instead of bouncing to /login —
      // this is what makes the workbench usable with no account at all.
      const status = (e as { status?: number }).status;
      if (status === 401 || status === 403) {
        // R1.4：先试找回（客户端留存的访客标记），找回失败才新建。
        // 换浏览器/清数据/无痕下没有标记，直接新建——按裁决允许产生新访客。
        const mark = readGuestMark();
        if (mark) {
          try {
            const reclaimed = await authApi.reclaimGuest(mark);
            applyIdentity(reclaimed);
            await refreshAccount();
            return;
          } catch {
            /* 标记过期或访客已升级/删除：落到新建 */
          }
        }
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
  }, [applyIdentity, ensureGuest, refreshAccount]);

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
      await refreshAccount();
    },
    [applyIdentity, refreshAccount],
  );

  const register = useCallback(
    async (input: {
      email: string;
      password: string;
      consent_accepted: boolean;
      display_name?: string;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const res = await authApi.register(input);
        if (res.csrf_token) setCsrfToken(res.csrf_token);
        await refresh();
      } catch (err: unknown) {
        setError((err as { message?: string })?.message || '注册失败，请检查输入后重试。');
        setLoading(false);
        throw err;
      }
    },
    [refresh],
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
    setAccount(null);
    window.location.href = '/login';
  }, []);

  const value = useMemo(
    () => ({ owner, loading, error, isGuest, plan, account, login, loginWithToken, logout, refresh, upgradeGuest, register }),
    [owner, loading, error, isGuest, plan, account, login, loginWithToken, logout, refresh, upgradeGuest, register],
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
