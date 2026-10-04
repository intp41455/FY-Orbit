/**
 * W7 · Agent 通信总线 API 客户端。
 *
 * 只调用真实后端；不模拟成功、不复制演示数据。
 *
 * 两条硬规则：
 *  1. `from_identity` 是**服务端派生**的字段 —— 客户端根本没有发送它的入口
 *     （`send()` 的 body 里没有这个键），UI 只负责展示谁说的。
 *  2. 房间键格式由后端 `/api/bus/rooms/schema` 下发，这里不另立一套约定。
 */
import { request } from './client';

const BASE = '/api/bus';

export type BusMessageKind = 'text' | 'file_ref' | 'handoff' | 'system';
export type BusRoomKind = 'global' | 'task' | 'team' | 'dm';

export interface BusMessage {
  id: number;
  room: string;
  from_identity: string;
  kind: BusMessageKind;
  content: string;
  refs: string[];
  at: string;
  mention: string | null;
}

export interface BusContextItem {
  id: string;
  room: string;
  kind: 'file_ref' | 'text';
  title: string;
  ref: string;
  content: string;
  added_by: string;
  created_at: string | null;
}

export interface BusSendResult {
  message: BusMessage;
  triggered: string[];
  scheduled: boolean;
  auto_reply_enabled: boolean;
}

export interface BusMemberRef {
  role: string;
  title?: string;
}

function enc(room: string): string {
  return encodeURIComponent(room);
}

export const busApi = {
  list: (room: string, afterId = 0, limit = 200) =>
    request<{ room: string; kind: BusRoomKind; items: BusMessage[]; count: number; next_after_id: number }>(
      `${BASE}/${enc(room)}/messages`,
      { query: { after_id: afterId, limit } },
    ),

  /** 注意：body 里没有 from_identity —— 身份由服务端从会话推导。 */
  send: (
    room: string,
    body: { content: string; kind?: BusMessageKind; refs?: string[]; mention?: string | null; max_tokens?: number },
  ) => request<BusSendResult>(`${BASE}/${enc(room)}/messages`, { method: 'POST', body }),

  handoffs: (room: string) =>
    request<{ room: string; kind: BusRoomKind; edges: Record<string, number>; total: number }>(
      `${BASE}/${enc(room)}/handoffs`,
    ),

  context: (room: string) =>
    request<{ room: string; kind: BusRoomKind; items: BusContextItem[]; count: number }>(
      `${BASE}/${enc(room)}/context`,
    ),

  addContext: (room: string, entries: { kind: 'file_ref' | 'text'; title?: string; ref?: string; content?: string }[]) =>
    request<{ room: string; kind: BusRoomKind; items: BusContextItem[]; count: number }>(
      `${BASE}/${enc(room)}/context`,
      { method: 'POST', body: { entries } },
    ),

  /** SSE 地址。`maxEvents` 给定时只推这一批就关闭（一次性补拉 / 测试）。 */
  streamUrl: (room: string, afterId = 0, maxEvents = 0) =>
    `${BASE}/${enc(room)}/stream?after_id=${afterId}&max_events=${maxEvents}`,
};

/** 私聊房间键：两端身份排序，保证「A 找 B」和「B 找 A」同一房间（与后端一致）。 */
export function dmRoom(a: string, b: string): string {
  const [lo, hi] = [a, b].sort();
  return `dm:${lo}:${hi}`;
}

/** 剥掉身份前缀，得到可比对/可展示的名字。 */
export function identityKey(identity: string): string {
  const raw = identity ?? '';
  if (raw.startsWith('owner:')) return raw.slice('owner:'.length);
  if (raw.startsWith('agent:')) return raw.slice('agent:'.length);
  return raw;
}

/** 展示名：本人 → 我；团队成员 → 职务名（拿不到职务就显示角色 key）。 */
export function identityLabel(identity: string, members: BusMemberRef[] = []): string {
  if (!identity) return '未知';
  if (identity === 'system') return '系统';
  if (identity.startsWith('owner:')) return '我';
  const key = identityKey(identity);
  const hit = members.find((m) => m.role === key);
  return hit?.title || hit?.role || key;
}

/** 连线徽标计数：handoff 消息按「发送方 → 被点名方」聚合。 */
export function handoffEdgeCounts(messages: BusMessage[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const m of messages) {
    if (m.kind !== 'handoff' || !m.mention) continue;
    const key = `${identityKey(m.from_identity)}>${identityKey(m.mention)}`;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

export const KIND_LABEL: Record<BusMessageKind, string> = {
  text: '发言',
  file_ref: '文件引用',
  handoff: '交接',
  system: '系统',
};

/**
 * 订阅房间实时消息。优先 EventSource；环境不支持时退化为轮询补拉
 * （jsdom 没有 EventSource）。返回取消订阅函数。
 */
export function subscribeBus(
  room: string,
  onMessage: (m: BusMessage) => void,
  afterId = 0,
): () => void {
  if (typeof EventSource === 'function') {
    const es = new EventSource(busApi.streamUrl(room, afterId));
    es.addEventListener('message', (ev) => {
      try {
        onMessage(JSON.parse((ev as MessageEvent<string>).data) as BusMessage);
      } catch {
        /* 坏帧忽略：下一帧/下一次补拉会覆盖 */
      }
    });
    return () => es.close();
  }
  let cursor = afterId;
  const timer = setInterval(() => {
    void busApi
      .list(room, cursor)
      .then((r) => {
        cursor = r.next_after_id;
        r.items.forEach(onMessage);
      })
      .catch(() => {
        /* 轮询失败：下一次再试，不在这里假装成功 */
      });
  }, 3000);
  return () => clearInterval(timer);
}
