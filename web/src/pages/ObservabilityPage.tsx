/**
 * 统一可观测看板与成本仪表盘页（A-可观测-01~04 · P2 / A-成本-02 · P3）。
 *
 * 四大板块：
 * 1. 实时日志监控面板：多维筛选 + SSE 流式日志 + 错误跳 Trace
 * 2. 性能监控面板：端到端耗时分位 (p50/p95) + 各 Agent 统计 (调用量/耗时/失败率)
 * 3. 成本与步骤进度条：任务执行进度加权 + 剩余耗时预测 + Token 预算沉淀
 * 4. Trace 调用链：集成既有 TraceView 泳道时序视图
 */
import { useEffect, useState, useRef } from 'react';
import {
  fetchLogs,
  fetchPerformance,
  fetchAgentPerformance,
  fetchCostProgress,
  fetchLogTrace,
  type LogItem,
  type PerformanceStats,
  type AgentPerformanceStats,
  type CostProgressResponse,
} from '../api/observability';
import { Spinner, errorMessage } from '../components/ui';
import { LineIcon } from '../components/ui/LineIcon';
import { TraceView } from '../components/trace/TraceView';
import { BaseBound } from '../components/ui/SaveStatusIndicator';
import '../styles/pages/system.css';

type Tab = 'logs' | 'performance' | 'progress' | 'trace';

export function ObservabilityPage() {
  const [tab, setTab] = useState<Tab>('logs');

  // Logs state
  const [levelFilter, setLevelFilter] = useState<string>('');
  const [actorFilter, setActorFilter] = useState<string>('');
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null);

  // Performance state
  const [perfStats, setPerfStats] = useState<PerformanceStats | null>(null);
  const [agentStats, setAgentStats] = useState<AgentPerformanceStats | null>(null);
  const [loadingPerf, setLoadingPerf] = useState(false);

  // Progress state
  const [costData, setCostData] = useState<CostProgressResponse | null>(null);
  const [taskIdInput, setTaskIdInput] = useState('');
  const [loadingCost, setLoadingCost] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);

  // Load logs
  const loadLogs = async () => {
    setLoadingLogs(true);
    setLogError(null);
    try {
      const res = await fetchLogs({
        level: levelFilter || undefined,
        actor: actorFilter || undefined,
        limit: 100,
      });
      setLogs(res.items);
    } catch (e) {
      setLogError(errorMessage(e));
    } finally {
      setLoadingLogs(false);
    }
  };

  useEffect(() => {
    if (tab === 'logs' && !streaming) {
      loadLogs();
    }
  }, [tab, levelFilter, actorFilter]);

  // Handle SSE streaming
  useEffect(() => {
    if (streaming) {
      const url = new URL('/api/observability/logs/stream', window.location.origin);
      if (levelFilter) url.searchParams.set('level', levelFilter);
      const es = new EventSource(url.toString(), { withCredentials: true });
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        try {
          const item: LogItem = JSON.parse(event.data);
          setLogs((prev) => [item, ...prev].slice(0, 300));
        } catch {
          // ignore non-json frames
        }
      };

      es.onerror = () => {
        setStreaming(false);
        es.close();
      };

      return () => {
        es.close();
      };
    } else {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    }
  }, [streaming, levelFilter]);

  // Load Performance
  useEffect(() => {
    if (tab === 'performance') {
      setLoadingPerf(true);
      Promise.all([fetchPerformance(), fetchAgentPerformance()])
        .then(([p, a]) => {
          setPerfStats(p);
          setAgentStats(a);
        })
        .catch((e) => console.error(e))
        .finally(() => setLoadingPerf(false));
    }
  }, [tab]);

  // Load Cost / Progress
  const loadCost = async (tId?: string) => {
    setLoadingCost(true);
    try {
      const data = await fetchCostProgress(tId);
      setCostData(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingCost(false);
    }
  };

  useEffect(() => {
    if (tab === 'progress') {
      loadCost(taskIdInput || undefined);
    }
  }, [tab]);

  const handleTraceJump = async (seq: number) => {
    try {
      const res = await fetchLogTrace(seq);
      if (res.trace_id) {
        setSelectedTrace(`Trace: ${res.trace_id} (Basis: ${res.basis})`);
      } else {
        setSelectedTrace(`序号 #${seq} 未关联显式链路 (原因: ${res.reason || res.basis})`);
      }
    } catch (e) {
      setSelectedTrace(`查询 Trace 失败: ${errorMessage(e)}`);
    }
  };

  return (
    <BaseBound surface="web/src/pages/ObservabilityPage">
      <div className="page-container observability-page" style={{ padding: '24px 32px' }}>
        <header className="page-header" style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
              <LineIcon name="sliders" size={24} />
              统一可观测与成本仪表盘
            </h1>
            <p style={{ color: 'var(--ui-muted, #8A93A8)', margin: '6px 0 0 0', fontSize: 14 }}>
              全链路 Trace 追踪 · 实时事件日志 · 耗时性能分位 · 成本与步骤进度
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, background: 'var(--ui-surface-2, rgba(255,255,255,0.04))', padding: 4, borderRadius: 8 }}>
            <button
              className={`btn btn-sm ${tab === 'logs' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('logs')}
            >
              实时日志流
            </button>
            <button
              className={`btn btn-sm ${tab === 'performance' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('performance')}
            >
              性能监控
            </button>
            <button
              className={`btn btn-sm ${tab === 'progress' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('progress')}
            >
              成本与进度
            </button>
            <button
              className={`btn btn-sm ${tab === 'trace' ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab('trace')}
            >
              Trace 调用链
            </button>
          </div>
        </header>

        {selectedTrace && (
          <div style={{
            background: 'var(--ui-accent-dim, rgba(53,224,200,0.1))',
            border: '1px solid var(--ui-accent-line, rgba(53,224,200,0.3))',
            borderRadius: 8,
            padding: '10px 16px',
            marginBottom: 16,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 13,
          }}>
            <span>🔍 {selectedTrace}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => setSelectedTrace(null)}>关闭</button>
          </div>
        )}

        {/* Tab 1: Logs */}
        {tab === 'logs' && (
          <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
              <select
                value={levelFilter}
                onChange={(e) => setLevelFilter(e.target.value)}
                style={{ padding: '6px 12px', borderRadius: 6, background: 'var(--ui-surface-2, #171B25)', color: '#fff', border: '1px solid var(--ui-line, rgba(255,255,255,0.1))' }}
              >
                <option value="">全部日志级别</option>
                <option value="error">ERROR 错误</option>
                <option value="warning">WARNING 警告</option>
                <option value="info">INFO 信息</option>
                <option value="debug">DEBUG 调试</option>
                <option value="change">CHANGE 变更</option>
              </select>

              <input
                type="text"
                placeholder="按执行者筛选 (Actor)..."
                value={actorFilter}
                onChange={(e) => setActorFilter(e.target.value)}
                style={{ padding: '6px 12px', borderRadius: 6, background: 'var(--ui-surface-2, #171B25)', color: '#fff', border: '1px solid var(--ui-line, rgba(255,255,255,0.1))', width: 220 }}
              />

              <button className="btn btn-sm btn-ghost" onClick={loadLogs} disabled={loadingLogs || streaming}>
                刷新
              </button>

              <button
                className={`btn btn-sm ${streaming ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setStreaming(!streaming)}
              >
                {streaming ? '⏹ 停止实时流' : '▶ 开启实时流 (SSE)'}
              </button>
            </div>

            {logError && <div style={{ color: '#ff6b6b', marginBottom: 12 }}>{logError}</div>}
            {loadingLogs && <Spinner />}

            <div style={{
              background: 'var(--ui-surface, #12151D)',
              border: '1px solid var(--ui-line, rgba(255,255,255,0.08))',
              borderRadius: 12,
              overflow: 'hidden',
              fontFamily: 'monospace',
              fontSize: 13,
            }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                <thead>
                  <tr style={{ background: 'var(--ui-surface-2, #171B25)', borderBottom: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                    <th style={{ padding: '10px 14px', width: 80 }}>Seq</th>
                    <th style={{ padding: '10px 14px', width: 90 }}>级别</th>
                    <th style={{ padding: '10px 14px', width: 140 }}>执行者</th>
                    <th style={{ padding: '10px 14px', width: 180 }}>动作</th>
                    <th style={{ padding: '10px 14px' }}>详情 / 目标</th>
                    <th style={{ padding: '10px 14px', width: 100, textAlign: 'right' }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.length === 0 && !loadingLogs ? (
                    <tr>
                      <td colSpan={6} style={{ padding: 32, textAlign: 'center', color: 'var(--ui-muted, #8A93A8)' }}>
                        暂无日志记录
                      </td>
                    </tr>
                  ) : (
                    logs.map((log) => {
                      const isErr = log.level === 'error';
                      const isWarn = log.level === 'warning';
                      const badgeColor = isErr ? '#ff4d4f' : isWarn ? '#faad14' : log.level === 'change' ? '#35E0C8' : '#8A7BFF';
                      return (
                        <tr key={log.seq} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', background: isErr ? 'rgba(255,77,79,0.06)' : 'transparent' }}>
                          <td style={{ padding: '8px 14px', color: 'var(--ui-muted, #8A93A8)' }}>#{log.seq}</td>
                          <td style={{ padding: '8px 14px' }}>
                            <span style={{
                              display: 'inline-block',
                              padding: '2px 6px',
                              borderRadius: 4,
                              fontSize: 11,
                              fontWeight: 600,
                              background: badgeColor,
                              color: '#07080B',
                            }}>
                              {log.level.toUpperCase()}
                            </span>
                          </td>
                          <td style={{ padding: '8px 14px', color: '#EAECF2' }}>{log.actor}</td>
                          <td style={{ padding: '8px 14px', color: '#35E0C8' }}>{log.action}</td>
                          <td style={{ padding: '8px 14px', color: 'var(--ui-muted, #8A93A8)' }}>
                            {log.target ? `[${log.target}] ` : ''}
                            {log.details ? JSON.stringify(log.details).slice(0, 80) : ''}
                          </td>
                          <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                            <button
                              className="btn btn-sm btn-ghost"
                              style={{ padding: '2px 8px', fontSize: 11 }}
                              onClick={() => handleTraceJump(log.seq)}
                            >
                              Trace
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 2: Performance */}
        {tab === 'performance' && (
          <div>
            {loadingPerf && <Spinner />}
            {perfStats && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
                <div className="card" style={{ padding: 18, background: 'var(--ui-surface, #12151D)', borderRadius: 12, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                  <div style={{ fontSize: 12, color: 'var(--ui-muted, #8A93A8)', marginBottom: 6 }}>中位耗时 (p50)</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: '#35E0C8' }}>
                    {perfStats.p50_latency_ms !== null ? `${perfStats.p50_latency_ms} ms` : '样本积累中'}
                  </div>
                </div>
                <div className="card" style={{ padding: 18, background: 'var(--ui-surface, #12151D)', borderRadius: 12, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                  <div style={{ fontSize: 12, color: 'var(--ui-muted, #8A93A8)', marginBottom: 6 }}>长尾耗时 (p95)</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: '#F5C26B' }}>
                    {perfStats.p95_latency_ms !== null ? `${perfStats.p95_latency_ms} ms` : '样本积累中'}
                  </div>
                </div>
                <div className="card" style={{ padding: 18, background: 'var(--ui-surface, #12151D)', borderRadius: 12, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                  <div style={{ fontSize: 12, color: 'var(--ui-muted, #8A93A8)', marginBottom: 6 }}>总事件吞吐</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: '#8A7BFF' }}>
                    {perfStats.total_events} 笔
                  </div>
                </div>
                <div className="card" style={{ padding: 18, background: 'var(--ui-surface, #12151D)', borderRadius: 12, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                  <div style={{ fontSize: 12, color: 'var(--ui-muted, #8A93A8)', marginBottom: 6 }}>活跃跨度 (Span)</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: '#EAECF2' }}>
                    {perfStats.active_span_ms !== null ? `${Math.round(perfStats.active_span_ms / 1000)} 秒` : '单点会话'}
                  </div>
                </div>
              </div>
            )}

            {agentStats && (
              <div style={{
                background: 'var(--ui-surface, #12151D)',
                border: '1px solid var(--ui-line, rgba(255,255,255,0.08))',
                borderRadius: 12,
                overflow: 'hidden',
              }}>
                <div style={{ padding: '14px 20px', fontWeight: 600, borderBottom: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
                  各 Agent 独立性能与健康度明细
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: 'var(--ui-surface-2, #171B25)', color: 'var(--ui-muted, #8A93A8)' }}>
                      <th style={{ padding: '10px 16px' }}>Agent 标识</th>
                      <th style={{ padding: '10px 16px' }}>调用次数</th>
                      <th style={{ padding: '10px 16px' }}>平均耗时</th>
                      <th style={{ padding: '10px 16px' }}>失败笔数</th>
                      <th style={{ padding: '10px 16px' }}>错误率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agentStats.agents.length === 0 ? (
                      <tr>
                        <td colSpan={5} style={{ padding: 24, textAlign: 'center', color: 'var(--ui-muted, #8A93A8)' }}>
                          暂无子 Agent 独立调用统计
                        </td>
                      </tr>
                    ) : (
                      agentStats.agents.map((ag) => (
                        <tr key={ag.agent_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                          <td style={{ padding: '10px 16px', fontWeight: 600, color: '#35E0C8' }}>{ag.agent_id}</td>
                          <td style={{ padding: '10px 16px' }}>{ag.calls_count} 次</td>
                          <td style={{ padding: '10px 16px' }}>{ag.avg_latency_ms !== null ? `${ag.avg_latency_ms} ms` : '-'}</td>
                          <td style={{ padding: '10px 16px', color: ag.error_count > 0 ? '#ff4d4f' : 'inherit' }}>{ag.error_count}</td>
                          <td style={{ padding: '10px 16px' }}>{(ag.error_rate * 100).toFixed(1)}%</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Cost & Progress */}
        {tab === 'progress' && (
          <div style={{ maxWidth: 840 }}>
            <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
              <input
                type="text"
                placeholder="输入任务 ID (选填，留空展示全局任务进度)..."
                value={taskIdInput}
                onChange={(e) => setTaskIdInput(e.target.value)}
                style={{ flex: 1, padding: '8px 14px', borderRadius: 6, background: 'var(--ui-surface-2, #171B25)', color: '#fff', border: '1px solid var(--ui-line, rgba(255,255,255,0.1))' }}
              />
              <button className="btn btn-sm btn-primary" onClick={() => loadCost(taskIdInput)}>查询进度</button>
            </div>

            {loadingCost && <Spinner />}

            {costData && (
              <div style={{ background: 'var(--ui-surface, #12151D)', borderRadius: 12, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))', padding: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16 }}>任务执行进度与剩余预估</h3>

                <div style={{ marginBottom: 20 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 8 }}>
                    <span>步骤完成度: {costData.steps_completed} / {costData.max_steps ?? '未设上限'} 步</span>
                    <span style={{ fontWeight: 700, color: '#35E0C8' }}>
                      {costData.step_progress_pct !== null ? `${Math.round(costData.step_progress_pct)}%` : '动态执行中'}
                    </span>
                  </div>
                  <div style={{ height: 10, background: 'rgba(255,255,255,0.08)', borderRadius: 5, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%',
                      width: `${costData.step_progress_pct ?? 25}%`,
                      background: 'linear-gradient(90deg, #35E0C8, #8A7BFF)',
                      borderRadius: 5,
                    }} />
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24, fontSize: 13 }}>
                  <div style={{ padding: 14, background: 'var(--ui-surface-2, #171B25)', borderRadius: 8 }}>
                    <span style={{ color: 'var(--ui-muted, #8A93A8)' }}>剩余步数估计上限: </span>
                    <strong style={{ color: '#EAECF2' }}>{costData.remaining_steps_upper_bound ?? '自适应'} 步</strong>
                  </div>
                  <div style={{ padding: 14, background: 'var(--ui-surface-2, #171B25)', borderRadius: 8 }}>
                    <span style={{ color: 'var(--ui-muted, #8A93A8)' }}>预计剩余时间 (基于历史均耗): </span>
                    <strong style={{ color: '#35E0C8' }}>
                      {costData.estimated_remaining_ms !== null ? `${(costData.estimated_remaining_ms / 1000).toFixed(1)} 秒` : '动态评估中'}
                    </strong>
                  </div>
                </div>

                <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>Token 预算账本</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                  <div style={{ padding: 12, background: 'rgba(245,194,107,0.08)', border: '1px solid rgba(245,194,107,0.2)', borderRadius: 8 }}>
                    <div style={{ fontSize: 12, color: '#F5C26B', marginBottom: 4 }}>已锁定 (Reserved)</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{costData.budget_tokens.reserved} Tokens</div>
                  </div>
                  <div style={{ padding: 12, background: 'rgba(53,224,200,0.08)', border: '1px solid rgba(53,224,200,0.2)', borderRadius: 8 }}>
                    <div style={{ fontSize: 12, color: '#35E0C8', marginBottom: 4 }}>已结算 (Settled)</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{costData.budget_tokens.settled} Tokens</div>
                  </div>
                  <div style={{ padding: 12, background: 'rgba(138,123,255,0.08)', border: '1px solid rgba(138,123,255,0.2)', borderRadius: 8 }}>
                    <div style={{ fontSize: 12, color: '#8A7BFF', marginBottom: 4 }}>浮动预估 (Unknown)</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{costData.budget_tokens.unknown} Tokens</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Tab 4: Trace */}
        {tab === 'trace' && (
          <div style={{ background: 'var(--ui-surface, #12151D)', borderRadius: 12, padding: 20, border: '1px solid var(--ui-line, rgba(255,255,255,0.08))' }}>
            <TraceView />
          </div>
        )}
      </div>
    </BaseBound>
  );
}
