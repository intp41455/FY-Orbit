import { useEffect, useRef, useState } from 'react';
import { tasksApi } from '../api/tasks';
import { openTaskEventStream } from '../api/sse';
import type { TaskEvent, TaskSummary } from '../api/types';
import { Spinner, errorMessage } from '../components/ui';

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `idem-${Date.now()}`;
}

export function WorkbenchPage() {
  const [goal, setGoal] = useState('');
  const [task, setTask] = useState<TaskSummary | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<{ close: () => void } | null>(null);

  useEffect(() => () => streamRef.current?.close(), []);

  async function createTask() {
    if (!goal.trim()) return;
    setLoading(true);
    setError(null);
    setEvents([]);
    try {
      const t = await tasksApi.create({ goal: goal.trim(), idempotency_key: newIdempotencyKey() });
      setTask(t);
      streamRef.current = openTaskEventStream(t.id, {
        onEvent: (e) => setEvents((ev) => [...ev, e]),
        onError: () => {
          /* surfaced via next poll; never fake success */
        },
      });
      // Light refresh of task state.
      const poll = setInterval(async () => {
        try {
          const fresh = await tasksApi.get(t.id);
          setTask(fresh);
          if (['succeeded', 'failed', 'cancelled'].includes(fresh.state)) {
            clearInterval(poll);
            streamRef.current?.close();
          }
        } catch {
          /* ignore transient */
        }
      }, 3000);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  async function cancel() {
    if (!task) return;
    try {
      const t = await tasksApi.cancel(task.id);
      setTask(t);
      streamRef.current?.close();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <>
      <div className="page-head"><h2>任务工作台</h2></div>
      <div className="card">
        <div className="field">
          <label htmlFor="goal">任务目标</label>
          <textarea
            id="goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="描述要完成的工程/调研任务"
          />
        </div>
        <button className="primary" onClick={() => void createTask()} disabled={loading || !goal.trim()}>
          {loading ? '创建中…' : '创建任务（幂等）'}
        </button>
        {error && <div className="error-text" role="alert">{error}</div>}
      </div>

      {task && (
        <div className="card">
          <div className="row spread">
            <strong>{task.goal}</strong>
            <span className="badge accent">{task.state}</span>
          </div>
          <div className="muted" style={{ margin: '0.4rem 0' }}>
            阶段 {task.stage} · 步数 {task.steps}/{task.max_steps} · 深度 {task.depth} · 幂等键 {task.idempotency_key}
          </div>
          {task.failure && <div className="notice danger">{task.failure}</div>}
          {!['succeeded', 'failed', 'cancelled'].includes(task.state) && (
            <button className="danger small" onClick={() => void cancel()}>取消任务</button>
          )}
          <h4>事件流（SSE）</h4>
          {events.length === 0 ? (
            <Spinner label="等待事件…" />
          ) : (
            <ul>
              {events.map((e, i) => (
                <li key={i}>
                  <code>{e.type}</code> · {new Date(e.at).toLocaleTimeString('zh-CN')}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
