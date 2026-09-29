import { useState } from 'react';
import { proposalsApi } from '../api/proposals';
import type { Proposal, ProposalStatus } from '../api/types';
import { useAsync, Spinner, errorMessage } from '../components/ui';

const STATUS_LABEL: Record<ProposalStatus, { text: string; cls: string }> = {
  pending: { text: '待审批', cls: 'warn' },
  approved_pending_execution: { text: '已批准，待外部执行', cls: 'accent' },
  executing: { text: '执行中', cls: 'accent' },
  executed: { text: '外部操作已执行', cls: 'ok' },
  failed: { text: '执行失败', cls: 'danger' },
  unknown: { text: '结果待核对', cls: 'warn' },
  rejected: { text: '已拒绝', cls: 'danger' },
  expired: { text: '已过期', cls: 'danger' },
};

// The UI must never claim "merged/released" before the external side effect
// has actually executed (FROZEN_CONTRACT §6.2, BUG-04). Only `executed` means
// the external operation completed.
function statusBadge(p: Proposal) {
  const s = STATUS_LABEL[p.status];
  const mergedLike = p.operation === 'task.merge' || p.operation === 'task.release';
  const isDone = p.status === 'executed';
  return (
    <span className={`badge ${s.cls}`}>
      {s.text}
      {mergedLike && !isDone && '（尚未合并/发布）'}
    </span>
  );
}

function PrettyPayload({ p }: { p: Proposal }) {
  return (
    <div>
      <div className="row spread"><strong>载荷（精确差异）</strong></div>
      <pre>{JSON.stringify(p.payload, null, 2)}</pre>
      <div className="grid cols-2" style={{ marginTop: '0.5rem' }}>
        <div className="card">
          <div className="muted">目标 / 期望版本</div>
          <div>{p.target_id} @ v{p.expected_version}</div>
        </div>
        <div className="card">
          <div className="muted">Digest (SHA-256)</div>
          <code style={{ fontSize: '0.72rem' }}>{p.digest}</code>
        </div>
        <div className="card">
          <div className="muted">回滚方案</div>
          <div>{p.rollback || '（无）'}</div>
        </div>
        <div className="card">
          <div className="muted">过期时间</div>
          <div>{new Date(p.expires_at).toLocaleString('zh-CN')}</div>
        </div>
        <div className="card">
          <div className="muted">测试证据 / Artifact</div>
          <div>evidence: {p.evidence_ids.join(', ') || '无'}</div>
          <div>artifacts: {p.artifact_ids.join(', ') || '无'}</div>
        </div>
        <div className="card">
          <div className="muted">外传 / 费用 / 环境</div>
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
    <>
      <div className="page-head"><h2>审批中心</h2></div>
      <p className="muted">
        只有所有者会话可决策。批准仅生成待执行许可；在外部操作真正完成前，状态不会显示“已合并/已发布”。
      </p>
      {loading && <Spinner />}
      {error && <div className="notice danger" role="alert">{error}</div>}
      {localError && <div className="error-text" role="alert">{localError}</div>}
      {data && data.proposals.length === 0 && <div className="muted">暂无提案。</div>}
      {data &&
        data.proposals.map((p) => (
          <div className="card" key={p.id}>
            <div className="row spread">
              <div>
                <strong>{p.operation}</strong> <span className="badge">{p.target_id}</span>
              </div>
              {statusBadge(p)}
            </div>
            <div className="muted" style={{ margin: '0.3rem 0' }}>理由：{p.reason}</div>
            <PrettyPayload p={p} />
            {p.status === 'pending' && (
              <div className="row" style={{ marginTop: '0.6rem' }}>
                <button className="primary" disabled={decidingId === p.id} onClick={() => void decide(p, 'approve')}>
                  {decidingId === p.id ? '处理中…' : '批准（待执行）'}
                </button>
                <button className="danger" disabled={decidingId === p.id} onClick={() => void decide(p, 'reject')}>
                  拒绝
                </button>
              </div>
            )}
          </div>
        ))}
    </>
  );
}
