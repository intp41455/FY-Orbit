/**
 * 包 C · 本地降级存储（chatui 私有）
 * ---------------------------------------------------------------------------
 * 后端事实：无 DELETE / PATCH / archive 接口（见后端事实核查）。
 * 因此「归档」与「重命名」只能降级为**本机标记**，不伪装成服务端结果：
 *   - 归档：fy.chat.archived.v1 → 会话 id 数组；徽标必须写明「仅本机」。
 *   - 重命名：fy.chat.alias.v1 → { [convId]: 别名 }；展示为「别名（原始：原标题）」。
 *   - 上下文条折叠：fy.chat.ctxbar.v1 → { collapsed: boolean }（只记折叠，不写永久屏蔽）。
 * 上下文保持（只存 id / 筛选 / 滚动位置，**绝不存消息正文**）：
 *   - fy:history:view.v1 = { scrollTop, filter:{q,domain,mode,archived}, ts }
 *   - fy:chat:entry.v1   = { from, convId, returnTo }
 */

export const LS_ARCHIVED = 'fy.chat.archived.v1';
export const LS_ALIAS = 'fy.chat.alias.v1';
export const LS_CTXBAR = 'fy.chat.ctxbar.v1';
export const SS_HISTORY_VIEW = 'fy:history:view.v1';
export const SS_CHAT_ENTRY = 'fy:chat:entry.v1';

/** 超过 30 分钟的历史位置视为失效。 */
export const HISTORY_VIEW_TTL_MS = 30 * 60 * 1000;

function ls(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}
function ss(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

function readJson<T>(store: Storage | null, key: string, fallback: T): T {
  if (!store) return fallback;
  try {
    const raw = store.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(store: Storage | null, key: string, value: unknown): void {
  if (!store) return;
  try {
    store.setItem(key, JSON.stringify(value));
  } catch {
    /* 配额/隐私模式：静默失败，不影响主流程 */
  }
}

function remove(store: Storage | null, key: string): void {
  if (!store) return;
  try {
    store.removeItem(key);
  } catch {
    /* ignore */
  }
}

/* ------------------------------------------------------------ 归档（本机） */
export function readArchived(): string[] {
  const v = readJson<unknown>(ls(), LS_ARCHIVED, []);
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}
export function writeArchived(ids: string[]): void {
  writeJson(ls(), LS_ARCHIVED, ids);
}

/* ------------------------------------------------------------ 别名（本机） */
export function readAlias(): Record<string, string> {
  const v = readJson<unknown>(ls(), LS_ALIAS, {});
  if (!v || typeof v !== 'object') return {};
  const out: Record<string, string> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (typeof val === 'string' && val.trim()) out[k] = val;
  }
  return out;
}
export function writeAlias(next: Record<string, string>): void {
  writeJson(ls(), LS_ALIAS, next);
}

/* ------------------------------------------------------------ 上下文条折叠 */
export function readCtxCollapsed(): boolean {
  const v = readJson<{ collapsed?: unknown }>(ls(), LS_CTXBAR, {});
  return v.collapsed === true;
}
export function writeCtxCollapsed(collapsed: boolean): void {
  writeJson(ls(), LS_CTXBAR, { collapsed });
}

/* ------------------------------------------------------------ 历史位置 */
export interface HistoryViewState {
  scrollTop: number;
  filter: { q: string; domain: string; mode: string; archived: boolean };
  ts: number;
}

export function saveHistoryView(s: HistoryViewState): void {
  // 只存 id/筛选/滚动位置 —— 绝不写消息正文。
  writeJson(ss(), SS_HISTORY_VIEW, s);
}
export function loadHistoryView(): HistoryViewState | null {
  const v = readJson<Partial<HistoryViewState> | null>(ss(), SS_HISTORY_VIEW, null);
  if (!v || typeof v !== 'object') return null;
  if (typeof v.ts !== 'number' || typeof v.scrollTop !== 'number') return null;
  const f = (v.filter ?? {}) as Partial<HistoryViewState['filter']>;
  return {
    scrollTop: v.scrollTop,
    ts: v.ts,
    filter: {
      q: typeof f.q === 'string' ? f.q : '',
      domain: typeof f.domain === 'string' ? f.domain : 'all',
      mode: typeof f.mode === 'string' ? f.mode : 'all',
      archived: f.archived === true,
    },
  };
}
export function clearHistoryView(): void {
  remove(ss(), SS_HISTORY_VIEW);
}

/* ------------------------------------------------------------ 入口回跳 */
export interface ChatEntryState {
  from: string;
  convId: string;
  returnTo: string;
}

export function saveChatEntry(s: ChatEntryState): void {
  writeJson(ss(), SS_CHAT_ENTRY, s);
}
export function loadChatEntry(): ChatEntryState | null {
  const v = readJson<Partial<ChatEntryState> | null>(ss(), SS_CHAT_ENTRY, null);
  if (!v || typeof v !== 'object') return null;
  if (typeof v.convId !== 'string' || !v.convId) return null;
  return {
    from: typeof v.from === 'string' ? v.from : '/history',
    convId: v.convId,
    returnTo: typeof v.returnTo === 'string' ? v.returnTo : '/history',
  };
}
export function clearChatEntry(): void {
  remove(ss(), SS_CHAT_ENTRY);
}
