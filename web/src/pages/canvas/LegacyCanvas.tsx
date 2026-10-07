import { useEffect, useState } from 'react';
import {
  canvasApi,
  type CanvasDispatch,
  type CanvasEventItem,
  type CanvasHandoff,
  type CanvasInstance,
  type CanvasSnapshot,
  type CanvasTemplate,
  type ConnectorProbe,
} from '../../api/canvas';
import { errorMessage } from '../../components/ui';
import { BaseBound } from '../../components/ui/SaveStatusIndicator';

export function LegacyCanvas() {
  const [templates, setTemplates] = useState<CanvasTemplate[]>([]);
  const [connectors, setConnectors] = useState<ConnectorProbe[]>([]);
  const [instances, setInstances] = useState<CanvasInstance[]>([]);
  const [activeInstance, setActiveInstance] = useState<CanvasInstance | null>(null);
  const [snapshot, setSnapshot] = useState<CanvasSnapshot | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // New instance form
  const [showNewInstance, setShowNewInstance] = useState(false);
  const [newInstanceName, setNewInstanceName] = useState('');
  const [selectedTemplateId, setSelectedTemplateId] = useState('personal');

  // Dispatch form
  const [showDispatch, setShowDispatch] = useState(false);
  const [dispatchWorker, setDispatchWorker] = useState('');
  const [dispatchGoal, setDispatchGoal] = useState('');
  const [dispatchBudget, setDispatchBudget] = useState(0.05);
  const [dispatching, setDispatching] = useState(false);

  // Handoff form
  const [showHandoff, setShowHandoff] = useState(false);
  const [handoffStage, setHandoffStage] = useState('architecture_handoff');
  const [handoffGoal, setHandoffGoal] = useState('');
  const [handoffFrom, setHandoffFrom] = useState('');
  const [handoffTo, setHandoffTo] = useState('');
  const [handoffItems, setHandoffItems] = useState('');

  useEffect(() => {
    loadInitialData();
  }, []);

  async function loadInitialData() {
    setLoading(true);
    setError(null);
    try {
      const [tmplRes, connRes, instRes] = await Promise.all([
        canvasApi.templates(),
        canvasApi.connectors(),
        canvasApi.listInstances(),
      ]);
      setTemplates(tmplRes.items);
      setConnectors(connRes.items);
      setInstances(instRes.items);
      if (instRes.items.length > 0) {
        selectInstance(instRes.items[0], tmplRes.items);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function selectInstance(inst: CanvasInstance, currentTemplates?: CanvasTemplate[]) {
    setActiveInstance(inst);
    try {
      const snap = await canvasApi.getSnapshot(inst.id);
      setSnapshot(snap);
      const tmplList = currentTemplates ?? templates;
      const tmpl = tmplList.find((t) => t.id === inst.template_id);
      if (tmpl && tmpl.workers.length > 0) {
        setDispatchWorker(tmpl.workers[0]);
        setHandoffFrom(inst.orchestrator_id);
        setHandoffTo(tmpl.workers[0]);
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleCreateInstance(e: React.FormEvent) {
    e.preventDefault();
    if (!newInstanceName.trim()) return;
    try {
      const inst = await canvasApi.createInstance({
        project_name: newInstanceName.trim(),
        template_id: selectedTemplateId,
      });
      setInstances((old) => [inst, ...old]);
      setShowNewInstance(false);
      setNewInstanceName('');
      selectInstance(inst, templates);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleDispatchSubtask(e: React.FormEvent) {
    e.preventDefault();
    const targetWorker = dispatchWorker || activeInstance?.orchestrator_id || activeTemplate?.workers?.[0] || 'Hermes';
    if (!activeInstance || !dispatchGoal.trim() || !targetWorker) {
      return;
    }
    if (dispatchBudget > 0.50) {
      setError('单次派发预算切片最高不可超过 $0.50');
      return;
    }
    setDispatching(true);
    try {
      await canvasApi.dispatchSubtask(activeInstance.id, {
        root_task_id: `root-${activeInstance.id}`,
        worker_id: targetWorker,
        goal: dispatchGoal.trim(),
        budget_slice: dispatchBudget,
      });
      setShowDispatch(false);
      setDispatchGoal('');
      // Refresh snapshot
      await selectInstance(activeInstance);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDispatching(false);
    }
  }

  async function handleRecordHandoff(e: React.FormEvent) {
    e.preventDefault();
    const fromWorker = handoffFrom || activeInstance?.orchestrator_id;
    const toWorker = handoffTo || activeTemplate?.workers[0];
    if (!activeInstance || !handoffGoal.trim() || !fromWorker || !toWorker) return;
    try {
      const items = handoffItems
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
      await canvasApi.recordHandoff(activeInstance.id, {
        stage: handoffStage,
        goal: handoffGoal.trim(),
        source_worker_id: fromWorker,
        target_worker_id: toWorker,
        source_task_id: `task-${Date.now().toString(36)}`,
        completed_items: items.length > 0 ? items : ['阶段工作按规格交付'],
      });
      setShowHandoff(false);
      setHandoffGoal('');
      setHandoffItems('');
      selectInstance(activeInstance);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function getStageBadgeClass(stage: string): string {
    switch (stage) {
      case '生产可用':
      case '合成任务往返':
      case '本机握手通过':
        return 'badge-ok';
      case '发现接口':
        return 'badge-primary';
      default:
        return 'badge-neutral';
    }
  }

  function getDispatchBadge(state: string) {
    switch (state) {
      case 'completed':
        return <span className="badge badge-ok">已完成 (completed)</span>;
      case 'dispatched':
        return <span className="badge badge-primary">已派发 (dispatched)</span>;
      case 'pending_adapter':
        return <span className="badge badge-neutral" style={{ background: '#fef3c7', color: '#92400e' }}>待接入 / 计划派发 (pending_adapter)</span>;
      case 'failed':
        return <span className="badge badge-neutral" style={{ background: '#fee2e2', color: '#991b1b' }}>失败 (failed)</span>;
      default:
        return <span className="badge badge-neutral">{state}</span>;
    }
  }

  const activeTemplate = templates.find((t) => t.id === activeInstance?.template_id);

  return (
    <BaseBound surface="legacy-canvas">
      <div className="page-container">
        <header className="page-header">
          <div>
            <h2>多 Agent 协作可视化画布 (05 Collaboration Canvas)</h2>
            <p className="subtext">
              统一编排拓扑与实机连接探测。支持私人事务 (Hermes) 与工作工程 (Codex) 模板，预算切片派发、结构化交接包与只读游标事件重放。
            </p>
          </div>
          <button
            className="btn btn-secondary"
            onClick={() => setShowNewInstance(!showNewInstance)}
          >
            {showNewInstance ? '取消' : '+ 新建画布项目'}
          </button>
        </header>

        {error && <div className="banner banner-error">{error}</div>}

        {/* New Canvas Instance Modal / Inline Form */}
        {showNewInstance && (
          <form className="card form-inline" onSubmit={handleCreateInstance} style={{ marginBottom: '1.5rem' }}>
            <h4>新建协作画布</h4>
            <div className="form-group">
              <label htmlFor="new-inst-name">项目名称</label>
              <input
                id="new-inst-name"
                type="text"
                required
                placeholder="例如：自我探索系统重构、个人事务自动化"
                value={newInstanceName}
                onChange={(e) => setNewInstanceName(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label htmlFor="new-inst-tmpl">编排模板</label>
              <select
                id="new-inst-tmpl"
                value={selectedTemplateId}
                onChange={(e) => setSelectedTemplateId(e.target.value)}
              >
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.domain === 'personal' ? '私人域' : '工作域'} · 主控: {t.center})
                  </option>
                ))}
              </select>
            </div>
            <button type="submit" className="btn btn-primary">立即创建</button>
          </form>
        )}

        {/* Instance Tabs */}
        <div className="tabs-container" style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
          {instances.map((i) => (
            <button
              key={i.id}
              className={`tab-btn ${activeInstance?.id === i.id ? 'active' : ''}`}
              onClick={() => selectInstance(i)}
            >
              <span className={`badge ${i.domain === 'personal' ? 'badge-primary' : 'badge-neutral'}`}>
                {i.domain === 'personal' ? '私人' : '工作'}
              </span>
              <strong>{i.project_name}</strong>
            </button>
          ))}
        </div>

        {activeInstance && snapshot ? (
          <div>
            {/* Canvas Topology Card */}
            <div className="card" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <h3 style={{ margin: 0 }}>协作拓扑: {activeInstance.project_name}</h3>
                  <small className="subtext">
                    主控 Agent: <strong>{activeInstance.orchestrator_id}</strong> · 模板: {activeTemplate?.name || activeInstance.template_id} · 状态: {activeInstance.state}
                  </small>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button className="btn btn-secondary" onClick={() => setShowDispatch(!showDispatch)}>
                    {showDispatch ? '取消派发' : '派发子任务'}
                  </button>
                  <button className="btn btn-secondary" onClick={() => setShowHandoff(!showHandoff)}>
                    {showHandoff ? '取消交接' : '记录交接包'}
                  </button>
                </div>
              </div>

              {/* Subtask Dispatch Form */}
              {showDispatch && (
                <form onSubmit={handleDispatchSubtask} style={{ marginTop: '1rem', padding: '1rem', background: '#f8fafc', borderRadius: '6px', border: '1px solid #cbd5e1' }}>
                  <h5>派发受约束子任务 (Subtask Dispatch)</h5>
                  <div style={{ fontSize: '0.8rem', color: '#64748b', marginBottom: '0.5rem' }}>
                    单次派发预算强制限制在 $0.50 以内；未就绪的 Worker 将记录为待接入 (pending_adapter) 计划事件。
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', margin: '0.5rem 0' }}>
                    <div className="form-group" style={{ flex: 1 }}>
                      <label htmlFor="dispatch-worker-select">目标执行 Worker</label>
                      <select
                        id="dispatch-worker-select"
                        value={dispatchWorker}
                        onChange={(e) => setDispatchWorker(e.target.value)}
                      >
                        {!activeTemplate?.workers.includes(activeInstance.orchestrator_id) && (
                          <option value={activeInstance.orchestrator_id}>
                            {activeInstance.orchestrator_id} (主控执行)
                          </option>
                        )}
                        {activeTemplate?.workers.map((w) => {
                          const probe = connectors.find((c) => c.name.toLowerCase() === w.toLowerCase());
                          const isReady = probe?.healthy;
                          return (
                            <option key={w} value={w}>
                              {w} {isReady ? '(就绪)' : '(待接入/计划派发)'}
                            </option>
                          );
                        })}
                      </select>
                    </div>
                    <div className="form-group" style={{ width: '150px' }}>
                      <label htmlFor="dispatch-budget-input">预算切片 ($ ≤ 0.50)</label>
                      <input
                        id="dispatch-budget-input"
                        type="number"
                        step="0.01"
                        min="0.01"
                        max="0.50"
                        value={isNaN(dispatchBudget) ? '' : dispatchBudget}
                        onChange={(e) => setDispatchBudget(parseFloat(e.target.value) || 0)}
                      />
                    </div>
                  </div>
                  <div className="form-group">
                    <label htmlFor="dispatch-goal-input">目标描述 (Goal)</label>
                    <input
                      id="dispatch-goal-input"
                      type="text"
                      required
                      placeholder="明确单一目标，如：编写集成测试用例、执行代码静态检查"
                      value={dispatchGoal}
                      onChange={(e) => setDispatchGoal(e.target.value)}
                    />
                  </div>
                  <button type="submit" className="btn btn-primary btn-sm" disabled={dispatching}>
                    {dispatching ? '派发执行中...' : '确认派发'}
                  </button>
                </form>
              )}

              {/* Handoff Packet Form */}
              {showHandoff && (
                <form onSubmit={handleRecordHandoff} style={{ marginTop: '1rem', padding: '1rem', background: '#f8fafc', borderRadius: '6px', border: '1px solid #cbd5e1' }}>
                  <h5>记录跨 Agent 结构化交接包 (Structured Handoff Packet)</h5>
                  <div style={{ display: 'flex', gap: '1rem', margin: '0.5rem 0' }}>
                    <div className="form-group" style={{ flex: 1 }}>
                      <label>交接阶段</label>
                      <input type="text" value={handoffStage} onChange={(e) => setHandoffStage(e.target.value)} />
                    </div>
                    <div className="form-group" style={{ width: '180px' }}>
                      <label>从 (From)</label>
                      <input type="text" value={handoffFrom} onChange={(e) => setHandoffFrom(e.target.value)} />
                    </div>
                    <div className="form-group" style={{ width: '180px' }}>
                      <label>至 (To)</label>
                      <input type="text" value={handoffTo} onChange={(e) => setHandoffTo(e.target.value)} />
                    </div>
                  </div>
                  <div className="form-group">
                    <label>交接目标 (Goal)</label>
                    <input type="text" required value={handoffGoal} onChange={(e) => setHandoffGoal(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label>已完成交付项 (每行一项，拒绝全量聊天倾倒)</label>
                    <textarea rows={3} value={handoffItems} onChange={(e) => setHandoffItems(e.target.value)} placeholder="功能代码实现完毕&#10;单元测试全部通过" />
                  </div>
                  <button type="submit" className="btn btn-primary btn-sm">保存交接记录</button>
                </form>
              )}

              {/* Visual Topology Nodes */}
              <div style={{ marginTop: '1.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '2rem', flexWrap: 'wrap' }}>
                {/* User Node */}
                <div style={{ textAlign: 'center', padding: '1rem', background: '#f1f5f9', borderRadius: '8px', minWidth: '130px', border: '1px solid #cbd5e1' }}>
                  <div style={{ fontSize: '1.5rem' }}>👤</div>
                  <strong>使用者 (Owner)</strong>
                  <div style={{ fontSize: '0.75rem', color: '#64748b' }}>目标 · 授权 · 验收</div>
                </div>

                <div style={{ color: '#94a3b8', fontSize: '1.25rem' }}>➔</div>

                {/* Orchestrator Center Node */}
                <div style={{ textAlign: 'center', padding: '1.25rem', background: '#eff6ff', borderRadius: '8px', minWidth: '160px', border: '2px solid #3b82f6' }}>
                  <div style={{ fontSize: '1.5rem' }}>⭐</div>
                  <strong>{activeInstance.orchestrator_id}</strong>
                  <div style={{ fontSize: '0.75rem', color: '#1d4ed8' }}>主控协同中心 (Center)</div>
                  <span className="badge badge-primary" style={{ marginTop: '0.25rem' }}>Orchestrator</span>
                </div>

                <div style={{ color: '#94a3b8', fontSize: '1.25rem' }}>➔</div>

                {/* Workers Nodes */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {activeTemplate?.workers.map((workerName) => {
                    const probe = connectors.find((c) => c.name.toLowerCase() === workerName.toLowerCase());
                    return (
                      <div
                        key={workerName}
                        style={{
                          padding: '0.75rem 1rem',
                          background: '#ffffff',
                          borderRadius: '6px',
                          border: '1px solid #e2e8f0',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: '1rem',
                          minWidth: '240px',
                        }}
                      >
                        <div>
                          <strong>{workerName}</strong>
                          <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                            {probe ? probe.protocol : '标准 Agent 协议'}
                          </div>
                        </div>
                        <span className={`badge ${getStageBadgeClass(probe?.stage || '仅设计')}`}>
                          {probe?.stage || '仅设计'}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Subtask Dispatches and Handoffs Grid */}
            <div className="grid grid-2" style={{ gap: '1.5rem', marginBottom: '1.5rem' }}>
              {/* Dispatches Card */}
              <div className="card">
                <h4>子任务派发记录 ({snapshot.dispatches.length})</h4>
                {snapshot.dispatches.length === 0 ? (
                  <p className="subtext">暂无派发记录。点击上方「派发子任务」分配工作。</p>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.5rem' }}>
                    {snapshot.dispatches.map((d: CanvasDispatch) => (
                      <div key={d.id} style={{ padding: '0.75rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <strong>{d.goal}</strong>
                          {getDispatchBadge(d.state)}
                        </div>
                        <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
                          执行者: <strong>{d.worker_id}</strong> · 预算切片: ${d.budget_slice.toFixed(2)} · {new Date(d.created_at).toLocaleTimeString()}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Handoffs Card */}
              <div className="card">
                <h4>结构化交接包 ({snapshot.handoffs.length})</h4>
                {snapshot.handoffs.length === 0 ? (
                  <p className="subtext">暂无跨 Agent 交接记录。结构化交付物在此归档。</p>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.5rem' }}>
                    {snapshot.handoffs.map((h: CanvasHandoff) => (
                      <div key={h.id} style={{ padding: '0.75rem', background: '#f8fafc', borderRadius: '4px', border: '1px solid #e2e8f0' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <strong>{h.from} ➔ {h.to}</strong>
                          <span className="badge badge-ok">{h.stage}</span>
                        </div>
                        <div style={{ fontSize: '0.8rem', margin: '0.25rem 0' }}>
                          交付清单:
                          <ul style={{ margin: '0.25rem 0', paddingLeft: '1.2rem', color: '#334155' }}>
                            {h.completed_items?.map((item, idx) => (
                              <li key={idx}>{item}</li>
                            ))}
                          </ul>
                        </div>
                        <small style={{ color: '#94a3b8' }}>{new Date(h.created_at).toLocaleTimeString()}</small>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Machine Connector Probes Status */}
            <div className="card" style={{ marginBottom: '1.5rem' }}>
              <h4>实机连接探针与状态阶梯 (Authentic Connector Probes)</h4>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
                {connectors.map((c) => (
                  <div
                    key={c.name}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '0.5rem 0.75rem',
                      background: '#f8fafc',
                      borderRadius: '4px',
                      fontSize: '0.85rem',
                    }}
                  >
                    <div>
                      <strong>{c.name}</strong> · <span style={{ color: '#64748b' }}>{c.protocol} ({c.role})</span>
                      {c.binary_path && (
                        <div style={{ fontSize: '0.72rem', color: 'var(--ui-st-complete)', marginTop: '2px' }}>
                          可执行路径: <code>{c.binary_path}</code>
                        </div>
                      )}
                      {c.blocking_reason && (
                        <div style={{ fontSize: '0.75rem', color: '#b45309', marginTop: '2px' }}>
                          阻断原因: {c.blocking_reason}
                        </div>
                      )}
                    </div>
                    <span className={`badge ${getStageBadgeClass(c.stage)}`}>
                      {c.stage}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Canvas Events Timeline */}
            <div className="card">
              <h4>事件时间线 (Canvas Events Replay - {snapshot.events.length})</h4>
              <div style={{ maxHeight: '240px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.35rem', marginTop: '0.5rem' }}>
                {snapshot.events.map((evt: CanvasEventItem) => (
                  <div key={evt.seq} style={{ fontSize: '0.8rem', display: 'flex', gap: '0.75rem', color: '#475569' }}>
                    <span style={{ color: '#94a3b8', width: '32px' }}>#{evt.seq}</span>
                    <span style={{ color: '#0f172a', fontWeight: 500, width: '180px' }}>{evt.event_type}</span>
                    <span style={{ flex: 1 }}>{JSON.stringify(evt.details)}</span>
                    <span style={{ color: '#94a3b8' }}>{new Date(evt.created_at).toLocaleTimeString()}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="card" style={{ textAlign: 'center', padding: '3rem' }}>
            {loading ? '正在加载画布...' : '未选择或创建任何协作画布。'}
          </div>
        )}
      </div>
    </BaseBound>
  );
}
