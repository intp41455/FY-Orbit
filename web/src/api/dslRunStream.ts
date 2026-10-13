// N3 · G2 实时推送：受限 DSL 画布执行事件的 SSE 消费端。
//
// 与 `api/sse.ts` 同风格：浏览器原生 EventSource 走同源 cookie 鉴权
// （`withCredentials: true`），**绝不把 token 放进 URL**。命名事件不会触发
// `onmessage`，因此对每个事件类型单独 `addEventListener`。返回 `{ close }` 句柄。
//
// 事件契约（与后端 `routes/dsl_canvas.py` 冻结一致）：
//   run_started / node_started / node_finished / run_finished
// 另有 `: heartbeat` 注释帧（EventSource 不会派发，天然忽略）与可能出现的
// `gap` 事件（表示事件流有缺口，需全量重拉——本消费端暂不订阅，交由调用方
// 以 `getRun` 兜底）。
import type { DslRunLog } from './dslCanvas';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

/** `run_started`：后台执行已开始。 */
export interface DslRunStartedEvent {
  run_id: string;
  status: 'running';
}

/** `node_started`：某节点开始执行（实时进度的起点）。 */
export interface DslNodeStartedEvent {
  node_id: string;
  node_type: string;
  verb: string | null;
}

/** `node_finished`：某节点结束，载荷即后端 `NodeLog.to_dict()`（与 DslRunLog 同形）。 */
export type DslNodeFinishedEvent = DslRunLog;

/** `run_finished`：整轮执行结束（成功 / 失败 / 挂起）。 */
export interface DslRunFinishedEvent {
  run_id: string;
  status: 'succeeded' | 'failed' | 'suspended';
  output: unknown;
  error: string | null;
}

export interface DslRunStream {
  close: () => void;
}

export interface DslRunStreamHandlers {
  onRunStarted?: (e: DslRunStartedEvent) => void;
  onNodeStarted?: (e: DslNodeStartedEvent) => void;
  onNodeFinished?: (e: DslNodeFinishedEvent) => void;
  onRunFinished?: (e: DslRunFinishedEvent) => void;
  /** 协议 / 网络层错误（含服务端在 run_finished 后断开连接）。 */
  onError?: (err: Event) => void;
}

/**
 * 打开某次运行的实时事件流。调用方负责在组件卸载 / 重新执行时 `close()`。
 */
export function openDslRunStream(runId: string, handlers: DslRunStreamHandlers): DslRunStream {
  const url = `${API_BASE}/api/dsl-canvas/runs/${runId}/events`;
  const source = new EventSource(url, { withCredentials: true });

  const handler = <T>(fn?: (e: T) => void) => (ev: MessageEvent) => {
    let data: T;
    try {
      data = JSON.parse(ev.data) as T;
    } catch {
      return; // 非法 JSON 一律忽略（注释帧不会被派发到命名监听器）
    }
    fn?.(data);
  };

  source.addEventListener('run_started', handler(handlers.onRunStarted));
  source.addEventListener('node_started', handler(handlers.onNodeStarted));
  source.addEventListener('node_finished', handler(handlers.onNodeFinished));
  source.addEventListener('run_finished', handler(handlers.onRunFinished));

  source.onerror = (ev) => {
    handlers.onError?.(ev);
    // 不无限自动重连：是否重开 / 降级由调用方决定。
    source.close();
  };

  return { close: () => source.close() };
}
