import type { TimelineEvent } from '../../api/archiveFork';

/**
 * P4 · 存档历史时间线（A-存档回溯-06）。
 *
 * 纯展示组件：把后端返回的**已排序**事件按时间轴渲染，不重排、不过滤，
 * 保证「有序且完整」在视图层同样成立。
 * 每条事件可点击 → 回调上层做分叉/对比动作。
 */

interface Props {
  events: TimelineEvent[];
  onSelectFork?: (forkId: string) => void;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('zh-CN');
  } catch {
    return iso;
  }
}

const KIND_LABEL: Record<string, string> = {
  fork: '分叉',
  discard: '弃用',
};

const KIND_COLOR: Record<string, string> = {
  fork: 'var(--sky, #0284c7)',
  discard: 'var(--rose, #e11d48)',
};

export function ArchiveTimeline({ events, onSelectFork }: Props) {
  if (events.length === 0) {
    return (
      <p style={{ color: 'var(--text-faint, #94a3b8)', fontSize: 13, textAlign: 'center', padding: 24 }}>
        还没有存档分支记录。回溯某个存档点并改参重跑后，这里会出现分支时间线。
      </p>
    );
  }

  return (
    <ol
      data-testid="archive-timeline"
      style={{ listStyle: 'none', margin: 0, padding: 0, position: 'relative' }}
    >
      {events.map((e, i) => (
        <li
          key={`${e.fork_id}-${e.kind}-${i}`}
          data-testid={`timeline-event-${e.kind}`}
          style={{ display: 'flex', gap: 12, paddingBottom: 14, position: 'relative' }}
        >
          {/* 轴点 */}
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 16 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: KIND_COLOR[e.kind] ?? 'var(--text-muted, #64748b)',
                marginTop: 4,
                flexShrink: 0,
              }}
            />
            {i < events.length - 1 && (
              <span style={{ flex: 1, width: 2, background: 'var(--line, #e2e8f0)' }} />
            )}
          </div>

          {/* 内容卡 */}
          <button
            type="button"
            onClick={() => e.kind === 'fork' && onSelectFork?.(e.fork_id)}
            style={{
              flex: 1,
              textAlign: 'left',
              border: '1px solid var(--line, #e2e8f0)',
              borderRadius: 10,
              padding: '8px 10px',
              background: 'var(--glass-base, #fff)',
              cursor: e.kind === 'fork' && onSelectFork ? 'pointer' : 'default',
              font: 'inherit',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  fontSize: 11, fontWeight: 700,
                  color: KIND_COLOR[e.kind] ?? 'inherit',
                }}
              >
                {KIND_LABEL[e.kind] ?? e.kind}
              </span>
              <span style={{ fontSize: 12, color: 'var(--text-main, #0f172a)' }}>
                {e.thread_id}
              </span>
              {e.label && (
                <span style={{ fontSize: 11, color: 'var(--text-muted, #64748b)' }}>
                  {e.label}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-faint, #94a3b8)', marginTop: 3 }}>
              {formatTime(e.at)}
              {e.parent_thread_id ? ` · 源自 ${e.parent_thread_id}` : ''}
            </div>
            {e.kind === 'fork' && e.overrides && Object.keys(e.overrides).length > 0 && (
              <div style={{ fontSize: 11, color: 'var(--text-muted, #64748b)', marginTop: 3 }}>
                改参：{Object.keys(e.overrides).join('、')}
              </div>
            )}
            {e.kind === 'discard' && e.reason && (
              <div style={{ fontSize: 11, color: 'var(--rose, #e11d48)', marginTop: 3 }}>
                原因：{e.reason}
              </div>
            )}
          </button>
        </li>
      ))}
    </ol>
  );
}
