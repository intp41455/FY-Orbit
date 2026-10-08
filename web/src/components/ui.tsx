import { useCallback, useEffect, useState } from 'react';
import { NetworkError } from '../api/client';

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <div className="muted" role="status">{label}</div>;
}

/**
 * 离线指示器（A-离线优先-01/03）。
 *
 * 这里只负责一件事：**断网时如实说明哪些还能用、哪些不能**。
 *
 * 注意与「默认离线运行架构」的区别：应用本身是「默认离线」（`FY_OFFLINE_MODE`
 * 缺省 True，即不打外网），那是后端的运行态，不是浏览器是否连得上网；
 * 后端的运行态由 `GET /api/offline/status` 如实上报，不在这里用
 * `navigator.onLine` 猜。本组件管的是浏览器侧真断网时给用户一句明确的降级提示。
 */
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
    <div className="notice warn" role="alert" data-testid="offline-badge">
      <strong>当前离线</strong>
      <span>
        本地能力照常：笔记 / 知识库检索 / 画布编辑与运行都在本机完成，不会丢。
        云端模型相关功能（一句话生成、AI 对话等）已停用——它们是本应用唯一的
        外部依赖；恢复联网后自动可用，无需重启。
      </span>
    </div>
  );
}

export function errorMessage(e: unknown): string {
  if (e instanceof NetworkError) return e.message;
  const status = (e as { status?: number }).status;
  const code = (e as { body?: { code?: string }; code?: string }).body?.code
    ?? (e as { code?: string }).code;
  const msg = (e as { body?: { message?: string }; message?: string }).body?.message
    ?? (e as Error).message;

  // 401 与 403 语义不同：401 是未登录/会话过期，403 是已登录但被拒
  // （CSRF 缺失、权限不足、配额超限……）。合并成一句话会丢掉真相，
  // 曾经让「CSRF token 还没拿到就 POST」表现为「点了没反应」。
  // 这里只为「后端没给 message」时提供按状态码区分的中文兜底，
  // 后端给了 message 就原样透出。
  if (!msg || msg === 'Unexpected error.') {
    if (status === 401) return '尚未登录或会话已过期，请重新登录后再试。';
    if (status === 403) {
      if (code === 'csrf' || code === 'csrf_failed' || code === 'invalid_csrf') {
        return '安全令牌缺失或已过期，正在刷新页面，请稍后重试。';
      }
      return '当前账号没有权限执行该操作。';
    }
    if (status === 409) return '内容已在别处被修改，请刷新后重试。';
    if (status === 422) return '提交的内容不符合要求，请检查后重试。';
  }
  return msg ?? '出现未知错误。';
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
