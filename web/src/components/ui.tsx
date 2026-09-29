import { useCallback, useEffect, useState } from 'react';
import { NetworkError } from '../api/client';

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <div className="muted" role="status">{label}</div>;
}

export function OfflineBadge() {
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== 'undefined' ? !navigator.onLine : false,
  );
  useEffect(() => {
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);
  if (!offline) return null;
  return (
    <div className="notice warn" role="alert">
      You are offline. Read-only shell available; new submissions cannot be sent.
    </div>
  );
}

export function errorMessage(e: unknown): string {
  if (e instanceof NetworkError) return e.message;
  const status = (e as { status?: number }).status;
  const msg = (e as { body?: { message?: string }; message?: string }).body?.message
    ?? (e as Error).message;
  if (status === 401 || status === 403) return 'You are not authorized for this resource.';
  return msg ?? 'Unexpected error.';
}

// Lightweight async loader hook — calls the async fn on mount and exposes
// state. Mocks are injected at the network layer in tests, not here.
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadTick]);

  return { data, loading, error, reload };
}
