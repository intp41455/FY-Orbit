// 外部 A2A Agent 出站面板：登记后的「探活 + 派发」闭环入口。
//
// 诚实约定（总纲铁律 3 / FROZEN_CONTRACT §11）：
//  - 探活结果原样展示后端 detail，不美化、不省略；失败就是失败。
//  - 页面**不自己**判断"这个 Agent 能不能用"——那只有真调用过才知道，
//    所以这里没有任何本地兜底的绿灯。探不通就显示不通。
//  - "派发一条消息"是真发一次 message/send（会真的对端产生一条 Task），
//    因此按钮默认只对已探活通过的连接开放，并在按钮上方写明副作用。
import { hubApi } from '../../api/hub';
import type { HubConnection, HubHealthReport } from '../../api/hub';
import { errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';

export function A2AAgentPanel({
  connections,
  busy,
  onProbe,
  onDispatch,
}: {
  connections: HubConnection[];
  busy: boolean;
  onProbe: (conn: HubConnection) => void;
  onDispatch: (conn: HubConnection, text: string) => void;
}) {
  useBase({ surface: 'web/src/components/hub/A2AAgentPanel' });
  if (connections.length === 0) {
    return (
      <section className="hub-a2a-panel" data-testid="hub-a2a-panel-empty">
        <h3>外部 A2A Agent（出站）</h3>
        <p className="muted">
          还没有登记外部 A2A Agent。在「新建连接」里选「外部 A2A Agent」，
          参数填一行 <code>base_url</code> 即可（Agent Card 与 message/send 都会被真实探测，不造假绿）。
        </p>
      </section>
    );
  }
  return (
    <section className="hub-a2a-panel" data-testid="hub-a2a-panel">
      <h3>外部 A2A Agent（出站）· {connections.length}</h3>
      <p className="muted">
        探活 = 真拉一次 Agent Card（<code>/.well-known/agent.json</code>），再实测一次{' '}
        <code>message/send</code>（发一条空消息，合规对端会在参数校验处拒掉，因此不产生真实任务）。
        卡片能取到但方法没实现，照样判红。
      </p>
      <div className="hub-a2a-list">
        {connections.map((conn) => {
          const healthy = conn.health.ok === true;
          const skills = conn.capabilities.map((c) => c.name).filter((n) => n.startsWith('a2a.skill:'));
          return (
            <div className="hub-card" key={conn.id} data-testid={`hub-a2a-${conn.id}`}>
              <header className="hub-card-head">
                <span className="hub-card-icon" aria-hidden="true">
                  {conn.icon || '🤝'}
                </span>
                <div className="hub-card-title">
                  <h3>{conn.name}</h3>
                  <span className="muted">{String(conn.config.base_url ?? conn.config.url ?? '')}</span>
                </div>
              </header>
              <p className="hub-card-desc" data-testid={`hub-a2a-detail-${conn.id}`}>
                {conn.health.checked_at ? conn.health.detail || '（后端未给出原因）' : '尚未探活'}
              </p>
              {skills.length > 0 && (
                <p className="hub-tags" data-testid={`hub-a2a-skills-${conn.id}`}>
                  {skills.map((n) => (
                    <span className="hub-tag" key={n}>
                      {n.replace('a2a.skill:', '')}
                    </span>
                  ))}
                </p>
              )}
              <div className="hub-card-actions">
                <button
                  type="button"
                  onClick={() => onProbe(conn)}
                  disabled={busy}
                  data-testid={`hub-a2a-probe-${conn.id}`}
                >
                  探活
                </button>
                <button
                  type="button"
                  onClick={() => onDispatch(conn, 'ping（来自中台的连通性测试）')}
                  disabled={busy || !healthy}
                  data-testid={`hub-a2a-dispatch-${conn.id}`}
                >
                  派发一条消息
                </button>
              </div>
              {!healthy && (
                <p className="muted">
                  「派发一条消息」需先探活通过——不对没验过的对端发真任务。
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** A2A 探活比通用探活多一次 JSON-RPC 往返，预算给得比 3s 宽一点。 */
export const A2A_HEALTH_TIMEOUT_SECONDS = 8;

/** 派发结果文案：成功/失败都原样带出后端给的 Task 或 error。 */
export function describeA2ADispatch(conn: HubConnection, report: HubHealthReport): string {
  return report.ok
    ? `「${conn.name}」A2A 探活通过（${report.latency_ms}ms）。`
    : `「${conn.name}」A2A 探活失败：${report.detail || '后端未给出原因'}`;
}

export function a2aDispatchError(e: unknown): string {
  return `A2A 派发请求失败：${errorMessage(e)}`;
}

/** 复用通用 api 层的 healthCheck（宽预算）。 */
export function probeA2A(id: string) {
  return hubApi.healthCheck(id, A2A_HEALTH_TIMEOUT_SECONDS);
}

/** 复用通用 api 层的 invoke（真正发一条 message/send）。 */
export function dispatchA2A(id: string, text: string) {
  return hubApi.invoke(id, { action: 'send', params: { prompt: text }, timeout_seconds: 30 });
}