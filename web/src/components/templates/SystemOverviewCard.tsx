/**
 * 「选中即懂」的构成摘要卡（P13 · A-开箱模板-07①）。
 *
 * 一眼可见：几个 agent、各自干什么、用什么工具、产出放哪、大致用多少。
 * 用量一律标注**预估**——后端 `estimate.estimated` 恒为 true，金额以运行账单为准，
 * 这里**不得**把它渲染成账单数字。
 */
import type { SystemOverview } from '../../api/templates';

const LAYER_LABELS: Record<string, string> = {
  novice_default: '新手默认层',
  advanced_swappable: '进阶可换层',
  technical_removable: '技术可拆层',
};

function asText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

export function SystemOverviewCard({ overview }: { overview: SystemOverview }) {
  const rule = overview.artifact_rule ?? {};
  const est = overview.estimate;
  return (
    <section className="fy-tpl-overview" aria-label="系统构成一眼可见">
      <header className="fy-tpl-overview-head">
        <div>
          <strong>{overview.name}</strong>
          <span className="muted"> · {overview.scenario_label || overview.scenario}</span>
        </div>
        <div className="row" style={{ gap: '0.4rem' }}>
          <span className="badge">{LAYER_LABELS[overview.layer] ?? overview.layer}</span>
          <span className="badge">{overview.member_count} 个成员 + 总控</span>
        </div>
      </header>

      <dl className="fy-tpl-facts">
        <div>
          <dt>总控职责</dt>
          <dd>{overview.controller.duties.join(' / ') || '—'}</dd>
        </div>
        <div>
          <dt>产出放哪</dt>
          <dd>
            <code>{asText(rule['dir'])}</code>
            <span className="muted"> · 命名 </span>
            <code>{asText(rule['pattern'])}</code>
          </dd>
        </div>
        <div>
          <dt>大致用量</dt>
          <dd>
            <span className="fy-tpl-estimate-badge">预估</span>{' '}
            约 {est.steps} 步 / {est.approx_model_calls} 次模型调用 ·{' '}
            {est.cost_note}
          </dd>
        </div>
      </dl>

      <table className="fy-tpl-members">
        <caption className="muted">每个成员负责什么、能碰哪些工具</caption>
        <thead>
          <tr>
            <th scope="col">成员</th>
            <th scope="col">职责</th>
            <th scope="col">可用工具</th>
          </tr>
        </thead>
        <tbody>
          {overview.members.map((m) => (
            <tr key={m.id}>
              <td>
                <code>{m.id}</code>
                <div className="muted">{m.role}</div>
              </td>
              <td>{m.responsibilities.join('；') || '—'}</td>
              <td>
                {m.tools.length === 0 ? (
                  <span className="muted">无（默认本地）</span>
                ) : (
                  m.tools.map((t) => (
                    <span key={t} className="badge">
                      {t}
                    </span>
                  ))
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
