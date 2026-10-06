import { useMemo, useState } from 'react';
import { proposalsApi } from '../api/proposals';
import type { Proposal, ProposalStatus } from '../api/types';
import { useAsync, Spinner, errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { ConfirmDialog } from '../components/workbench/ConfirmDialog';
import './../styles/pages/workbench.css';

const STATUS_LABEL: Record<ProposalStatus, { text: string; tone: string; icon: Parameters<typeof LineIcon>[0]['name'] }> = {
  pending: { text: '待审批', tone: 'waiting', icon: 'clock' },
  approved_pending_execution: { text: '已批准，待外部执行', tone: 'running', icon: 'refresh' },
  executing: { text: '执行中', tone: 'running', icon: 'refresh' },
  executed: { text: '外部操作已执行', tone: 'complete', icon: 'check' },
  failed: { text: '执行失败', tone: 'failed', icon: 'xCircle' },
  unknown: { text: '结果待核对', tone: 'verifying', icon: 'shield' },
  rejected: { text: '已拒绝', tone: 'paused', icon: 'pause' },
  expired: { text: '已过期', tone: 'blocked', icon: 'alert' },
};

type FilterKey = 'all' | 'pending' | 'active' | 'done';

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '待我决策' },
  { key: 'active', label: '进行中' },
  { key: 'done', label: '已结束' },
];

function matchesFilter(status: ProposalStatus, filter: FilterKey): boolean {
  if (filter === 'all') return true;
  if (filter === 'pending') return status === 'pending';
  if (filter === 'active') return ['approved_pending_execution', 'executing', 'unknown'].includes(status);
  return ['executed', 'failed', 'rejected', 'expired'].includes(status);
}

// The UI must never claim "merged/released" before the external side effect
// has actually executed (FROZEN_CONTRACT §6.2, BUG-04). Only `executed` means
// the external operation completed.
function StatusBadge({ p }: { p: Proposal }) {
  const s = STATUS_LABEL[p.status];
  const mergedLike = p.operation === 'task.merge' || p.operation === 'task.release';
  const isDone = p.status === 'executed';
  return (
    <span className="appr-badge" data-tone={s.tone}>
      <LineIcon name={s.icon} size={14} />
      {s.text}
      {mergedLike && !isDone && '（尚未合并/发布）'}
    </span>
  );
}

function PrettyPayload({ p }: { p: Proposal }) {
  return (
    <div className="appr-payload">
      <div className="appr-payload-hd">
        <strong>载荷（精确差异）</strong>
      </div>
      <pre className="appr-payload-pre">{JSON.stringify(p.payload, null, 2)}</pre>
      <div className="appr-payload-grid">
        <div className="appr-field">
          <div className="muted small">目标 / 期望版本</div>
          <div>{p.target_id} @ v{p.expected_version}</div>
        </div>
        <div className="appr-field">
          <div className="muted small">Digest (SHA-256)</div>
          <code className="appr-digest">{p.digest}</code>
        </div>
        <div className="appr-field">
          <div className="muted small">回滚方案</div>
          <div>{p.rollback || '（无）'}</div>
        </div>
        <div className="appr-field">
          <div className="muted small">过期时间</div>
          <div>{new Date(p.expires_at).toLocaleString('zh-CN')}</div>
        </div>
        <div className="appr-field">
          <div className="muted small">测试证据 / Artifact</div>
          <div>evidence: {(p.evidence_ids ?? []).join(', ') || '无'}</div>
          <div>artifacts: {(p.artifact_ids ?? []).join(', ') || '无'}</div>
        </div>
        <div className="appr-field">
          <div className="muted small">外传 / 费用 / 环境</div>
          <div>外传域: {p.out_bound?.domains.join(', ') || '无'}</div>
          <div>
            费用: {p.cost_estimate ? `${p.cost_estimate.amount} ${p.cost_estimate.currency}` : '未知'}
          </div>
          <div>环境: {p.environment ?? '未指定'}</div>
        </div>
      </div>
    </div>
  );
}

export function ApprovalsPage() {
  const { data, loading, error, reload } = useAsync(() => proposalsApi.list(), []);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>('all');
  const [sortKey, setSortKey] = useState<'created' | 'expires'>('created');
  const [search, setSearch] = useState('');
  // 批准 armed（§6-armed）：点击后等 5s 才真正提交
  const [armedApprove, setArmedApprove] = useState<Proposal | null>(null);
  // 驳回模态
  const [rejectProposal, setRejectProposal] = useState<Proposal | null>(null);

  const filtered = useMemo(() => {
    if (!data) return [];
    let list = data.filter((p) => matchesFilter(p.status, filter));
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((p) =>
        p.operation.toLowerCase().includes(q) ||
        p.target_id.toLowerCase().includes(q) ||
        p.digest.toLowerCase().includes(q) ||
        p.reason.toLowerCase().includes(q),
      );
    }
    list.sort((a, b) => {
      if (sortKey === 'created') return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      return new Date(a.expires_at).getTime() - new Date(b.expires_at).getTime();
    });
    return list;
  }, [data, filter, sortKey, search]);

  async function decide(p: Proposal, decision: 'approve' | 'reject') {
    setDecidingId(p.id);
    setLocalError(null);
    try {
      // Re-send the exact digest the user reviewed (§6.1). Backend re-verifies
      // payload/version/expiry/owner.
      await proposalsApi.decide(p.id, {
        decision,
        digest: p.digest,
        expected_version: p.expected_version,
      });
      reload();
    } catch (e) {
      setLocalError(errorMessage(e));
    } finally {
      setDecidingId(null);
    }
  }

  return (
    <div className="appr-shell">
      <div className="page-head appr-page-head">
        <h2>审批中心</h2>
      </div>
      <p className="muted">
        只有所有者会话可决策。批准仅生成待执行许可；在外部操作真正完成前，状态不会显示"已合并/已发布"。
      </p>

      {/* 工具条（§1.4）：筛选 + 排序 + 搜索 */}
      <div className="appr-toolbar">
        <div className="appr-filter-chips" role="group" aria-label="状态筛选">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`appr-chip${filter === f.key ? ' is-active' : ''}`}
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="appr-sort-row">
          <span className="muted small">排序</span>
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as 'created' | 'expires')}>
            <option value="created">创建时间</option>
            <option value="expires">过期时间</option>
          </select>
        </label>
        <input
          type="search"
          className="appr-search"
          placeholder="搜索 operation / target / digest…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="搜索提案"
        />
      </div>

      {loading && <Spinner />}
      {error && <div className="ui-error-text" role="alert">{error}</div>}
      {localError && <div className="ui-error-text" role="alert">{localError}</div>}
      {filtered.length === 0 && !loading && (
        <div className="ui-empty">
          <LineIcon name="approvals" size={24} />
          <p className="ui-empty-title">没有匹配的提案</p>
          <p className="ui-empty-hint">试试切换筛选或清空搜索词。</p>
        </div>
      )}

      {filtered.map((p) => {
        // pending 卡默认展开，终结态默认折叠（§1.4）
        const expanded = p.status === 'pending';
        return (
          <div className={`appr-card${expanded ? ' is-expanded' : ''}`} key={p.id}>
            <div className="appr-card-hd">
              <div className="appr-card-summary">
                <strong>{p.operation}</strong>
                <span className="appr-target-badge">{p.target_id}</span>
                <code className="appr-digest-inline">{p.digest.slice(0, 12)}…</code>
              </div>
              <StatusBadge p={p} />
            </div>
            <div className="muted small appr-card-reason">理由：{p.reason}</div>
            <PrettyPayload p={p} />
            {p.status === 'pending' && (
              <div className="appr-card-actions">
                <button
                  type="button"
                  className="ui-btn ui-btn--sm ui-btn--primary"
                  disabled={decidingId === p.id || armedApprove !== null}
                  onClick={() => setArmedApprove(p)}
                >
                  {armedApprove?.id === p.id ? '准备批准…' : '批准（待执行）'}
                </button>
                <button
                  type="button"
                  className="ui-btn ui-btn--sm ui-btn--danger"
                  disabled={decidingId === p.id}
                  onClick={() => setRejectProposal(p)}
                >
                  驳回…
                </button>
              </div>
            )}
          </div>
        );
      })}

      {/* 批准 armed 模态（5s 冷静期 → 真正提交） */}
      {armedApprove && (
        <ConfirmDialog
          title="确认批准这个提案？"
          destructive={false}
          confirmLabel="批准（生成待执行许可）"
          body={
            <>
              即将批准 <code>{armedApprove.operation}</code> 针对{' '}
              <strong>{armedApprove.target_id}</strong> 的提案。
              批准后仅生成待执行许可，外部操作完成前不会显示「已合并/已发布」。
              <br />
              <span className="muted small">Digest: {armedApprove.digest}</span>
            </>
          }
          onConfirm={() => {
            const p = armedApprove;
            setArmedApprove(null);
            void decide(p, 'approve');
          }}
          onCancel={() => setArmedApprove(null)}
        />
      )}

      {/* 驳回模态 */}
      {rejectProposal && (
        <ConfirmDialog
          title="驳回这个提案？"
          destructive
          confirmLabel="确认驳回"
          body={
            <>
              即将驳回 <code>{rejectProposal.operation}</code> 针对{' '}
              <strong>{rejectProposal.target_id}</strong> 的提案。
              驳回后提案不可再批准，需重新发起。
            </>
          }
          onConfirm={() => {
            const p = rejectProposal;
            setRejectProposal(null);
            void decide(p, 'reject');
          }}
          onCancel={() => setRejectProposal(null)}
        />
      )}
    </div>
  );
}
