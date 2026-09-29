import { catalogApi } from '../api/catalog';
import { useAsync, Spinner } from '../components/ui';

export function SkillsPage() {
  const agents = useAsync(() => catalogApi.agents(), []);
  const skills = useAsync(() => catalogApi.skills(), []);

  return (
    <>
      <div className="page-head"><h2>能力目录</h2></div>
      <p className="muted">
        Agent 与技能的写操作（启用/晋级/下线）必须经提案审批；此处为只读目录。含脚本包的技能需要隔离沙箱。
      </p>

      <h3>Agents</h3>
      {agents.loading && <Spinner />}
      {agents.error && <div className="notice danger">{agents.error}</div>}
      {agents.data && agents.data.length === 0 && <div className="muted">暂无 Agent。</div>}
      {agents.data && agents.data.length > 0 && (
        <div className="card">
          <table>
            <thead><tr><th>名称</th><th>版本</th><th>域</th><th>状态</th><th>健康</th><th>能力</th></tr></thead>
            <tbody>
              {agents.data.map((a) => (
                <tr key={a.name + a.version}>
                  <td>{a.name}</td>
                  <td>{a.version}</td>
                  <td>{a.domain}</td>
                  <td><span className="badge">{a.state}</span></td>
                  <td>{a.healthy ? <span className="badge ok">healthy</span> : <span className="badge danger">unhealthy</span>}</td>
                  <td>{a.capabilities.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>技能</h3>
      {skills.loading && <Spinner />}
      {skills.error && <div className="notice danger">{skills.error}</div>}
      {skills.data && skills.data.length === 0 && <div className="muted">暂无技能。</div>}
      {skills.data && skills.data.length > 0 && (
        <div className="card">
          <table>
            <thead><tr><th>名称</th><th>版本</th><th>状态</th><th>来源/许可</th><th>隔离</th></tr></thead>
            <tbody>
              {skills.data.map((s) => (
                <tr key={s.name + s.version}>
                  <td>{s.name}</td>
                  <td>{s.version}</td>
                  <td><span className="badge">{s.state}</span></td>
                  <td>{s.source} · {s.license}</td>
                  <td>{s.requires_isolation ? <span className="badge warn">需沙箱</span> : <span className="badge">指令包</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
