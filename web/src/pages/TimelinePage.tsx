import { useCallback, useEffect, useState } from 'react';
import {
  compareFork,
  createFork,
  discardFork,
  fetchTimeline,
  listForks,
  type CompareResult,
  type ForkRecordView,
  type TimelineEvent,
} from '../api/archiveFork';
import { ArchiveTimeline, ForkCompare, ForkForm } from '../components/timeline';

/**
 * P4 · 存档回溯页（A-存档回溯-04/05/06）。
 *
 * 三个区：
 * 1. 左：存档历史时间线（A-存档回溯-06）
 * 2. 中：分叉表单（A-存档回溯-04「改参重跑生成新分支」）
 * 3. 右：分支对比（A-存档回溯-05）
 *
 * 路由挂载（App.tsx）由主控统一追加——本包不自行改动锁三。
 */
export function TimelinePage() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [forks, setForks] = useState<ForkRecordView[]>([]);
  const [selectedFork, setSelectedFork] = useState<string>('');
  const [compare, setCompare] = useState<CompareResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  const reload = useCallback(() => setReloadTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [tl, fk] = await Promise.all([fetchTimeline(), listForks()]);
        if (cancelled) return;
        setEvents(tl.events);
        setForks(fk.forks);
        setError('');
      } catch {
        if (!cancelled) setError('加载存档数据失败（后端未启动或未登录）');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  const handleFork = async (input: {
    source_thread_id: string;
    source_checkpoint_id: string;
    overrides: Record<string, unknown>;
    label: string;
  }) => {
    setBusy(true);
    try {
      await createFork(input);
      setError('');
      reload();
    } catch {
      setError('分叉失败：请检查源 thread 是否存在');
    } finally {
      setBusy(false);
    }
  };

  const handleCompare = async (forkId: string) => {
    setSelectedFork(forkId);
    try {
      const r = await compareFork(forkId);
      setCompare(r);
      setError('');
    } catch {
      setCompare(null);
      setError('对比失败');
    }
  };

  const handleDiscard = async (forkId: string) => {
    try {
      await discardFork(forkId, '用户在时间线中弃用');
      reload();
      if (selectedFork === forkId) setSelectedFork('');
    } catch {
      setError('弃用失败');
    }
  };

  const activeForks = forks.filter((f) => f.state === 'active');

  return (
    <div data-testid="timeline-page" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <header>
        <h2 style={{ margin: 0, fontSize: 'var(--ui-fs-xl, 20px)', color: 'var(--text-main, #0f172a)' }}>
          存档回溯 · 分叉 / 对比 / 时间线
        </h2>
        <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
          回溯任意存档点、改参重跑生成新分支；原历史永不被覆盖。
        </p>
      </header>

      {error && (
        <p data-testid="timeline-error" style={{ color: 'var(--rose, #e11d48)', fontSize: 13, margin: 0 }}>
          {error}
        </p>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(260px, 1fr) minmax(260px, 1fr) minmax(320px, 1.4fr)',
          gap: 14,
          alignItems: 'start',
        }}
      >
        {/* 区 1：时间线 */}
        <section
          style={{
            border: '1px solid var(--line, #e2e8f0)', borderRadius: 12, padding: 12,
            background: 'var(--glass-elevated, #fff)',
          }}
        >
          <h3 style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-main, #0f172a)' }}>
            存档历史时间线（{events.length}）
          </h3>
          <ArchiveTimeline events={events} onSelectFork={handleCompare} />
        </section>

        {/* 区 2：分叉表单 */}
        <section
          style={{
            border: '1px solid var(--line, #e2e8f0)', borderRadius: 12, padding: 12,
            background: 'var(--glass-elevated, #fff)',
          }}
        >
          <h3 style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-main, #0f172a)' }}>
            改参重跑 · 生成新分支
          </h3>
          <ForkForm onSubmit={handleFork} disabled={busy} />
        </section>

        {/* 区 3：对比 */}
        <section
          style={{
            border: '1px solid var(--line, #e2e8f0)', borderRadius: 12, padding: 12,
            background: 'var(--glass-elevated, #fff)',
          }}
        >
          <h3 style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-main, #0f172a)' }}>
            分支对比
          </h3>

          {activeForks.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              {activeForks.map((f) => (
                <span key={f.fork_id} style={{ display: 'inline-flex', gap: 4 }}>
                  <button
                    type="button"
                    data-testid={`fork-select-${f.fork_id}`}
                    onClick={() => handleCompare(f.fork_id)}
                    style={{
                      padding: '4px 8px', borderRadius: 8, fontSize: 11, cursor: 'pointer',
                      border: `1px solid ${selectedFork === f.fork_id ? 'var(--sky, #0284c7)' : 'var(--line, #e2e8f0)'}`,
                      background: selectedFork === f.fork_id ? 'var(--ice-soft, #e0f2fe)' : 'transparent',
                      color: 'var(--text-main, #0f172a)',
                    }}
                  >
                    {f.label || f.new_thread_id}
                  </button>
                  <button
                    type="button"
                    data-testid={`fork-discard-${f.fork_id}`}
                    onClick={() => handleDiscard(f.fork_id)}
                    style={{
                      padding: '4px 6px', borderRadius: 8, fontSize: 11, cursor: 'pointer',
                      border: '1px solid var(--line, #e2e8f0)', background: 'transparent',
                      color: 'var(--text-muted, #64748b)',
                    }}
                  >
                    弃用
                  </button>
                </span>
              ))}
            </div>
          )}

          <ForkCompare result={compare} />
        </section>
      </div>
    </div>
  );
}
