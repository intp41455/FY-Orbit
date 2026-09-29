import { useState } from 'react';
import { memoryApi } from '../api/memory';
import type { MemoryHit } from '../api/types';
import { Spinner, errorMessage } from '../components/ui';

const BOUNDARY_LABEL: Record<string, string> = {
  fact: '事实',
  assumption: '假设',
  hypothesis: '待验证假设',
};

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
      setHits(res.hits);
      setSearched(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="page-head"><h2>成长记录</h2></div>
      <p className="muted">
        目标、偏好与假设分开呈现。检索由服务端在授权范围内完成；假设/玄学/精神分析解释均带证据边界，不作为确定事实画像。
      </p>
      <div className="card">
        <div className="field">
          <label htmlFor="mem-q">检索正式记忆</label>
          <div className="row">
            <input
              id="mem-q"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
              placeholder="例如：反复出现的回避场景"
            />
            <button className="primary" onClick={() => void search()} disabled={loading || !q.trim()}>
              {loading ? '检索中…' : '检索'}
            </button>
          </div>
        </div>
        {error && <div className="error-text" role="alert">{error}</div>}
        {searched && !loading && hits.length === 0 && (
          <div className="muted">没有命中已授权的正式记忆。</div>
        )}
        {hits.map((h) => (
          <div className="card" key={h.record_id + h.version} style={{ marginBottom: '0.5rem' }}>
            <div className="row spread">
              <strong>{h.category}</strong>
              <span className={`badge ${h.evidence_boundary === 'fact' ? 'ok' : 'warn'}`}>
                {BOUNDARY_LABEL[h.evidence_boundary] ?? h.evidence_boundary}
              </span>
            </div>
            <div style={{ margin: '0.4rem 0' }}>{h.snippet}</div>
            <div className="muted">
              v{h.version} · {h.domain} · {new Date(h.created_at).toLocaleDateString('zh-CN')} ·{' '}
              来源 {h.sources.length} 条
            </div>
          </div>
        ))}
        {loading && <Spinner />}
      </div>
    </>
  );
}
