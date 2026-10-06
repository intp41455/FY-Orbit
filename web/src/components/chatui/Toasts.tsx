/**
 * 包 C · 轻提示（chatui 私有）
 * 用于「归档可撤销（5s）」「知识库打开提示」「原位置已失效」等真实反馈，
 * 不用于伪造成功。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface ToastItem {
  id: string;
  text: string;
  kind?: 'info' | 'warn' | 'error';
  actionLabel?: string;
  onAction?: () => void;
  /** 毫秒；0 表示不自动消失 */
  timeout?: number;
}

function newId(): string {
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (t: Omit<ToastItem, 'id'>) => {
      const id = newId();
      const item: ToastItem = { timeout: 5000, kind: 'info', ...t, id };
      setToasts((prev) => [...prev, item]);
      if (item.timeout && item.timeout > 0) {
        timers.current.set(
          id,
          setTimeout(() => {
            setToasts((prev) => prev.filter((x) => x.id !== id));
            timers.current.delete(id);
          }, item.timeout),
        );
      }
      return id;
    },
    [],
  );

  useEffect(
    () => () => {
      for (const timer of timers.current.values()) clearTimeout(timer);
      timers.current.clear();
    },
    [],
  );

  return { toasts, push, dismiss };
}

export function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="ui-toast-stack chatui-toast-stack" aria-live="polite">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`ui-toast chatui-toast${t.kind === 'warn' ? ' ui-toast--warn' : ''}${t.kind === 'error' ? ' ui-toast--error' : ''}`}
          role="status"
        >
          <span className="chatui-toast-text">{t.text}</span>
          {t.actionLabel ? (
            <button
              type="button"
              className="ui-btn ui-btn--sm chatui-toast-action"
              onClick={() => {
                t.onAction?.();
                onDismiss(t.id);
              }}
            >
              {t.actionLabel}
            </button>
          ) : null}
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm chatui-toast-close"
            aria-label="关闭提示"
            onClick={() => onDismiss(t.id)}
          >
            关闭
          </button>
        </div>
      ))}
    </div>
  );
}
