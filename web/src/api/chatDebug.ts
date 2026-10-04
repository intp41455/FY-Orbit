// P1-19 Chat 调试预览 API surface.
// 模板库 (/api/prompts) + 工具注册表 (/api/tools) 读取，以及
// POST /api/streaming/chat 的 SSE 流式解析（EventSource 不支持 POST，
// 因此用 fetch + ReadableStream 手工解析 text/event-stream 帧协议）。
import { getCsrfToken, request } from './client';

export interface PromptTemplateSummary {
  id: string;
  name: string;
  latest_version: number;
  variables_schema: Record<string, { type?: string; required?: boolean; default?: unknown }>;
  description: string;
  is_active: boolean;
  scope: string;
}

export interface ToolMeta {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface TemplateMetaBadge {
  name: string;
  version: number;
  content_hash: string;
  variables_hash: string;
}

export interface ToolTraceEntry {
  index: number;
  name: string;
  arguments: Record<string, unknown>;
  call_id?: string;
  result?: unknown;
  executed?: boolean;
  executed_at?: string;
}

export interface StreamChatBody {
  prompt: string;
  model?: string;
  task_id?: string | null;
  template_name?: string | null;
  template_version?: number | null;
  variables?: Record<string, unknown>;
  tools?: string[];
}

export type StreamEventHandler = (event: string, data: Record<string, unknown>) => void;

/**
 * POST SSE：逐帧回调 event/data。返回 abort 句柄。
 * 帧协议与 P1-08/P1-19 一致：message_start / delta / tool_call /
 * tool_result / error / message_end，心跳为 ": heartbeat" 注释帧。
 */
export async function streamChat(body: StreamChatBody,
                                 onEvent: StreamEventHandler,
                                 signal?: AbortSignal): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const csrf = getCsrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;

  const res = await fetch('/api/streaming/chat', {
    method: 'POST',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = await res.json();
      if (parsed?.error?.message) detail = parsed.error.message;
    } catch { /* keep status fallback */ }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      handleFrame(frame, onEvent);
    }
  }
  if (buffer.trim()) handleFrame(buffer, onEvent);
}

function handleFrame(frame: string, onEvent: StreamEventHandler): void {
  let event = '';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue; // heartbeat comment
    if (line.startsWith('event: ')) event = line.slice(7).trim();
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
  }
  if (!event && dataLines.length === 0) return;
  let data: Record<string, unknown> = {};
  if (dataLines.length) {
    try { data = JSON.parse(dataLines.join('\n')); } catch { return; }
  }
  onEvent(event || 'message', data);
}

export const chatDebugApi = {
  listPrompts: () => request<PromptTemplateSummary[]>('/api/prompts'),
  discoverTools: () => request<{ count: number; tools: ToolMeta[] }>('/api/tools/discover'),
};
