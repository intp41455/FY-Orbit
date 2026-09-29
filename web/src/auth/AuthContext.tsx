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
import { onUnauthorized } from '../api/client';
import type { OwnerIdentity } from '../api/types';

interface AuthState {
  owner: OwnerIdentity | null;
  loading: boolean;
  error: string | null;
  login: () => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
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

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authApi.me();
      setOwner(res.owner);
    } catch (e) {
      // 401/403 means simply not logged in; other errors (offline/5xx) are surfaced.
      const status = (e as { status?: number }).status;
      if (status === 401 || status === 403) {
        setOwner(null);
        setError(null);
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
  }, []);

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

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Even if the network call fails, clear local state.
    }
    purgeSensitiveClientState();
    setOwner(null);
    window.location.href = '/login';
  }, []);

  const value = useMemo(
    () => ({ owner, loading, error, login, logout, refresh }),
    [owner, loading, error, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
