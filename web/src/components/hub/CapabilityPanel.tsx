// 能力清单 + 路由试算。
// 路由是 v1 的确定性打分（标签命中 + 健康优先 + 偏好权重），不是 LLM 决策，
// 因此这里把「得分」和「理由」都摊开给用户看，不假装智能。
import type { HubCapabilityRow, HubRouteCandidate } from '../../api/hub';

export function CapabilityTable({
  rows,
  busy,
  onDrop,
}: {
  rows: HubCapabilityRow[];
  busy: boolean;
  onDrop: (row: HubCapabilityRow) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="muted" data-testid="hub-caps-empty">
        还没有任何能力声明。创建连接时填写「能力标签」，或对 MCP 连接点探活自动发现。
      </p>
    );
  }
  return (
    <table className="hub-table" data-testid="hub-capability-table">
      <thead>
        <tr>
          <th>能力</th>
          <th>所属连接</th>
          <th>类型</th>
          <th>健康</th>
          <th>说明</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.connection_id}:${r.capability.name}`}>
            <td>{r.capability.name}</td>
            <td>
              {r.icon || '🔌'} {r.connection_name}
            </td>
            <td>{r.kind}</td>
            <td>{r.healthy === null ? '未探活' : r.healthy ? '可用' : '不可用'}</td>
            <td className="muted">{r.capability.description ?? '—'}</td>
            <td>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDrop(r)}
                data-testid={`hub-cap-drop-${r.connection_id}-${r.capability.name}`}
              >
                移除
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RouteLab({
  hint,
  onHint,
  onRun,
  busy,
  candidates,
  searched,
  error,
}: {
  hint: string;
  onHint: (v: string) => void;
  onRun: () => void;
  busy: boolean;
  candidates: HubRouteCandidate[];
  searched: boolean;
  error: string;
}) {
  return (
    <section className="hub-route" data-testid="hub-route-lab">
      <h3>路由试算</h3>
      <p className="muted">
        输入一句任务描述，按「能力标签命中 + 最近探活结果 + 用户偏好」打分排序。v1 为确定性规则，非模型决策。
      </p>
      <div className="hub-form-row">
        <input
          value={hint}
          onChange={(e) => onHint(e.target.value)}
          placeholder="如：把这段中文总结成三条要点"
          data-testid="hub-route-hint"
        />
        <button type="button" onClick={onRun} disabled={busy || hint.trim() === ''} data-testid="hub-route-run">
          试算
        </button>
      </div>
      {error && <div className="notice danger" data-testid="hub-route-error">{error}</div>}
      {searched && candidates.length === 0 && !error && (
        <p className="muted" data-testid="hub-route-empty">
          没有连接的能力标签命中这句话。检查能力标签，或先用预置建连接。
        </p>
      )}
      <ol className="hub-route-list">
        {candidates.map((c) => (
          <li key={c.connection_id} data-testid={`hub-route-candidate-${c.connection_id}`}>
            <div className="hub-route-head">
              <span aria-hidden="true">{c.icon || '🔌'}</span>
              <strong>{c.connection_name}</strong>
              <span className="hub-score">得分 {c.score}</span>
              <span className="muted">{c.healthy === null ? '未探活' : c.healthy ? '可用' : '不可用'}</span>
            </div>
            <p className="muted">能力：{c.capability.name}</p>
            <ul className="hub-reasons">
              {c.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}
