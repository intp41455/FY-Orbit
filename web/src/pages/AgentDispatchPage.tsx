import { useState } from 'react';
import {
  agentDispatchApi,
  type AcceptanceType,
  type ParentTrace,
} from '../api/agentDispatch';

/** P1-20 子 Agent 派发验证页面：发起派发 + 父/子 trace 树展示。 */
export function AgentDispatchPage() {
  const [capability, setCapability] = useState('文本回显');
  const [message, setMessage] = useState('你好世界');
  const [acceptanceType, setAcceptanceType] = useState<AcceptanceType>('output_contains');
  const [contains, setContains] = useState('你好世界');
  const [toolName, setToolName] = useState('echo');
  const [parent, setParent] = useState<ParentTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function dispatch() {
    setBusy(true);
    setError(null);
    try {
      const acceptance =
        acceptanceType === 'output_contains'
          ? { type: acceptanceType, contains: contains.split(',').map((s) => s.trim()).filter(Boolean) }
          : { type: acceptanceType, tools: [toolName] };
      const trace = await agentDispatchApi.dispatch(
        {
          capability,
          payload: { message },
          acceptance,
          env_contract: { runtime: 'deterministic-worker' },
        },
        [{ name: toolName, arguments: toolName === 'add' ? { a: 2, b: 3 } : { message } }],
      );
      setParent(trace);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head"><h2>子 Agent 派发验证</h2></div>
      <div className="muted" style={{ marginBottom: 8 }}>
        Task 四件套（capability / payload / acceptance / env_contract）派发子任务；
        子 Agent 真实执行工具后自报结果，独立验收器按 acceptance 真实复核，
        不信任自报。验收拒绝时失败原因经验回写。
      </div>

      <section
        style={{
          border: '1px solid var(--border, #d8dee9)', borderRadius: 8,
          padding: '12px 16px', marginBottom: 16, display: 'grid',
          gap: 8, maxWidth: 720,
        }}
        aria-label="派发表单"
      >
        <label style={rowStyle}>
          <span style={labelStyle}>能力（capability）</span>
          <input value={capability} onChange={(e) => setCapability(e.target.value)} style={inputStyle} />
        </label>
        <label style={rowStyle}>
          <span style={labelStyle}>任务载荷（payload.message）</span>
          <input value={message} onChange={(e) => setMessage(e.target.value)} style={inputStyle} />
        </label>
        <label style={rowStyle}>
          <span style={labelStyle}>工具（tools）</span>
          <select value={toolName} onChange={(e) => setToolName(e.target.value)} style={inputStyle}>
            <option value="echo">echo（回显文本）</option>
            <option value="add">add（数值求和）</option>
          </select>
        </label>
        <label style={rowStyle}>
          <span style={labelStyle}>验收类型（acceptance.type）</span>
          <select
            value={acceptanceType}
            onChange={(e) => setAcceptanceType(e.target.value as AcceptanceType)}
            style={inputStyle}
          >
            <option value="output_contains">output_contains（产出包含）</option>
            <option value="tool_invoked">tool_invoked（工具真实调用）</option>
          </select>
        </label>
        {acceptanceType === 'output_contains' ? (
          <label style={rowStyle}>
            <span style={labelStyle}>验收片段（contains，逗号分隔）</span>
            <input value={contains} onChange={(e) => setContains(e.target.value)} style={inputStyle} />
          </label>
        ) : null}
        <div>
          <button type="button" className="small" onClick={() => void dispatch()} disabled={busy}>
            {busy ? '派发中…' : '发起派发'}
          </button>
        </div>
        {error ? <div style={{ color: 'var(--amber, #b45309)' }} role="alert">派发失败：{error}</div> : null}
      </section>

      {parent ? <TraceTree parent={parent} /> : null}
    </>
  );
}

const rowStyle: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '200px 1fr', gap: 8, alignItems: 'center',
};
const labelStyle: React.CSSProperties = { fontSize: 13, color: 'var(--muted, #5a6572)' };
const inputStyle: React.CSSProperties = {
  padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border, #d8dee9)',
  font: 'inherit',
};

function VerdictBadge({ verdict }: { verdict: string }) {
  const ok = verdict === 'verified';
  return (
    <span
      className={`badge ${ok ? 'ok' : 'fail'}`}
      style={{
        padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700,
        color: ok ? '#0a7d33' : '#b3261e',
        background: ok ? 'rgba(10,125,51,.12)' : 'rgba(179,38,30,.12)',
      }}
    >
      {ok ? '已验收 verified' : '已拒绝 rejected'}
    </span>
  );
}

function TraceTree({ parent }: { parent: ParentTrace }) {
  return (
    <section aria-label="父/子 trace 树" style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 6 }}>
        <strong>父任务 {parent.parent_task_id}</strong>
        <VerdictBadge verdict={parent.status} />
        <span className="muted" style={{ fontSize: 12 }}>
          派发于 {parent.dispatched_at}
          {parent.verified_at ? ` · 验收于 ${parent.verified_at}` : ''}
        </span>
      </div>
      <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
        capability: {parent.task.capability} · env_contract:{' '}
        {JSON.stringify(parent.task.env_contract ?? {})}
      </div>

      {parent.children.map((child) => (
        <div
          key={child.child_task_id}
          style={{
            border: '1px solid var(--border, #d8dee9)', borderRadius: 8,
            padding: '12px 16px', marginLeft: 24, marginBottom: 12,
          }}
        >
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <strong>└ 子任务 {child.child_task_id}</strong>
            <VerdictBadge verdict={child.verification?.verdict ?? child.status} />
            <span className="muted" style={{ fontSize: 12 }}>
              自报：{child.self_report?.status ?? '—'}（{child.self_report?.summary ?? '—'}）
            </span>
          </div>

          <div style={{ margin: '8px 0' }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>工具调用（真实执行）</div>
            {child.tool_calls.map((c, i) => (
              <div key={i} style={{ fontSize: 13, marginLeft: 16 }}>
                {c.ok ? '✅' : '❌'} <code>{c.tool}</code> call_id: {c.call_id ?? '—'}
                {c.error ? <span style={{ color: '#b3261e' }}> · {c.error}</span> : null}
                <div style={{ marginLeft: 16, color: 'var(--muted, #5a6572)' }}>
                  产出：{truncate(c.error ?? (typeof c.result === 'string' ? c.result : JSON.stringify(c.result)))}
                </div>
              </div>
            ))}
          </div>

          {child.verification ? (
            <div style={{ margin: '8px 0' }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                独立验收结论（不信任自报，一致性：{child.verification.consistent ? '一致' : '不一致'}）
              </div>
              {child.verification.checks.map((chk, i) => (
                <div key={i} style={{ fontSize: 13, marginLeft: 16 }}>
                  {chk.passed ? '✅' : '❌'} {chk.check}: {chk.target}
                </div>
              ))}
              {child.verification.reasons.map((rsn, i) => (
                <div key={i} style={{ fontSize: 13, marginLeft: 16, color: '#b3261e' }}>
                  原因：{rsn}
                </div>
              ))}
            </div>
          ) : null}

          {child.experience ? (
            <div
              style={{
                fontSize: 13, marginLeft: 16, padding: '6px 10px',
                background: 'rgba(179,38,30,.08)', borderRadius: 6,
              }}
            >
              经验回写（{child.experience.scope}，{child.experience.written_at}）：
              {child.experience.lesson}
            </div>
          ) : null}
        </div>
      ))}
    </section>
  );
}

function truncate(text: string, max = 160): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
