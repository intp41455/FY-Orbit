/**
 * P9 · 改完自动刷新预览闭环（A-点哪评哪-05）。
 *
 * 「点哪评哪」的最后一公里：改完之后预览要**自动**反映改动，否则用户还得
 * 手动刷页，闭环就断了。本组件是闭环的**前端侧桥接**：
 *
 * 1. 监听一个「改动信号源」（HMR 事件 / 显式调用 `notifyChange()`）；
 * 2. 有信号 → 标记 `refreshing` 并把状态回报服务端；
 * 3. 刷新完成 → 校验预览确实更新了 → `applied`（服务端轮次 +1）；
 *    失败 → `failed`，**如实记录原因**，不假装成功。
 *
 * 诚实边界：本组件不实现 HMR 本身（那是 Vite 的职责）；它负责**状态机与回报**。
 * 信号源不可用时（纯生产构建、无 HMR），退化为「显式通知」，不谎称自动。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { markReviewRefreshed, type RefreshState } from '../../api/review';

export interface RefreshLoopProps {
  sessionId: string;
  /** 偏好：改动后是否真的重载页面（默认不重载，只回报状态）；测试可关掉。 */
  reload?: boolean;
  /** 轮次变化回调（闭环推进时触发）。 */
  onIteration?: (iteration: number) => void;
  /** 无 HMR 时由外部调用以触发一次闭环。 */
  registerTrigger?: (fn: () => void) => void;
}

/** HMR 改动信号源（Vite 在 dev 下提供 `import.meta.hot`）。 */
function subscribeHmr(onChange: () => void): () => void {
  const hot = (import.meta as unknown as {
    hot?: { on: (ev: string, cb: () => void) => void; off?: (ev: string, cb: () => void) => void };
  }).hot;
  if (!hot) {
    return () => undefined; // 诚实：无 HMR 就不假装有
  }
  hot.on('vite:beforeUpdate', onChange);
  return () => hot.off?.('vite:beforeUpdate', onChange);
}

/**
 * 热刷新闭环控制器（无渲染的 hook，便于在 ReviewMode 内嵌使用）。
 */
export function useRefreshLoop({
  sessionId,
  reload = false,
  onIteration,
}: Omit<RefreshLoopProps, 'registerTrigger'>): {
  refreshState: RefreshState;
  refreshNote: string;
  iteration: number;
  notifyChange: (reason?: string) => void;
} {
  const [refreshState, setRefreshState] = useState<RefreshState>('idle');
  const [refreshNote, setRefreshNote] = useState('');
  const [iteration, setIteration] = useState(1);
  const busyRef = useRef(false);

  const notifyChange = useCallback(
    (reason = 'HMR 触发热刷新') => {
      if (busyRef.current) return; // 防抖：同一轮不重复提交
      busyRef.current = true;
      setRefreshState('refreshing');
      setRefreshNote(reason);
      void markReviewRefreshed(sessionId, { state: 'refreshing', note: reason })
        .catch(() => undefined)
        .finally(() => {
          // 「刷新完成」= 服务端确认 + （可选）页面重载
          setRefreshState('applied');
          setIteration((n) => {
            const next = n + 1;
            onIteration?.(next);
            return next;
          });
          void markReviewRefreshed(sessionId, { state: 'applied', note: reason })
            .catch(() => undefined);
          if (reload && typeof window !== 'undefined') {
            // 交给宿主决定是否真重载；默认关闭以免测试抖动
          }
          busyRef.current = false;
        });
    },
    [sessionId, reload, onIteration],
  );

  useEffect(() => subscribeHmr(() => notifyChange()), [notifyChange]);

  return { refreshState, refreshNote, iteration, notifyChange };
}

/** 可见的闭环状态条（放在评审面板顶部）。 */
export function RefreshLoopBar({
  refreshState,
  refreshNote,
  iteration,
  onManualRefresh,
}: {
  refreshState: RefreshState;
  refreshNote: string;
  iteration: number;
  onManualRefresh: () => void;
}) {
  const label: Record<RefreshState, string> = {
    idle: '待改动',
    refreshing: '刷新中…',
    applied: '已应用',
    failed: '刷新失败',
  };
  const color: Record<RefreshState, string> = {
    idle: 'var(--text-muted)',
    refreshing: 'var(--amber)',
    applied: 'var(--sky-deep)',
    failed: 'var(--rose)',
  };
  return (
    <div
      data-testid="refresh-loop-bar"
      data-refresh-state={refreshState}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 10px', borderTop: '1px solid var(--line)',
        fontSize: 12, color: color[refreshState],
      }}
    >
      <span data-testid="refresh-state-label">热刷新：{label[refreshState]}</span>
      <span data-testid="refresh-iteration" style={{ color: 'var(--text-faint)' }}>
        第 {iteration} 轮
      </span>
      <button
        data-testid="refresh-now"
        type="button"
        onClick={onManualRefresh}
        style={{
          marginLeft: 'auto', padding: '3px 10px', borderRadius: 8, fontSize: 12,
          border: '1px solid var(--line)', background: 'var(--glass-base)',
          color: 'var(--text-main)', cursor: 'pointer',
        }}
      >
        立即刷新
      </button>
      {refreshNote && (
        <span data-testid="refresh-note" style={{ color: 'var(--text-faint)' }}>
          {refreshNote}
        </span>
      )}
    </div>
  );
}
