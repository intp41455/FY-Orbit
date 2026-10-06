import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineIcon } from '../ui/LineIcon';

/**
 * 包 A 轻量提示。与 ui-toast-stack 共享层样式对齐，但动作按钮是本域新增能力
 * （「重新启动会话」「撤销」这类必须就地给出补救入口，否则等于把用户丢在死路上）。
 */

export interface WbToastItem {
  id: number;
  tone: 'info' | 'warn' | 'error' | 'complete';
  icon: Parameters<typeof LineIcon>[0]['name'];
  text: string;
  action?: { label: string; run: () => void };
}

const TONE_ICON: Record<WbToastItem['tone'], string> = {
  info: 'ui-badge',
  warn: 'ui-badge--waiting',
  error: 'ui-badge--failed',
  complete: 'ui-badge--complete',
};

export function useWbToasts() {
  const [toasts, setToasts] = useState<WbToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((t: Omit<WbToastItem, 'id'> & { ttl?: number }) => {
    const id = ++seq.current;
    setToasts((list) => [...list.slice(-3), { ...t, id }]);
    if (t.ttl !== 0) {
      window.setTimeout(() => setToasts((list) => list.filter((x) => x.id !== id)), t.ttl ?? 5200);
    }
    return id;
  }, []);

  const stack = useMemo(() => ({ toasts, push, dismiss }), [toasts, push, dismiss]);
  return stack;
}

export function WbToastStack({
  items,
  onDismiss,
}: {
  items: WbToastItem[];
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    if (items.length === 0) return;
  }, [items.length]);

  if (items.length === 0) return null;
  return (
    <div className="ui-toast-stack wb-toast-stack" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`ui-toast wb-toast`} data-tone={t.tone}>
          <span className={`ui-badge ${TONE_ICON[t.tone]} wb-toast-glyph`} aria-hidden="true">
            <LineIcon name={t.icon} size={16} />
          </span>
          <span className="wb-toast-text">{t.text}</span>
          {t.action && (
            <button type="button" className="ui-btn ui-btn--sm ui-btn--ghost wb-toast-action"
                    onClick={() => { t.action!.run(); onDismiss(t.id); }}>
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            className="wb-toast-close"
            aria-label="关闭提示"
            onClick={() => onDismiss(t.id)}
          >
            <LineIcon name="xCircle" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
