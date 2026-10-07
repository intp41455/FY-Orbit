import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { conversationsApi } from '../api/conversations';
import { NetworkError, request } from '../api/client';
import { modelsApi, type ModelCatalogSummary } from '../api/models';
import { catalogApi } from '../api/catalog';
import type { KBDocument } from '../api/knowledge';
import type { ConversationSummary, Message, SkillInfo } from '../api/types';
import { errorMessage, Spinner } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { Composer, type CommandId, type ComposerHandle } from '../components/chatui/Composer';
import { ContextBar } from '../components/chatui/ContextBar';
import { MessageBubble, formatTime } from '../components/chatui/MessageBubble';
import { ContextMenu, type ContextMenuItem } from '../components/chatui/ContextMenu';
import { Modal } from '../components/chatui/Modal';
import { HoverActions } from '../components/chatui/HoverActions';
import { Skeleton } from '../components/chatui/Skeleton';
import { EmptyState } from '../components/chatui/EmptyState';
import { StatusTag } from '../components/chatui/StatusTag';
import { ToastStack, useToasts } from '../components/chatui/Toasts';
import { ChatIcon } from '../components/chatui/ChatIcons';
import {
  readAlias,
  readArchived,
  readCtxCollapsed,
  writeAlias,
  writeArchived,
  writeCtxCollapsed,
  loadChatEntry,
  clearChatEntry,
  type ChatEntryState,
} from '../components/chatui/chatStorage';
import '../styles/pages/chat.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

function newClientMessageId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `cm-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// P1 交互双件 · 任务一：定位栏摘要（前 40 字，压缩空白）。
function summarize(content: string): string {
  const oneLine = content.replace(/\s+/g, ' ').trim();
  return oneLine.length > 40 ? `${oneLine.slice(0, 40)}…` : oneLine;
}

function isNarrowNow(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return typeof window.matchMedia === 'function'
      ? window.matchMedia('(max-width: 860px)').matches
      : window.innerWidth <= 860;
  } catch {
    return false;
  }
}

/**
 * 包 C 页面内的跳转：本页单测在「无 Router 上下文」下渲染（ChatPage.test.tsx
 * 直接 render(<ChatPage />)），因此本页不使用 useNavigate/useSearchParams，
 * 改用 pushState + popstate 让 react-router 同步 location。
 */
function spaNavigate(to: string): void {
  try {
    window.history.pushState({}, '', to);
    window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
  } catch {
    window.location.assign(to);
  }
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 剪贴板被拒：如实告知，不伪造成功 */
  }
  return false;
}

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function conversationToText(c: ConversationSummary, msgs: Message[]): string {
  const lines = [
    `# ${c.title || '未命名会话'}`,
    `会话 ID：${c.id}`,
    `域：${c.domain} · 模式：${c.mode}`,
    '',
    ...msgs.map((m) => `[${m.role}] ${formatTime(m.created_at)} ${m.content}`),
  ];
  return lines.join('\n');
}

export function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [text, setText] = useState('');
  const [mode, setMode] = useState<'listen' | 'explore'>('listen');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  );

  // P1 交互双件 · 任务一：提问侧边定位（保持挂载，收起态用 hidden + aria-hidden）。
  const [qnavOpen, setQnavOpen] = useState<boolean>(() => !isNarrowNow());
  const [flashId, setFlashId] = useState<string | null>(null);
  const [inViewIds, setInViewIds] = useState<Set<string>>(() => new Set());
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userMessages = messages.filter((m) => m.role === 'user');

  // 窄屏形态由 CSS 决定；这里只保留一个布尔用于抽屉的焦点/语义（不卸载任何 DOM）。
  const [narrow, setNarrow] = useState<boolean>(() => isNarrowNow());
  const [convPanelOpen, setConvPanelOpen] = useState<boolean>(() => !isNarrowNow());

  const [convQuery, setConvQuery] = useState('');
  const [archivedIds, setArchivedIds] = useState<string[]>(() => readArchived());
  const [aliasMap, setAliasMap] = useState<Record<string, string>>(() => readAlias());
  const [showArchived, setShowArchived] = useState(false);

  const [refs, setRefs] = useState<KBDocument[]>([]);
  const [ctxCollapsed, setCtxCollapsed] = useState<boolean>(() => readCtxCollapsed());

  const [model, setModel] = useState<ModelCatalogSummary | null>(null);
  const [modelState, setModelState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [modelError, setModelError] = useState<string>('');
  const [skills, setSkills] = useState<SkillInfo[] | null>(null);
  const [skillsState, setSkillsState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [skillsError, setSkillsError] = useState<string>('');

  const [menu, setMenu] = useState<{ convId: string; x: number; y: number } | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [abortNote, setAbortNote] = useState<string | null>(null);
  const [entry, setEntry] = useState<ChatEntryState | null>(null);

  const { toasts, push, dismiss } = useToasts();
  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const qnavToggleRef = useRef<HTMLButtonElement>(null);
  const qnavCloseRef = useRef<HTMLButtonElement>(null);
  const qnavRef = useRef<HTMLElement>(null);

  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(max-width: 860px)');
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    // 老式 API 兜底
    const legacy = (e: MediaQueryListEvent) => setNarrow(e.matches);
    (mq as unknown as { addListener: (cb: (e: MediaQueryListEvent) => void) => void }).addListener(legacy);
    return () =>
      (mq as unknown as { removeListener: (cb: (e: MediaQueryListEvent) => void) => void }).removeListener(legacy);
  }, []);

  // 抽屉打开时焦点落在关闭按钮，关闭后交还 qnav-toggle。
  useEffect(() => {
    if (!narrow) return;
    if (qnavOpen) qnavCloseRef.current?.focus();
  }, [narrow, qnavOpen]);

  // 视口内提问标记：观察所有消息气泡，进入/离开视口时维护 inViewIds。
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      (entries) => {
        setInViewIds((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const id = (e.target as HTMLElement).dataset.messageId;
            if (!id) continue;
            if (e.isIntersecting) next.add(id);
            else next.delete(id);
          }
          return next;
        });
      },
      { threshold: 0.6 },
    );
    for (const el of document.querySelectorAll('[data-message-id]')) io.observe(el);
    return () => io.disconnect();
  }, [messages]);

  function jumpTo(id: string) {
    const el = document.getElementById(`msg-${id}`);
    el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    setFlashId(id);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlashId(null), 2000);
  }

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);

  /* ------------------------------------------------ 只读事实：模型 / 技能目录 */
  useEffect(() => {
    let alive = true;
    modelsApi
      .catalog(false)
      .then((s) => {
        if (!alive) return;
        setModel(s);
        setModelState('ready');
      })
      .catch((e) => {
        if (!alive) return;
        setModelState('failed');
        setModelError(errorMessage(e));
      });
    catalogApi
      .skills()
      .then((s) => {
        if (!alive) return;
        setSkills(Array.isArray(s) ? s : []);
        setSkillsState('ready');
      })
      .catch((e) => {
        if (!alive) return;
        setSkillsState('failed');
        setSkillsError(errorMessage(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  /* ------------------------------------------------ 会话列表 */
  async function loadList() {
    setLoadingList(true);
    try {
      const res = await conversationsApi.list();
      const list = Array.isArray(res) ? res : [];
      setConversations(list);
      if (list.length > 0) {
        setActiveId((prev) => {
          if (prev && list.some((c) => c.id === prev)) return prev;
          const urlId = new URLSearchParams(window.location.search).get('id');
          if (urlId && list.some((c) => c.id === urlId)) return urlId;
          const e = loadChatEntry();
          if (e && list.some((c) => c.id === e.convId)) return e.convId;
          return list[0].id;
        });
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => {
    // 来自 /history 的入口：只认 id，不认消息正文
    const e = loadChatEntry();
    if (e) setEntry(e);
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 入口里的 convId 已经不存在 → 丢弃并如实提示（不静默跳到别的会话）
  useEffect(() => {
    if (!entry || loadingList) return;
    if (!conversations.some((c) => c.id === entry.convId)) {
      clearChatEntry();
      setEntry(null);
      push({ text: '原位置已失效', kind: 'warn', timeout: 5000 });
    }
  }, [entry, conversations, loadingList, push]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    setLoadingMsgs(true);
    conversationsApi
      .messages(activeId)
      .then((res) => setMessages(Array.isArray(res) ? res : []))
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoadingMsgs(false));
  }, [activeId]);

  async function refreshMessages(convId: string) {
    setLoadingMsgs(true);
    setAbortNote(null);
    try {
      const res = await conversationsApi.messages(convId);
      setMessages(Array.isArray(res) ? res : []);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoadingMsgs(false);
    }
  }

  /* ------------------------------------------------ 发送（可中止） */
  async function send() {
    const content = text.trim();
    if (!content) return;
    if (!online) {
      setError('离线状态下不能发送消息。');
      return;
    }
    setSubmitting(true);
    setError(null);
    setAbortNote(null);
    const controller = new AbortController();
    abortRef.current = controller;
    const clientMessageId = newClientMessageId();
    try {
      let convId = activeId;
      if (!convId) {
        const created = await conversationsApi.create({ title: content.slice(0, 24), mode });
        convId = created.id;
        setActiveId(convId);
        setConversations((c) => [created, ...c]);
      }
      // 与 conversationsApi.postMessage 逐字一致，只是多带 signal 以便真实中止：
      // POST /api/conversations/{id}/messages + Idempotency-Key: client_message_id
      await request<Message>(`/api/conversations/${convId}/messages`, {
        method: 'POST',
        body: { content, client_message_id: clientMessageId, mode },
        idempotencyKey: clientMessageId,
        signal: controller.signal,
      });
      setText('');
      // 发送后重取（同一 controller，可一并中止）
      const res = await request<Message[]>(`/api/conversations/${convId}/messages`, {
        signal: controller.signal,
      });
      setMessages(Array.isArray(res) ? res : []);
    } catch (e) {
      if (e instanceof NetworkError && e.kind === 'aborted') {
        // 后端无 SSE 流式：只能中止本次请求，不写「已停止生成」
        setAbortNote('已取消本次请求。消息可能已送达，可点刷新确认。');
      } else if (e instanceof NetworkError && e.kind === 'offline') {
        setError('离线：消息未发送。');
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setSubmitting(false);
      abortRef.current = null;
    }
  }

  function stopGenerating() {
    abortRef.current?.abort();
  }

  /* ------------------------------------------------ 归档 / 别名（本机降级） */
  function setArchived(id: string, archived: boolean) {
    setArchivedIds((prev) => {
      const next = archived ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((x) => x !== id);
      writeArchived(next);
      return next;
    });
    if (archived) {
      const c = conversations.find((x) => x.id === id);
      const name = c ? displayTitle(c) : id;
      push({
        text: `已在本机归档「${name}」（仅本机标记，后端无归档接口）。`,
        kind: 'info',
        timeout: 5000,
        actionLabel: '撤销',
        onAction: () => setArchived(id, false),
      });
    }
  }

  function saveAlias(id: string, value: string) {
    setAliasMap((prev) => {
      const next = { ...prev };
      const v = value.trim();
      if (v) next[id] = v;
      else delete next[id];
      writeAlias(next);
      return next;
    });
    push({ text: '已保存本机备注名（后端标题未改动）。', timeout: 4000 });
  }

  const displayTitle = useCallback(
    (c: ConversationSummary) => {
      const raw = c.title || '未命名';
      const alias = aliasMap[c.id];
      return alias ? `${alias}（原始：${raw}）` : raw;
    },
    [aliasMap],
  );

  const archivedSet = useMemo(() => new Set(archivedIds), [archivedIds]);

  const filtered = useMemo(() => {
    const q = convQuery.trim().toLowerCase();
    return conversations.filter((c) => {
      if (showArchived ? !archivedSet.has(c.id) : archivedSet.has(c.id)) return false;
      if (!q) return true;
      const t = (c.title || '').toLowerCase();
      const a = (aliasMap[c.id] || '').toLowerCase();
      return t.includes(q) || a.includes(q);
    });
  }, [conversations, convQuery, showArchived, archivedSet, aliasMap]);

  const archivedCount = useMemo(
    () => conversations.filter((c) => archivedSet.has(c.id)).length,
    [conversations, archivedSet],
  );

  /* ------------------------------------------------ 右键菜单（无删除） */
  const menuItems = useMemo<ContextMenuItem[]>(() => {
    const c = conversations.find((x) => x.id === menu?.convId);
    if (!c) return [];
    const isArchived = archivedSet.has(c.id);
    return [
      { key: 'open', label: '继续对话', icon: <LineIcon name="chat" size={16} />, onSelect: () => setActiveId(c.id) },
      {
        key: 'copy',
        label: '复制全文',
        icon: <LineIcon name="copy" size={16} />,
        onSelect: () => {
          void conversationsApi
            .messages(c.id)
            .then(async (msgs) => {
              const list = Array.isArray(msgs) ? msgs : [];
              const ok = await copyText(conversationToText(c, list));
              push({
                text: ok ? `已复制全文（${list.length} 条消息）。` : '复制失败：浏览器未授权剪贴板。',
                kind: ok ? 'info' : 'warn',
              });
            })
            .catch((e) => push({ text: `复制失败：${errorMessage(e)}`, kind: 'error' }));
        },
      },
      {
        key: 'export',
        label: '导出会话',
        icon: <LineIcon name="download" size={16} />,
        onSelect: () => {
          void conversationsApi
            .messages(c.id)
            .then((msgs) => {
              const list = Array.isArray(msgs) ? msgs : [];
              downloadText(`${c.title || '会话'}-${c.id}.txt`, conversationToText(c, list));
              push({ text: `已导出 ${list.length} 条消息为文本文件。` });
            })
            .catch((e) => push({ text: `导出失败：${errorMessage(e)}`, kind: 'error' }));
        },
      },
      {
        key: 'rename',
        label: '重命名…',
        icon: <LineIcon name="edit" size={16} />,
        onSelect: () => setRenameId(c.id),
      },
      isArchived
        ? {
            key: 'unarchive',
            label: '取消归档',
            icon: <LineIcon name="play" size={16} />,
            onSelect: () => setArchived(c.id, false),
          }
        : {
            key: 'archive',
            label: '归档',
            icon: <ChatIcon name="archive" size={16} />,
            onSelect: () => setArchived(c.id, true),
          },
      {
        key: 'copyid',
        label: '复制会话 ID',
        icon: <LineIcon name="file" size={16} />,
        onSelect: () => {
          void copyText(c.id).then((ok) =>
            push({ text: ok ? '已复制会话 ID。' : '复制失败：浏览器未授权剪贴板。', kind: ok ? 'info' : 'warn' }),
          );
        },
      },
    ];
  }, [conversations, menu, archivedSet, push]);

  /* ------------------------------------------------ 命令（本地可执行） */
  function runCommand(id: CommandId) {
    if (id === 'listen' || id === 'explore') {
      setMode(id);
      push({ text: id === 'listen' ? '已切到倾听模式。' : '已切到探索模式。', timeout: 3000 });
      return;
    }
    if (id === 'new') {
      newConversation();
      return;
    }
    if (id === 'clearrefs') {
      setRefs([]);
      push({ text: '已清空引用（不会写任何永久屏蔽标记）。', timeout: 3000 });
      return;
    }
    if (id === 'skills') {
      spaNavigate('/skills');
      return;
    }
    if (id === 'export') {
      const c = conversations.find((x) => x.id === activeId);
      if (!c) {
        push({ text: '当前没有可导出的会话。', kind: 'warn' });
        return;
      }
      downloadText(`${c.title || '会话'}-${c.id}.txt`, conversationToText(c, messages));
      push({ text: `已导出 ${messages.length} 条消息为文本文件。` });
    }
  }

  function newConversation() {
    setActiveId(null);
    setMessages([]);
    setText('');
    setRefs([]);
    setError(null);
  }

  /* ------------------------------------------------ 引用跳转（真实 @ 引用） */
  function openRefDoc(doc: KBDocument) {
    spaNavigate(`/knowledge?doc=${encodeURIComponent(doc.id)}`);
    push({ text: `已在知识库打开，请搜索文档名：${doc.name}`, timeout: 6000 });
  }

  function quoteMessage(m: Message) {
    setText((prev) => {
      const who = m.role === 'user' ? '我' : '助手';
      const prefix = prev && !prev.endsWith('\n') ? '\n' : '';
      return `${prev}${prefix}> 引用${who}：${m.content}\n`;
    });
    composerRef.current?.focus();
  }

  function copyMessage(m: Message) {
    void copyText(m.content).then((ok) =>
      push({ text: ok ? '已复制这条消息。' : '复制失败：浏览器未授权剪贴板。', kind: ok ? 'info' : 'warn' }),
    );
  }

  /* ------------------------------------------------ 键位 */
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName?.toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || t?.isContentEditable === true;

      if (e.key === 'Escape') {
        // 三层优先级：浮层（联想 > 右键菜单 > 抽屉 > 模态）→ 中止提交 → 退出输入态
        if (suggestOpen) {
          // 浮层第一优先：关联想层（不清空草稿，不退出输入态）
          composerRef.current?.closeSuggest();
          return;
        }
        if (menu) {
          setMenu(null);
          return;
        }
        if (narrow && qnavOpen) {
          setQnavOpen(false);
          qnavToggleRef.current?.focus();
          return;
        }
        if (renameId) {
          setRenameId(null);
          return;
        }
        if (submitting) {
          stopGenerating();
          return;
        }
        const composerEl = composerRef.current?.element();
        if (composerEl && (t as unknown) === composerEl) composerRef.current?.blurComposer();
        return;
      }

      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (typing) return;
      if (e.key === '/') {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === '@') {
        e.preventDefault();
        composerRef.current?.openRefSuggest();
        return;
      }
      if (e.altKey && (e.key === 'n' || e.key === 'N')) {
        e.preventDefault();
        newConversation();
        return;
      }
      if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        if (filtered.length === 0) return;
        e.preventDefault();
        const idx = filtered.findIndex((c) => c.id === activeId);
        const nextIdx =
          e.key === 'ArrowDown'
            ? Math.min(filtered.length - 1, idx + 1)
            : Math.max(0, (idx < 0 ? 0 : idx) - 1);
        setActiveId(filtered[nextIdx].id);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestOpen, menu, narrow, qnavOpen, renameId, submitting, filtered, activeId]);

  const lastAssistantId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return messages[i].id;
    }
    return null;
  }, [messages]);

  const hasCtx =
    refs.length > 0 ||
    model !== null ||
    skills !== null ||
    modelState === 'failed' ||
    skillsState === 'failed';

  const activeConv = conversations.find((c) => c.id === activeId) ?? null;

  return (
    <BaseBound surface="chat">
      <div className="chat-shell">
        <div className="page-head chat-page-head">
          <h2>对话</h2>
          <div className="chat-page-head-tools">
            {entry ? (
              <a
                className="ui-btn ui-btn--sm chatui-entry-link"
                href={entry.returnTo}
                onClick={(e) => {
                  e.preventDefault();
                  setEntry(null);
                  clearChatEntry();
                  spaNavigate(entry.returnTo);
                }}
              >
                <LineIcon name="arrowRight" size={16} />
                来自历史会话 · 返回历史
              </a>
            ) : null}
            <label className="chatui-mode-label" htmlFor="chat-mode">模式</label>
            <select
              id="chat-mode"
              className="ui-select chatui-mode-select"
              value={mode}
              onChange={(e) => setMode(e.target.value as 'listen' | 'explore')}
            >
              <option value="listen">倾听（默认，不自动派发任务）</option>
              <option value="explore">探索（可委派专家）</option>
            </select>
            <button
              ref={qnavToggleRef}
              type="button"
              className="ui-btn ui-btn--sm"
              data-testid="qnav-toggle"
              aria-expanded={qnavOpen}
              aria-controls="chat-qnav"
              onClick={() => setQnavOpen((o) => !o)}
            >
              <LineIcon name="target" size={16} />
              {qnavOpen ? '收起定位' : '提问定位'}
            </button>
          </div>
        </div>

        {!online ? (
          <div className="notice warn chatui-offline" role="status">
            <ChatIcon name="send" size={16} />
            当前离线：消息无法发送，已保存的会话仍可查看。
          </div>
        ) : null}

        {error ? <div className="notice danger chatui-error" role="alert">{error}</div> : null}
        {abortNote ? (
          <div className="notice warn chatui-abort" role="status">
            <span>{abortNote}</span>
            <button
              type="button"
              className="ui-btn ui-btn--sm"
              onClick={() => activeId && void refreshMessages(activeId)}
            >
              <LineIcon name="refresh" size={16} /> 刷新消息
            </button>
          </div>
        ) : null}

        <div className={`chat-layout${qnavOpen ? ' qnav-open' : ''}`}>
          {/* ① 会话搜索 */}
          <div className="chat-search">
            <label className="chatui-sr-only" htmlFor="chat-conv-search">搜索会话</label>
            <input
              id="chat-conv-search"
              ref={searchRef}
              className="ui-input chatui-search-input"
              type="search"
              value={convQuery}
              placeholder="搜索会话（/ 聚焦）"
              onChange={(e) => setConvQuery(e.target.value)}
            />
          </div>

          {/* ② 会话列表（窄屏折叠成标题「会话（N）」的可展开面板，不消失） */}
          <section
            className={`ui-panel chat-convpanel${convPanelOpen ? '' : ' is-collapsed'}`}
            aria-label="会话列表"
          >
            <button
              type="button"
              className="chat-conv-toggle"
              aria-expanded={convPanelOpen}
              aria-controls="chat-conv-list"
              onClick={() => setConvPanelOpen((o) => !o)}
            >
              <LineIcon name="chevronDown" size={16} />
              会话（{filtered.length}）
            </button>

            <div className="chat-conv-body" id="chat-conv-list">
              {archivedCount > 0 ? (
                <div className="chat-archived-row">
                  <button
                    type="button"
                    className="ui-chip"
                    aria-pressed={showArchived}
                    onClick={() => setShowArchived((v) => !v)}
                  >
                    <ChatIcon name="archive" size={16} />
                    已归档（{archivedCount}）
                  </button>
                </div>
              ) : null}

              {loadingList ? (
                <Skeleton rows={3} variant="conv" label="正在加载会话列表" />
              ) : conversations.length === 0 ? (
                <EmptyState
                  icon="chat"
                  title="还没有会话"
                  hint="描述一件事就能开始。会自动带入上次使用的模式。"
                  action={
                    <button type="button" className="ui-btn ui-btn--primary" onClick={newConversation}>
                      <LineIcon name="plus" size={16} /> 开始第一个会话
                    </button>
                  }
                />
              ) : filtered.length === 0 ? (
                <EmptyState
                  icon="search"
                  title="没有匹配的会话"
                  hint={convQuery ? `没有匹配「${convQuery}」的会话。` : '当前筛选下没有会话。'}
                  action={
                    <button type="button" className="ui-btn" onClick={() => setConvQuery('')}>
                      清空搜索
                    </button>
                  }
                />
              ) : (
                <ul className="ui-scroll chat-convlist" role="list">
                  {filtered.map((c) => {
                    const isArchived = archivedSet.has(c.id);
                    return (
                      <li key={c.id} className="chat-conv-li">
                        <button
                          type="button"
                          className={`chat-conv-item${c.id === activeId ? ' is-active' : ''}`}
                          aria-current={c.id === activeId ? 'true' : undefined}
                          onClick={() => setActiveId(c.id)}
                          onContextMenu={(e) => {
                            e.preventDefault();
                            setMenu({ convId: c.id, x: e.clientX, y: e.clientY });
                          }}
                        >
                          <span className="chat-conv-title">{displayTitle(c)}</span>
                          <span className="chat-conv-meta">
                            {c.mode} · {new Date(c.updated_at).toLocaleString('zh-CN')}
                          </span>
                          {isArchived ? (
                            <span className="chat-conv-flag">
                              <StatusTag kind="paused" text="已归档（仅本机）" />
                            </span>
                          ) : null}
                          {aliasMap[c.id] ? <span className="chat-conv-flag chatui-hint">本机备注名</span> : null}
                        </button>
                        <HoverActions
                          actions={[
                            {
                              key: 'rename',
                              label: '重命名（本机备注）',
                              icon: <LineIcon name="edit" size={16} />,
                              onClick: () => setRenameId(c.id),
                            },
                            {
                              key: 'archive',
                              label: isArchived ? '取消归档' : '归档（仅本机）',
                              icon: isArchived ? <LineIcon name="play" size={16} /> : <ChatIcon name="archive" size={16} />,
                              onClick: () => setArchived(c.id, !isArchived),
                            },
                            {
                              key: 'more',
                              label: '更多操作',
                              icon: <LineIcon name="more" size={16} />,
                              onClick: (e) => {
                                const r = (e?.target as HTMLElement)?.getBoundingClientRect?.();
                                setMenu({ convId: c.id, x: r?.right ?? 240, y: r?.bottom ?? 240 });
                              },
                            },
                          ]}
                        />
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </section>

          {/* ③ 新建会话 */}
          <div className="chat-newrow">
            <button type="button" className="ui-btn ui-btn--block" onClick={newConversation}>
              <LineIcon name="plus" size={16} /> 新建会话（Alt+N）
            </button>
          </div>

          {/* ④ 消息列表 */}
          <section className="ui-panel chat-stream-panel" aria-label="消息列表">
            <div className="chat-stream-hd">
              <h3 className="ui-panel-title">
                {activeConv ? displayTitle(activeConv) : '新会话'}
              </h3>
              {activeConv ? (
                <span className="ui-panel-sub">
                  {messages.length} 条消息 · {activeConv.domain} · 模式 {activeConv.mode}
                </span>
              ) : (
                <span className="ui-panel-sub">发送第一条消息后即创建会话</span>
              )}
            </div>

            <div className="chat-scroll chat-stream" aria-live="polite" aria-relevant="additions text">
              {loadingMsgs ? (
                <Skeleton rows={4} variant="msg" label="正在加载消息" />
              ) : messages.length === 0 ? (
                activeId ? (
                  <EmptyState
                    icon="chat"
                    title="这个会话还没有消息"
                    hint="在下方输入第一句话；Enter 发送，Shift/⌘+Enter 换行。"
                  />
                ) : (
                  <EmptyState
                    icon="chat"
                    title="还没有会话"
                    hint="描述一件事就能开始。会自动带入上次使用的模式。"
                  />
                )
              ) : (
                messages.map((m) => (
                  <MessageBubble
                    key={m.id}
                    message={m}
                    flash={flashId === m.id}
                    refs={refs}
                    onCopy={copyMessage}
                    onQuote={quoteMessage}
                    onOpenRef={openRefDoc}
                    generating={submitting && m.id === lastAssistantId}
                    onStop={stopGenerating}
                  />
                ))
              )}
              {submitting && !lastAssistantId ? (
                <div className="chatui-generating chatui-generating--standalone">
                  <span className="ui-dot ui-dot--running chatui-dot" aria-hidden="true" />
                  <span className="chatui-generating-text">生成中…</span>
                  <button type="button" className="ui-btn ui-btn--sm chatui-stop-btn" onClick={stopGenerating}>
                    <LineIcon name="stop" size={16} /> 停止生成
                  </button>
                </div>
              ) : null}
            </div>
          </section>

          {/* ⑤ 上下文条（三项全无则不渲染） */}
          <div className="chat-ctx">
            {hasCtx ? (
              <ContextBar
                model={model}
                modelState={modelState}
                modelError={modelError}
                skills={skills}
                skillsState={skillsState}
                skillsError={skillsError}
                refs={refs}
                onRemoveRef={(docId) => setRefs((prev) => prev.filter((d) => d.id !== docId))}
                onClearRefs={() => {
                  setRefs([]);
                  setCtxCollapsed(true);
                  writeCtxCollapsed(true);
                }}
                collapsed={ctxCollapsed}
                onSetCollapsed={(v) => {
                  setCtxCollapsed(v);
                  writeCtxCollapsed(v);
                }}
              />
            ) : null}
          </div>

          {/* ⑥ composer ⑦ 发送 */}
          <div className="chatui-composer-area">
            <Composer
              ref={composerRef}
              value={text}
              onChange={setText}
              onSubmit={() => void send()}
              submitting={submitting}
              online={online}
              onCommand={runCommand}
              onPickRef={(doc) => setRefs((prev) => (prev.some((d) => d.id === doc.id) ? prev : [...prev, doc]))}
              onSuggestOpenChange={setSuggestOpen}
            />
            <div className="chatui-sendrow">
              <button
                type="button"
                className="ui-btn ui-btn--primary chatui-send"
                onClick={() => void send()}
                disabled={submitting || !text.trim()}
              >
                {submitting ? <Spinner label="发送中…" /> : (
                  <>
                    <ChatIcon name="send" size={16} /> 发送
                  </>
                )}
              </button>
              <span className="ui-hint chatui-sendhint">
                Enter 发送 · Shift/⌘+Enter 换行 · / 命令 · @ 引用
              </span>
            </div>
          </div>

          {/* ⑧ 定位栏（收起态 hidden + aria-hidden，保持挂载） */}
          <aside
            id="chat-qnav"
            ref={qnavRef}
            className={`card qnav chat-qnav${narrow ? ' is-drawer' : ''}`}
            data-testid="qnav"
            aria-label="提问定位"
            role={narrow ? 'dialog' : undefined}
            aria-modal={narrow ? true : undefined}
            hidden={!qnavOpen}
            aria-hidden={!qnavOpen}
          >
            <div className="chat-qnav-hd">
              <strong>提问定位</strong>
              <span className="muted small">{userMessages.length} 条</span>
              <button
                ref={qnavCloseRef}
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm chat-qnav-close"
                onClick={() => {
                  setQnavOpen(false);
                  qnavToggleRef.current?.focus();
                }}
              >
                <LineIcon name="close" size={16} /> 收起
              </button>
            </div>
            {userMessages.length === 0 ? (
              <div className="muted small chat-qnav-empty">当前会话还没有提问。</div>
            ) : (
              <div className="ui-scroll chat-qnav-body">
                {userMessages.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={`qnav-item${inViewIds.has(m.id) ? ' in-view' : ''}`}
                    data-testid="qnav-item"
                    onClick={() => jumpTo(m.id)}
                    title={m.content}
                  >
                    <span className="qnav-summary">{summarize(m.content)}</span>
                    <span className="qnav-time">{new Date(m.created_at).toLocaleTimeString('zh-CN')}</span>
                    {inViewIds.has(m.id) ? <span className="qnav-inview-tag">视口内</span> : null}
                  </button>
                ))}
              </div>
            )}
          </aside>
        </div>

        {menu ? (
          <ContextMenu
            x={menu.x}
            y={menu.y}
            items={menuItems}
            onClose={() => setMenu(null)}
            label={`会话 ${menu.convId} 的操作`}
          />
        ) : null}

        {renameId ? (
          <Modal
            title="重命名（仅本机备注）"
            initialValue={aliasMap[renameId] ?? ''}
            placeholder="输入本机备注名"
            hint={
              <>
                后端没有会话改名接口，这里只改本机显示（localStorage），展示为「别名（原始：原标题）」。
              </>
            }
            onCancel={() => setRenameId(null)}
            onConfirm={(v) => {
              saveAlias(renameId, v);
              setRenameId(null);
            }}
          />
        ) : null}

        <ToastStack toasts={toasts} onDismiss={dismiss} />
      </div>
    </BaseBound>
  );
}
