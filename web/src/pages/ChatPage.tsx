import { useEffect, useRef, useState } from 'react';
import { conversationsApi } from '../api/conversations';
import { NetworkError } from '../api/client';
import type { ConversationSummary, Message } from '../api/types';
import { errorMessage, Spinner } from '../components/ui';

function newClientMessageId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `cm-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// P1 交互双件 · 任务一：定位栏摘要（前 40 字，压缩空白）。
function summarize(content: string): string {
  const oneLine = content.replace(/\s+/g, ' ').trim();
  return oneLine.length > 40 ? `${oneLine.slice(0, 40)}…` : oneLine;
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
  const listRef = useRef<HTMLDivElement>(null);
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  );

  // P1 交互双件 · 任务一：提问侧边定位。
  const [qnavOpen, setQnavOpen] = useState(true);
  // 点击定位项后 2 秒高亮对应消息气泡（msg-flash）。
  const [flashId, setFlashId] = useState<string | null>(null);
  // 当前视口内的 user 消息 id（IntersectionObserver 维护）。
  const [inViewIds, setInViewIds] = useState<Set<string>>(() => new Set());
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userMessages = messages.filter((m) => m.role === 'user');

  useEffect(() => () => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
  }, []);

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

  async function loadList() {
    setLoadingList(true);
    try {
      const res = await conversationsApi.list();
      const list = Array.isArray(res) ? res : [];
      setConversations(list);
      if (!activeId && list.length > 0) setActiveId(list[0].id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoadingList(false);
    }
  }

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  async function send() {
    const content = text.trim();
    if (!content) return;
    if (!online) {
      setError('离线状态下不能发送消息。');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      let convId = activeId;
      if (!convId) {
        const created = await conversationsApi.create({ title: content.slice(0, 24), mode });
        convId = created.id;
        setActiveId(convId);
        setConversations((c) => [created, ...c]);
      }
      await conversationsApi.postMessage(convId, {
        content,
        client_message_id: newClientMessageId(),
        mode,
      });
      setText('');
      const res = await conversationsApi.messages(convId);
      setMessages(Array.isArray(res) ? res : []);
    } catch (e) {
      if (e instanceof NetworkError && e.kind === 'offline') setError('离线：消息未发送。');
      else setError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h2>对话</h2>
        <div className="row">
          <label htmlFor="mode" style={{ margin: 0 }}>模式</label>
          <select
            id="mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as 'listen' | 'explore')}
            style={{ width: 'auto' }}
          >
            <option value="listen">倾听（默认，不自动派发任务）</option>
            <option value="explore">探索（可委派专家）</option>
          </select>
          <button
            type="button"
            className="small"
            data-testid="qnav-toggle"
            aria-expanded={qnavOpen}
            aria-controls="chat-qnav"
            onClick={() => setQnavOpen((o) => !o)}
          >
            {qnavOpen ? '收起定位' : '提问定位'}
          </button>
        </div>
      </div>

      <div className={`grid cols-2 chat-layout${qnavOpen ? ' qnav-open' : ''}`}>
        <div className="card" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <div className="row spread" style={{ marginBottom: '0.5rem' }}>
            <strong>会话</strong>
            <button className="small" onClick={() => setActiveId(null)}>+ 新对话</button>
          </div>
          {loadingList ? (
            <Spinner />
          ) : (
            conversations.map((c) => (
              <div
                key={c.id}
                onClick={() => setActiveId(c.id)}
                style={{
                  padding: '0.4rem',
                  cursor: 'pointer',
                  borderRadius: 8,
                  background: c.id === activeId ? 'var(--bg-elev-2)' : 'transparent',
                }}
              >
                <div style={{ fontSize: '0.9rem' }}>{c.title || '(未命名)'}</div>
                <div className="muted" style={{ fontSize: '0.75rem' }}>{c.mode}</div>
              </div>
            ))
          )}
        </div>

        <div className="card" ref={listRef}>
          {loadingMsgs ? (
            <Spinner />
          ) : (
            <div className="chat-scroll" aria-live="polite">
              {messages.length === 0 && (
                <div className="muted">还没有消息。开始一段对话吧（原文完整保存于后端）。</div>
              )}
              {messages.map((m) => (
                <div
                  key={m.id}
                  id={`msg-${m.id}`}
                  data-message-id={m.id}
                  className={`bubble ${m.role}${flashId === m.id ? ' msg-flash' : ''}`}
                >
                  <div>{m.content}</div>
                  <div className="meta">
                    {m.role} · {new Date(m.created_at).toLocaleString('zh-CN')}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="chat-composer">
            <textarea
              aria-label="消息内容"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={online ? '说点什么…' : '离线：无法发送'}
              disabled={!online}
            />
            <button className="primary" onClick={() => void send()} disabled={submitting || !text.trim() || !online}>
              {submitting ? '发送中…' : '发送'}
            </button>
          </div>
          {error && <div className="error-text" role="alert">{error}</div>}
        </div>

        {qnavOpen && (
          <aside id="chat-qnav" className="card qnav" data-testid="qnav" aria-label="提问定位">
            <div className="row spread" style={{ marginBottom: '0.25rem' }}>
              <strong>提问定位</strong>
              <span className="muted small">{userMessages.length} 条</span>
            </div>
            {userMessages.length === 0 && (
              <div className="muted small">当前会话还没有提问。</div>
            )}
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
                <span className="qnav-time">
                  {new Date(m.created_at).toLocaleTimeString('zh-CN')}
                </span>
                {inViewIds.has(m.id) && <span className="qnav-inview-tag">视口内</span>}
              </button>
            ))}
          </aside>
        )}
      </div>
    </>
  );
}
