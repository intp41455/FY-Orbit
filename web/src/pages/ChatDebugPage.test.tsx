import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ChatDebugPage } from './ChatDebugPage';
import type { PromptTemplateSummary, ToolMeta } from '../api/chatDebug';

vi.mock('../api/chatDebug', async () => {
  const actual = await vi.importActual<typeof import('../api/chatDebug')>('../api/chatDebug');
  return {
    ...actual,
    chatDebugApi: {
      listPrompts: vi.fn(),
      discoverTools: vi.fn(),
    },
  };
});

import { chatDebugApi } from '../api/chatDebug';

const TEMPLATE: PromptTemplateSummary = {
  id: 'tpl-1',
  name: 'chat.debug.researcher',
  latest_version: 1,
  variables_schema: {
    topic: { type: 'str', required: true },
    depth: { type: 'int', required: false, default: 2 },
  },
  description: '调试模板',
  is_active: true,
  scope: 'platform',
};

const TOOL: ToolMeta = {
  name: 'add',
  description: '两数相加',
  parameters: { type: 'object', properties: { a: { type: 'number' }, b: { type: 'number' } } },
};

/** 构造一个按 P1-08/P1-19 帧协议回放的 SSE Response。 */
function sseResponse(frames: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const f of frames) controller.enqueue(enc.encode(f));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function renderPage(initialEntry = '/chat-debug') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ChatDebugPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(chatDebugApi.listPrompts).mockResolvedValue([TEMPLATE]);
  vi.mocked(chatDebugApi.discoverTools).mockResolvedValue({ count: 1, tools: [TOOL] });
});

describe('ChatDebugPage (P1-19)', () => {
  it('加载模板与工具并渲染配置区', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('template-select')).toHaveValue(''));
    expect(screen.getByTestId('tool-check-add')).not.toBeChecked();
    expect(screen.getByTestId('chat-trace-panel')).toHaveTextContent('未触发工具调用');
  });

  it('选择模板后按 schema 生成变量表单并应用默认值', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('template-select')).toBeEnabled());
    await user.selectOptions(screen.getByTestId('template-select'), TEMPLATE.name);
    expect(screen.getByTestId('var-input-topic')).toBeInTheDocument();
    expect(screen.getByTestId('var-input-depth')).toHaveValue(2);
  });

  it('试跑深链 ?template=&version=&variables= 自动填充', async () => {
    renderPage(
      `/chat-debug?template=${TEMPLATE.name}&version=1&variables=${encodeURIComponent(
        JSON.stringify({ topic: '心理画像' }),
      )}`,
    );
    await waitFor(() => expect(screen.getByTestId('template-select')).toHaveValue(TEMPLATE.name));
    await waitFor(() => expect(screen.getByTestId('var-input-topic')).toHaveValue('心理画像'));
  });

  it('发送消息：流式渲染 delta、展示工具 trace、message_start 徽标', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([
      'event: message_start\ndata: {"stream_id":"s1","model":"default","mode":"stub","template":{"name":"chat.debug.researcher","version":1,"content_hash":"c1","variables_hash":"abc123def456"}}\n\n',
      'event: delta\ndata: {"index":0,"text":"计算"}\n\n',
      'event: delta\ndata: {"index":1,"text":"结果 7"}\n\n',
      'event: tool_call\ndata: {"index":0,"name":"add","arguments":{"a":3,"b":4}}\n\n',
      'event: tool_result\ndata: {"index":0,"name":"add","call_id":"call-1","result":{"sum":7},"executed":true}\n\n',
      'event: message_end\ndata: {"stream_id":"s1","finish_reason":"stop","tools_used":[{"index":0,"name":"add","arguments":{"a":3,"b":4},"result":{"sum":7}}]}\n\n',
    ])));

    renderPage();
    await waitFor(() => expect(screen.getByTestId('template-select')).toBeEnabled());
    await user.type(screen.getByTestId('chat-input'), '调用 add 计算 3 和 4');
    await act(async () => { await user.click(screen.getByTestId('chat-send')); });

    await waitFor(() =>
      expect(screen.getByTestId('chat-msg-assistant')).toHaveTextContent('计算结果 7'));
    const entries = screen.getAllByTestId('chat-trace-entry');
    expect(entries).toHaveLength(1);
    expect(entries[0]).toHaveTextContent('add');
    expect(entries[0]).toHaveTextContent('{"a":3,"b":4}');
    expect(entries[0]).toHaveTextContent('{"sum":7}');
    expect(screen.getByTestId('badge-template')).toHaveTextContent('chat.debug.researcher v1');
  });

  it('SSE error 帧呈现为错误提示', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([
      'event: error\ndata: {"code":"stream_error","message":"上游失败"}\n\n',
      'event: message_end\ndata: {"stream_id":"s1","finish_reason":"error"}\n\n',
    ])));
    renderPage();
    await waitFor(() => expect(screen.getByTestId('template-select')).toBeEnabled());
    await user.type(screen.getByTestId('chat-input'), 'hi');
    await act(async () => { await user.click(screen.getByTestId('chat-send')); });
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('上游失败'));
  });
});
