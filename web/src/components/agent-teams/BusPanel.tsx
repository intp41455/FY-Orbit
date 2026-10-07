import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  busApi,
  handoffEdgeCounts,
  identityKey,
  identityLabel,
  subscribeBus,
  KIND_LABEL,
  type BusContextItem,
  type BusMessage,
  type BusMemberRef,
} from '../../api/bus';
import { errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';

/**
 * W7 · 团队房间会话面板（挂在 TeamDesigner 侧栏）。
 *
 * 只读渲染真实消息流 + 一个输入框：以本人身份发言，点名成员即触发该成员经
 * 真实执行通道作答。点名失败/模型未配置时，后端会在房间里留下 system 说明，
 * 前端原样显示，不把「没人回答」包装成成功。
 */

export interface BusPanelProps {
  /** 房间键：团队房间 = team id；也接受 task id / `global` / `dm:a:b`。 */
  room: string;
  members: BusMemberRef[];
}

export function BusPanel({ room, members }: BusPanelProps) {
  useBase({ surface: 'web/src/components/agent-teams/BusPanel' });
  const [messages, setMessages] = useState<BusMessage[]>([]);
  const [context, setContext] = useState<BusContextItem[]>([]);
  const [edges, setEdges] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState('');
  const [kind, setKind] = useState<'text' | 'handoff'>('text');
  const [mention, setMention] = useState('');
  const [refs, setRefs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [ctxTitle, setCtxTitle] = useState('');
  const [ctxBody, setCtxBody] = useState('');

  const push = useCallback((incoming: BusMessage[]) => {
    setMessages((prev) => {
      const known = new Set(prev.map((m) => m.id));
      const add = incoming.filter((m) => !known.has(m.id));
      if (add.length === 0) return prev;
      return [...prev, ...add].sort((a, b) => a.id - b.id);
    });
  }, []);

  const refreshEdges = useCallback(async () => {
    try {
      const h = await busApi.handoffs(room);
      setEdges(h.edges);
    } catch {
      /* 计数拿不到就保持上一次的值，不在这里假装 0 */
    }
  }, [room]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const unsubscribe = subscribeBus(room, (m) => {
      push([m]);
      void refreshEdges();
    });
    void (async () => {
      try {
        const r = await busApi.list(room, 0);
        if (!cancelled) push(r.items);
        const c = await busApi.context(room);
        if (!cancelled) setContext(c.items);
        await refreshEdges();
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [room, push, refreshEdges]);

  const counts = useMemo(() => {
    const live = handoffEdgeCounts(messages);
    return Object.keys(live).length > 0 ? live : edges;
  }, [messages, edges]);

  async function send() {
    const content = draft.trim();
    if (!content) return;
    setBusy(true);
    setError(null);
    setNotice('');
    try {
      const out = await busApi.send(room, {
        content,
        kind,
        refs,
        mention: mention || null,
      });
      push([out.message]);
      setDraft('');
      setNotice(
        out.triggered.length > 0
          ? `已点名 ${out.triggered.join('、')}；回复在后台生成，会直接出现在这里。`
          : '已发送。',
      );
      if (kind === 'handoff') await refreshEdges();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function addContext() {
    const title = ctxTitle.trim();
    const body = ctxBody.trim();
    if (!title && !body) {
      setError('请填写标题或正文后再登记共享上下文。');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await busApi.addContext(room, [{ kind: 'text', title, content: body }]);
      setContext((prev) => [...prev, ...out.items]);
      setCtxTitle('');
      setCtxBody('');
      setNotice('已登记到共享上下文，发消息时可勾选引用。');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function toggleRef(id: string) {
    setRefs((prev) => (prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id]));
  }

  return (
    <div className="bus-panel">
      <div className="bus-head">
        <span className="badge">房间</span>
        <code className="bus-room">{room}</code>
      </div>

      {error && <div className="notice danger" role="alert">{error}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}

      {Object.keys(counts).length > 0 && (
        <div className="bus-edges">
          <span className="muted">交接计数（连线徽标）</span>
          <ul>
            {Object.entries(counts).map(([edge, n]) => (
              <li key={edge}>
                <span className="bus-edge">{edge.replace('>', ' → ')}</span>
                <span className="badge accent">×{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="bus-log" aria-label="房间消息流">
        {/* 加载失败时不说「还没有消息」：那会把越权/离线伪装成空房间 */}
        {messages.length === 0 && !loading && !error && (
          <li className="muted">这个房间还没有消息。</li>
        )}
        {loading && messages.length === 0 && <li className="muted" role="status">正在加载消息…</li>}
        {messages.map((m) => (
          <li key={m.id} className={`bus-msg bus-msg-${m.kind}`}>
            <div className="bus-msg-head">
              <span className="bus-who">{identityLabel(m.from_identity, members)}</span>
              <span className="bus-kind">{KIND_LABEL[m.kind] ?? m.kind}</span>
              {m.mention && (
                <span className="bus-mention">
                  @{identityLabel(m.mention, members)}
                </span>
              )}
              <span className="bus-seq">#{m.id}</span>
            </div>
            <p className="bus-body">{m.content}</p>
            {m.refs.length > 0 && (
              <p className="bus-refs">引用：{m.refs.join('、')}</p>
            )}
          </li>
        ))}
      </ul>

      <div className="bus-compose">
        <label htmlFor="bus-draft">以本人身份发言</label>
        <textarea
          id="bus-draft"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="例如：@编码专家 请把这个函数实现一下"
          rows={2}
        />

        <div className="bus-compose-row">
          <label htmlFor="bus-mention-select">
            点名成员
            <select
              id="bus-mention-select"
              value={mention}
              onChange={(e) => setMention(e.target.value)}
            >
              <option value="">（不点名）</option>
              {members.map((m) => (
                <option key={m.role} value={`agent:${m.role}`}>
                  {m.title || m.role}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="bus-kind">
            消息类型
            <select
              id="bus-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as 'text' | 'handoff')}
            >
              <option value="text">发言</option>
              <option value="handoff">交接</option>
            </select>
          </label>
          <button
            type="button"
            className="primary"
            disabled={busy || !draft.trim()}
            onClick={() => void send()}
          >
            发送
          </button>
        </div>

        {context.length > 0 && (
          <details>
            <summary>引用共享上下文（{context.length}）</summary>
            <ul className="bus-context">
              {context.map((c) => (
                <li key={c.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={refs.includes(c.id)}
                      onChange={() => toggleRef(c.id)}
                    />
                    {c.title || c.content.slice(0, 24) || c.id}
                  </label>
                  <span className="muted">{identityKey(c.added_by)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        <details>
          <summary>登记共享上下文</summary>
          <label htmlFor="bus-ctx-title">标题</label>
          <input
            id="bus-ctx-title"
            value={ctxTitle}
            onChange={(e) => setCtxTitle(e.target.value)}
            placeholder="例如：需求要点"
          />
          <label htmlFor="bus-ctx-body">文本片段</label>
          <textarea
            id="bus-ctx-body"
            value={ctxBody}
            onChange={(e) => setCtxBody(e.target.value)}
            rows={2}
          />
          <button type="button" className="small" disabled={busy} onClick={() => void addContext()}>
            登记
          </button>
        </details>
      </div>
    </div>
  );
}
