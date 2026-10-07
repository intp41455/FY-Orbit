import { useState } from 'react';
import {
  agentDispatchApi,
  type AcceptanceType,
  type ParentTrace,
} from '../api/agentDispatch';
import { LineIcon } from '../components/ui/LineIcon';
import './../styles/pages/workbench.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

/**
 * P1-20 子 Agent 派发验证页面（包 A 重构版）。
 *
 * 颜色一律走 `var(--ui-*)`（§12.5 违规修：`#0a7d33`→`--ui-st-complete`、
 * `#b3261e`→`--ui-st-failed`）；emoji `✅/❌` 全部改为 `LineIcon`（红线二：
 * 颜色不是唯一信息通道）；内联字面量收进 `workbench.css` 的 `.disp-*` 作用域。
 */
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
    <BaseBound surface="agent-dispatch">
      <div className="disp-shell">
        <div className="page-head">
          <h2>子 Agent 派发验证</h2>
        </div>
        <p className="muted disp-intro">
          Task 四件套（capability / payload / acceptance / env_contract）派发子任务；
          子 Agent 真实执行工具后自报结果，独立验收器按 acceptance 真实复核，
          不信任自报。验收拒绝时失败原因经验回写。
        </p>

        <section className="disp-form" aria-label="派发表单">
          <label className="disp-row">
            <span className="disp-label">能力（capability）</span>
            <input value={capability} onChange={(e) => setCapability(e.target.value)} className="disp-input" />
          </label>
          <label className="disp-row">
            <span className="disp-label">任务载荷（payload.message）</span>
            <input value={message} onChange={(e) => setMessage(e.target.value)} className="disp-input" />
          </label>
          <label className="disp-row">
            <span className="disp-label">工具（tools）</span>
            <select value={toolName} onChange={(e) => setToolName(e.target.value)} className="disp-input">
              <option value="echo">echo（回显文本）</option>
              <option value="add">add（数值求和）</option>
            </select>
          </label>
          <label className="disp-row">
            <span className="disp-label">验收类型（acceptance.type）</span>
            <select
              value={acceptanceType}
              onChange={(e) => setAcceptanceType(e.target.value as AcceptanceType)}
              className="disp-input"
            >
              <option value="output_contains">output_contains（产出包含）</option>
              <option value="tool_invoked">tool_invoked（工具真实调用）</option>
            </select>
          </label>
          {acceptanceType === 'output_contains' ? (
            <label className="disp-row">
              <span className="disp-label">验收片段（contains，逗号分隔）</span>
              <input value={contains} onChange={(e) => setContains(e.target.value)} className="disp-input" />
            </label>
          ) : null}
          <div className="disp-actions">
            <button type="button" className="ui-btn ui-btn--sm ui-btn--primary" onClick={() => void dispatch()} disabled={busy}>
              {busy ? '派发中…' : '发起派发'}
            </button>
          </div>
          {error ? <div className="ui-error-text" role="alert">派发失败：{error}</div> : null}
        </section>

        {parent ? <TraceTree parent={parent} /> : null}
      </div>
    </BaseBound>
  );
}

/* ---------- 验收徽标 ---------- */

function VerdictBadge({ verdict }: { verdict: string }) {
  const ok = verdict === 'verified';
  return (
    <span className={`disp-verdict${ok ? ' is-ok' : ' is-fail'}`} data-verdict={ok ? 'verified' : 'rejected'}>
      <LineIcon name={ok ? 'check' : 'xCircle'} size={14} />
      {ok ? '已验收 verified' : '已拒绝 rejected'}
    </span>
  );
}

/* ---------- Trace 树 ---------- */

function TraceTree({ parent }: { parent: ParentTrace }) {
  return (
    <section className="disp-trace" aria-label="父/子 trace 树">
      <div className="disp-trace-hd">
        <strong>父任务 {parent.parent_task_id}</strong>
        <VerdictBadge verdict={parent.status} />
        <span className="muted small">
          派发于 {parent.dispatched_at}
          {parent.verified_at ? ` · 验收于 ${parent.verified_at}` : ''}
        </span>
      </div>
      <div className="muted small disp-trace-meta">
        capability: {parent.task.capability} · env_contract:{' '}
        {JSON.stringify(parent.task.env_contract ?? {})}
      </div>

      {parent.children.map((child) => (
        <div key={child.child_task_id} className="disp-child">
          <div className="disp-child-hd">
            <strong>└ 子任务 {child.child_task_id}</strong>
            <VerdictBadge verdict={child.verification?.verdict ?? child.status} />
            <span className="muted small">
              自报：{child.self_report?.status ?? '—'}（{child.self_report?.summary ?? '—'}）
            </span>
          </div>

          {/* 工具调用（真实执行） */}
          <div className="disp-sec">
            <div className="disp-sec-title">工具调用（真实执行）</div>
            {child.tool_calls.map((c, i) => (
              <div key={i} className="disp-tool-call">
                <span className={`disp-tool-led${c.ok ? ' is-ok' : ' is-fail'}`}>
                  <LineIcon name={c.ok ? 'check' : 'xCircle'} size={12} />
                </span>
                <code>{c.tool}</code> call_id: {c.call_id ?? '—'}
                {c.error ? <span className="disp-err-inline"> · {c.error}</span> : null}
                <div className="muted small disp-tool-out">
                  产出：{truncate(c.error ?? (typeof c.result === 'string' ? c.result : JSON.stringify(c.result)))}
                </div>
              </div>
            ))}
          </div>

          {/* 独立验收结论 */}
          {child.verification ? (
            <div className="disp-sec">
              <div className="disp-sec-title">
                独立验收结论（不信任自报，一致性：{child.verification.consistent ? '一致' : '不一致'}）
              </div>
              {child.verification.checks.map((chk, i) => (
                <div key={i} className="disp-check">
                  <span className={`disp-tool-led${chk.passed ? ' is-ok' : ' is-fail'}`}>
                    <LineIcon name={chk.passed ? 'check' : 'xCircle'} size={12} />
                  </span>
                  {chk.check}: {chk.target}
                </div>
              ))}
              {child.verification.reasons.map((rsn, i) => (
                <div key={i} className="disp-reason">
                  原因：{rsn}
                </div>
              ))}
            </div>
          ) : null}

          {/* 经验回写 */}
          {child.experience ? (
            <div className="disp-experience">
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
