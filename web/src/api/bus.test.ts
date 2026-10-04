/**
 * W7 · Agent 通信总线：前端 API 层单测。
 *
 * 关注三件真实风险：
 *  1. 房间键与身份展示的纯函数不能和后端约定跑偏（dm 排序、前缀剥离）。
 *  2. 交接计数必须和后端 `handoffs` 端点同键（`a>b`，已剥前缀）。
 *  3. 订阅降级：没有 EventSource 时轮询补拉，取消后不再打接口。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./client', () => ({
  request: vi.fn(),
  onUnauthorized: vi.fn(),
  setCsrfToken: vi.fn(),
  getCsrfToken: vi.fn(),
  ApiError: class ApiError extends Error {},
  NetworkError: class NetworkError extends Error {},
}));

import { request } from './client';
import {
  busApi,
  dmRoom,
  handoffEdgeCounts,
  identityKey,
  identityLabel,
  subscribeBus,
  KIND_LABEL,
  type BusMessage,
} from './bus';

const req = request as unknown as ReturnType<typeof vi.fn>;

function msg(over: Partial<BusMessage> & { id: number }): BusMessage {
  return {
    room: 'team-1',
    from_identity: 'owner:o1',
    kind: 'text',
    content: '',
    refs: [],
    at: '2026-01-01T00:00:00Z',
    mention: null,
    ...over,
  };
}

const MEMBERS = [
  { role: 'coordinator', title: '协调官' },
  { role: 'implementer', title: '编码专家' },
];

afterEach(() => {
  req.mockReset();
  delete (globalThis as Record<string, unknown>).EventSource;
});

describe('bus.ts 房间键与身份', () => {
  it('dm 房间对两端排序，保证 A↔B 与 B↔A 同一房间', () => {
    expect(dmRoom('owner:o1', 'agent:implementer')).toBe(
      dmRoom('agent:implementer', 'owner:o1'),
    );
    expect(dmRoom('owner:o1', 'agent:implementer')).toBe('dm:agent:implementer:owner:o1');
  });

  it('identityKey 剥掉 owner: / agent: 前缀，其他原样返回', () => {
    expect(identityKey('owner:o1')).toBe('o1');
    expect(identityKey('agent:implementer')).toBe('implementer');
    expect(identityKey('system')).toBe('system');
  });

  it('identityLabel：本人→我，系统→系统，成员→职务名', () => {
    expect(identityLabel('owner:o1', MEMBERS)).toBe('我');
    expect(identityLabel('system', MEMBERS)).toBe('系统');
    expect(identityLabel('agent:implementer', MEMBERS)).toBe('编码专家');
    // 拿不到职务时退回角色 key，绝不显示成「未知成员」这类编造文案
    expect(identityLabel('agent:shadow', MEMBERS)).toBe('shadow');
  });

  it('KIND_LABEL 覆盖后端四种消息类型', () => {
    expect(Object.keys(KIND_LABEL).sort()).toEqual(['file_ref', 'handoff', 'system', 'text']);
  });
});

describe('handoffEdgeCounts', () => {
  it('只统计 handoff，按「发送方>被点名方」聚合且剥离身份前缀', () => {
    const counts = handoffEdgeCounts([
      msg({ id: 1, from_identity: 'owner:o1', kind: 'handoff', mention: 'agent:reviewer' }),
      msg({ id: 2, from_identity: 'owner:o1', kind: 'handoff', mention: 'agent:reviewer' }),
      msg({ id: 3, from_identity: 'agent:coordinator', kind: 'handoff', mention: 'agent:implementer' }),
      msg({ id: 4, from_identity: 'owner:o1', kind: 'text', mention: 'agent:reviewer' }), // 非 handoff：不计数
      msg({ id: 5, from_identity: 'owner:o1', kind: 'handoff', mention: null }), // 无被点名方：不计数
    ]);
    expect(counts).toEqual({ 'o1>reviewer': 2, 'coordinator>implementer': 1 });
  });

  it('空消息流返回空对象（不臆造连线）', () => {
    expect(handoffEdgeCounts([])).toEqual({});
  });
});

describe('subscribeBus', () => {
  it('无 EventSource 时退化为轮询补拉，取消后不再请求', async () => {
    delete (globalThis as Record<string, unknown>).EventSource;
    req.mockResolvedValue({ room: 'team-1', kind: 'team', items: [], count: 0, next_after_id: 0 });
    vi.useFakeTimers();

    const un = subscribeBus('team-1', () => {}, 0);
    await vi.advanceTimersByTimeAsync(3_100);
    expect(req).toHaveBeenCalledTimes(1);
    expect(req.mock.calls[0][0]).toBe('/api/bus/team-1/messages');

    await vi.advanceTimersByTimeAsync(6_000);
    expect(req).toHaveBeenCalledTimes(3);

    un();
    await vi.advanceTimersByTimeAsync(9_000);
    expect(req).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it('有 EventSource 时订阅 SSE 并解析 message 帧', () => {
    class FakeEventSource {
      static last: FakeEventSource | null = null;
      closed = false;
      listeners: Record<string, ((ev: unknown) => void)[]> = {};
      constructor(public url: string) {
        FakeEventSource.last = this;
      }
      addEventListener(type: string, cb: (ev: unknown) => void) {
        (this.listeners[type] ??= []).push(cb);
      }
      close() {
        this.closed = true;
      }
    }
    (globalThis as Record<string, unknown>).EventSource = FakeEventSource;

    const seen: BusMessage[] = [];
    const un = subscribeBus('team-1', (m) => seen.push(m), 5);
    const es = FakeEventSource.last!;
    expect(es.url).toBe('/api/bus/team-1/stream?after_id=5&max_events=0');

    const payload = msg({ id: 9, from_identity: 'agent:implementer', content: '收到' });
    es.listeners.message.forEach((cb) => cb({ data: JSON.stringify(payload) }));
    expect(seen).toHaveLength(1);
    expect(seen[0].content).toBe('收到');

    un();
    expect(es.closed).toBe(true);
  });

  it('busApi.send 的请求体里不含 from_identity（身份由服务端派生）', async () => {
    req.mockResolvedValue({
      message: msg({ id: 1, content: 'hi' }),
      triggered: [],
      scheduled: false,
      auto_reply_enabled: true,
    });
    await busApi.send('team-1', { content: 'hi', kind: 'text', mention: 'agent:implementer' });
    const body = req.mock.calls[0][1]?.body as Record<string, unknown>;
    expect(body).toBeDefined();
    expect(Object.keys(body)).not.toContain('from_identity');
    expect(body.mention).toBe('agent:implementer');
  });
});
