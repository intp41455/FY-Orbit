import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { conversationsApi } from '../api/conversations';
import type { ConversationSummary } from '../api/types';
import { errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { ChatIcon } from '../components/chatui/ChatIcons';
import { EmptyState } from '../components/chatui/EmptyState';
import { Skeleton } from '../components/chatui/Skeleton';
import { StatusTag } from '../components/chatui/StatusTag';
import { ToastStack, useToasts } from '../components/chatui/Toasts';
import {
  HISTORY_VIEW_TTL_MS,
  clearHistoryView,
  loadHistoryView,
  readAlias,
  readArchived,
  saveChatEntry,
  saveHistoryView,
} from '../components/chatui/chatStorage';
import '../styles/pages/chat.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

interface Filter {
  q: string;
  domain: string;
  mode: string;
  archived: boolean;
}

const DEFAULT_FILTER: Filter = { q: '', domain: 'all', mode: 'all', archived: false };

/** 并发上限（后端事实：会话数只能逐条 get，批量接口不存在）。 */
const COUNT_CONCURRENCY = 6;
/** 超过这个数量改为按需加载（进入视口才取消息数）。 */
const COUNT_LAZY_THRESHOLD = 50;

type CountState = { status: 'pending' } | { status: 'done'; value: number } | { status: 'failed' };

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function groupOf(iso: string): 'today' | 'week' | 'earlier' {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return 'earlier';
  const today = startOfDay(new Date());
  if (t >= today) return 'today';
  if (t >= today - 7 * 24 * 60 * 60 * 1000) return 'week';
  return 'earlier';
}

const GROUP_META: Array<{ key: 'today' | 'week' | 'earlier'; title: string }> = [
  { key: 'today', title: '今天' },
  { key: 'week', title: '本周' },
  { key: 'earlier', title: '更早' },
];

export function HistoryPage() {
  const navigate = useNavigate();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>(DEFAULT_FILTER);
  const [aliasMap] = useState<Record<string, string>>(() => readAlias());
  const [archivedIds] = useState<string[]>(() => readArchived());
  const [counts, setCounts] = useState<Record<string, CountState>>({});
  const { toasts, push, dismiss } = useToasts();

  const queueRef = useRef<string[]>([]);
  const requestedRef = useRef<Set<string>>(new Set());
  const pumpingRef = useRef(false);
  const restoredRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const archivedSet = useMemo(() => new Set(archivedIds), [archivedIds]);

  /* ------------------------------------------------ 列表 */
  useEffect(() => {
    let alive = true;
    setLoading(true);
    conversationsApi
      .list()
      .then((res) => {
        if (!alive) return;
        setConversations(Array.isArray(res) ? res : []);
      })
      .catch((e) => {
        if (!alive) return;
        setError(errorMessage(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  /* ------------------------------------------------ 上下文保持（只存筛选+滚动位置） */
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const v = loadHistoryView();
    if (!v) return;
    if (Date.now() - v.ts > HISTORY_VIEW_TTL_MS) {
      clearHistoryView();
      push({ text: '原位置已失效', kind: 'warn', timeout: 5000 });
      return;
    }
    setFilter(v.filter);
    requestAnimationFrame(() => {
      window.scrollTo({ top: v.scrollTop, behavior: 'auto' });
    });
  }, [push]);

  const persist = useCallback(() => {
    saveHistoryView({ scrollTop: window.scrollY || 0, filter, ts: Date.now() });
  }, [filter]);

  // 筛选变化节流 200ms 写入
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(persist, 200);
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [persist]);

  useEffect(() => {
    const onHide = () => persist();
    window.addEventListener('pagehide', onHide);
    return () => window.removeEventListener('pagehide', onHide);
  }, [persist]);

  useEffect(
    () => () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    },
    [],
  );

  /* ------------------------------------------------ 消息数：懒加载 */
  const pumpCounts = useCallback(async () => {
    if (pumpingRef.current) return;
    pumpingRef.current = true;
    try {
      while (queueRef.current.length > 0) {
        const batch = queueRef.current.splice(0, COUNT_CONCURRENCY);
        const settled = await Promise.allSettled(batch.map((id) => conversationsApi.get(id)));
        setCounts((prev) => {
          const next = { ...prev };
          settled.forEach((r, i) => {
            const id = batch[i];
            if (r.status === 'fulfilled' && typeof r.value?.message_count === 'number') {
              next[id] = { status: 'done', value: r.value.message_count };
            } else {
              next[id] = { status: 'failed' };
            }
          });
          return next;
        });
      }
    } finally {
      pumpingRef.current = false;
    }
  }, []);

  const requestCount = useCallback(
    (ids: string[]) => {
      const fresh = ids.filter((id) => !requestedRef.current.has(id));
      if (fresh.length === 0) return;
      for (const id of fresh) requestedRef.current.add(id);
      queueRef.current.push(...fresh);
      setCounts((prev) => {
        const next = { ...prev };
        for (const id of fresh) next[id] = { status: 'pending' };
        return next;
      });
      void pumpCounts();
    },
    [pumpCounts],
  );

  /* ------------------------------------------------ 过滤与分组 */
  const visible = useMemo(() => {
    const q = filter.q.trim().toLowerCase();
    return conversations.filter((c) => {
      const isArchived = archivedSet.has(c.id);
      if (filter.archived ? !isArchived : isArchived) return false;
      if (filter.domain !== 'all' && c.domain !== filter.domain) return false;
      if (filter.mode !== 'all' && c.mode !== filter.mode) return false;
      if (!q) return true;
      const t = (c.title || '').toLowerCase();
      const a = (aliasMap[c.id] || '').toLowerCase();
      return t.includes(q) || a.includes(q);
    });
  }, [conversations, filter, archivedSet, aliasMap]);

  const groups = useMemo(() => {
    const map: Record<'today' | 'week' | 'earlier', ConversationSummary[]> = {
      today: [],
      week: [],
      earlier: [],
    };
    for (const c of visible) map[groupOf(c.updated_at)].push(c);
    for (const k of Object.keys(map) as Array<keyof typeof map>) {
      map[k].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    }
    return map;
  }, [visible]);

  const domains = useMemo(
    () => Array.from(new Set(conversations.map((c) => c.domain).filter(Boolean))).sort(),
    [conversations],
  );

  /* ------------------------------------------------ 触发消息数加载 */
  useEffect(() => {
    if (visible.length === 0) return;
    const ids = visible.map((c) => c.id);
    if (visible.length <= COUNT_LAZY_THRESHOLD) {
      requestCount(ids);
      return;
    }
    // >50 条改按需加载：进入视口的卡片才去取消息数
    if (typeof IntersectionObserver === 'undefined') {
      requestCount(ids);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      const ids2 = entries
        .filter((e) => e.isIntersecting)
        .map((e) => (e.target as HTMLElement).dataset.convId)
        .filter((x): x is string => Boolean(x));
      if (ids2.length > 0) requestCount(ids2);
    });
    for (const id of ids) {
      const el = document.querySelector(`[data-conv-id="${id}"]`);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [visible, requestCount]);

  /* ------------------------------------------------ 跳回对话 */
  function openConversation(c: ConversationSummary) {
    saveChatEntry({ from: '/history', convId: c.id, returnTo: '/history' });
    persist();
    navigate(`/chat?id=${encodeURIComponent(c.id)}`);
  }

  function displayTitle(c: ConversationSummary): string {
    const raw = c.title || '未命名';
    const alias = aliasMap[c.id];
    return alias ? `${alias}（原始：${raw}）` : raw;
  }

  const archivedCount = conversations.filter((c) => archivedSet.has(c.id)).length;

  function countCell(c: ConversationSummary) {
    const st = counts[c.id];
    if (!st || st.status === 'pending') {
      return (
        <StatusTag
          kind="verifying"
          text="获取中"
        />
      );
    }
    if (st.status === 'failed') {
      return (
        <>
          {/* 未取到就显示「—」并给出 title，绝不填 0 冒充 */}
          <span className="hist-count" title="未能获取">—</span>
          <StatusTag kind="failed" text="获取失败" />
        </>
      );
    }
    return <span className="hist-count">消息 {st.value} 条</span>;
  }

  return (
    <BaseBound surface="history">
      <div className="hist-shell">
        <div className="page-head">
          <h2>历史会话</h2>
          <span className="ui-hint">完整原文由后端保存；这里只列授权范围内的会话元数据。</span>
        </div>

        {error ? <div className="notice danger" role="alert">{error}</div> : null}

        <div className="hist-filters" role="group" aria-label="历史会话筛选">
          <label className="chatui-sr-only" htmlFor="hist-q">搜索历史会话</label>
          <input
            id="hist-q"
            className="ui-input hist-search"
            type="search"
            value={filter.q}
            placeholder="搜索标题或本机备注名"
            onChange={(e) => setFilter((f) => ({ ...f, q: e.target.value }))}
          />
          <label className="chatui-sr-only" htmlFor="hist-domain">域</label>
          <select
            id="hist-domain"
            className="ui-select hist-select"
            value={filter.domain}
            onChange={(e) => setFilter((f) => ({ ...f, domain: e.target.value }))}
          >
            <option value="all">全部域</option>
            {domains.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <label className="chatui-sr-only" htmlFor="hist-mode">模式</label>
          <select
            id="hist-mode"
            className="ui-select hist-select"
            value={filter.mode}
            onChange={(e) => setFilter((f) => ({ ...f, mode: e.target.value }))}
          >
            <option value="all">全部模式</option>
            <option value="listen">倾听</option>
            <option value="explore">探索</option>
          </select>
          {archivedCount > 0 ? (
            <button
              type="button"
              className="ui-chip hist-chip-more"
              aria-pressed={filter.archived}
              onClick={() => setFilter((f) => ({ ...f, archived: !f.archived }))}
            >
              <ChatIcon name="archive" size={16} />
              已归档（{archivedCount}）
            </button>
          ) : null}
        </div>

        {loading ? (
          <Skeleton rows={6} variant="card" label="正在加载历史会话" />
        ) : conversations.length === 0 ? (
          <EmptyState
            icon="history"
            title="还没有历史会话"
            hint="开始一段对话后，这里会按时间分组列出会话元数据。"
            action={
              <a className="ui-btn ui-btn--primary" href="/chat">
                <LineIcon name="chat" size={16} /> 去对话
              </a>
            }
          />
        ) : visible.length === 0 ? (
          <EmptyState
            icon="search"
            title="没有匹配的会话"
            hint={filter.q ? `没有匹配「${filter.q}」的历史会话。` : '当前筛选下没有会话。'}
            action={
              <button type="button" className="ui-btn" onClick={() => setFilter(DEFAULT_FILTER)}>
                清空筛选
              </button>
            }
          />
        ) : (
          GROUP_META.map((g) =>
            groups[g.key].length === 0 ? null : (
              <section className="hist-group" key={g.key} aria-label={g.title}>
                <div className="hist-group-hd">
                  <h3 className="hist-group-title">{g.title}</h3>
                  <span className="hist-group-count">{groups[g.key].length} 个会话</span>
                </div>
                <div className="hist-cards">
                  {groups[g.key].map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      data-conv-id={c.id}
                      className="card hist-card"
                      onClick={() => openConversation(c)}
                    >
                      <span className="hist-card-title">{displayTitle(c)}</span>
                      <span className="hist-card-meta">
                        <span>{c.domain}</span>
                        <span>模式 {c.mode}</span>
                        <span>{new Date(c.updated_at).toLocaleString('zh-CN')}</span>
                      </span>
                      {/* duration / 涉及对象：后端无字段 → 隐藏且不占位 */}
                      <span className="hist-field-missing" aria-hidden="true">无字段</span>
                      <span className="hist-card-tags">
                        {countCell(c)}
                        {archivedSet.has(c.id) ? <StatusTag kind="paused" text="已归档（仅本机）" /> : null}
                        {aliasMap[c.id] ? <span className="ui-hint">本机备注名</span> : null}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            ),
          )
        )}

        <ToastStack toasts={toasts} onDismiss={dismiss} />
      </div>
    </BaseBound>
  );
}
