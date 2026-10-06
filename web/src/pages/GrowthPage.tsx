import { useState } from 'react';
import { memoryApi } from '../api/memory';
import type { MemoryHit } from '../api/types';
import { errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { ChatIcon } from '../components/chatui/ChatIcons';
import { EmptyState } from '../components/chatui/EmptyState';
import { Skeleton } from '../components/chatui/Skeleton';
import { StatusTag } from '../components/chatui/StatusTag';
import '../styles/pages/chat.css';

/** 证据边界 → 九档状态 + 图标 + 中文文字（颜色不是唯一通道） */
const BOUNDARY: Record<string, { kind: 'complete' | 'waiting' | 'verifying'; text: string }> = {
  fact: { kind: 'complete', text: '事实' },
  assumption: { kind: 'waiting', text: '假设' },
  hypothesis: { kind: 'verifying', text: '待验证假设' },
};

function boundaryOf(v: string) {
  return BOUNDARY[v] ?? { kind: 'external' as const, text: v || '未标注证据边界' };
}

export function GrowthPage() {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MemoryHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  async function search() {
    if (!q.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await memoryApi.search({ q: q.trim(), limit: 20 });
      setHits(Array.isArray(res?.hits) ? res.hits : []);
      setSearched(true);
    } catch (e) {
      setError(errorMessage(e));
      setHits([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="growth-shell">
      <div className="page-head">
        <h2>成长记录</h2>
        <span className="ui-hint">
          检索由服务端在授权范围内完成；假设/玄学/精神分析解释均带证据边界，不作为确定事实画像。
        </span>
      </div>

      {/* 阶段视图公告（后端事实：/api/memory 只有检索命中，没有阶段数据源） */}
      <div className="growth-notice" role="status">
        <ChatIcon name="clock" size={18} title="等待后端阶段数据源" />
        <span className="growth-notice-body">
          <strong>阶段视图未接入：</strong>
          阶段视图需要后端阶段数据源（当前 /api/memory 仅提供检索命中），本轮未接入。以下为已授权正式记忆的检索结果。
        </span>
      </div>

      <div className="growth-search">
        <label className="chatui-sr-only" htmlFor="mem-q">检索正式记忆</label>
        <input
          id="mem-q"
          className="ui-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void search();
            }
          }}
          placeholder="例如：反复出现的回避场景"
        />
        <button className="ui-btn ui-btn--primary" onClick={() => void search()} disabled={loading || !q.trim()}>
          {loading ? '检索中…' : <><LineIcon name="search" size={16} /> 检索</>}
        </button>
      </div>

      {error ? <div className="notice danger" role="alert">{error}</div> : null}

      {loading ? (
        <Skeleton rows={4} variant="card" label="正在检索正式记忆" />
      ) : !searched ? (
        <EmptyState
          icon="search"
          title="还没有检索正式记忆"
          hint="输入关键词检索已授权的正式记忆；命中会标注证据边界（事实 / 假设 / 待验证假设）。"
        />
      ) : hits.length === 0 ? (
        <EmptyState
          icon="database"
          title="没有命中已授权的正式记忆"
          hint={`「${q.trim()}」在已授权的正式记忆里没有命中。可以换个说法再试。`}
        />
      ) : (
        <div className="growth-hits">
          {hits.map((h) => {
            const b = boundaryOf(h.evidence_boundary);
            return (
              <article className="card growth-hit" key={`${h.record_id}-${h.version}`}>
                <div className="growth-hit-head">
                  <strong>{h.category || h.kind}</strong>
                  <StatusTag kind={b.kind} text={b.text} />
                </div>
                <div className="growth-hit-snippet">{h.snippet}</div>
                <div className="growth-hit-meta">
                  v{h.version} · {h.domain} · {new Date(h.created_at).toLocaleDateString('zh-CN')} · 来源{' '}
                  {h.sources.length} 条
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
