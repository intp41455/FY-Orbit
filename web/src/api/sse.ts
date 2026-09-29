// SSE client for /api/tasks/{id}/events (§5.2).
// Uses the browser EventSource which authenticates via the same-origin session
// cookie. We never put the token in the URL. Returns a close() handle.
import type { TaskEvent } from './types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export interface TaskEventStream {
  close: () => void;
}

export function openTaskEventStream(
  taskId: string,
  handlers: {
    onEvent: (e: TaskEvent) => void;
    onOpen?: () => void;
    onError: (err: Event) => void;
  },
): TaskEventStream {
  const url = `${API_BASE}/api/tasks/${taskId}/events`;
  const source = new EventSource(url, { withCredentials: true });

  const handler = (type: TaskEvent['type']) => (ev: MessageEvent) => {
    let data: TaskEvent;
    try {
      data = JSON.parse(ev.data) as TaskEvent;
    } catch {
      return;
    }
    if (data.type === type) handlers.onEvent(data);
  };

  source.addEventListener('stage', handler('stage'));
  source.addEventListener('status', handler('status'));
  source.addEventListener('artifact', handler('artifact'));
  source.addEventListener('error', (ev) => {
    // Protocol-level error event from the stream payload.
    handlers.onEvent({ type: 'error', task_id: taskId, code: 'stream_error', message: 'stream error', at: new Date().toISOString() });
    // Also surface the network error; EventSource auto-reconnects otherwise.
    handlers.onError(ev);
    source.close();
  });
  source.addEventListener('done', handler('done'));
  source.onopen = () => handlers.onOpen?.();
  source.onerror = (ev) => {
    handlers.onError(ev);
    // Do not auto-reconnect forever; leave it to the caller to reopen.
    source.close();
  };

  return {
    close: () => source.close(),
  };
}
