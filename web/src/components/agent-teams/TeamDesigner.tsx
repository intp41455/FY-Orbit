import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  teamsApi,
  type BindingView,
  type HostCapability,
  type ModelOption,
  type TeamCatalog,
  type TeamEventItem,
  type TeamMemberView,
  type TeamSnapshot,
  type TeamSummary,
} from '../../api/teams';
import { TeamCanvas } from './TeamCanvas';
import { BusPanel } from './BusPanel'; // W7：团队房间会话（只读渲染 + 发言，挂在本侧栏）
import { SCOPE_LABEL } from './teamVisual';
import { errorMessage } from '../ui';

/**
 * 19 号团队画布设计器（21 号布局）。
 *
 * 视觉与信息层级照搬已批准基准：团队工具条在画布上方，点击成员在右侧栏
 * 一次展开「模型与职责」，低频参数折叠。所有数据来自真实 `/api/teams`，
 * 没有演示数字、没有 alert 冒充成功、没有把 unknown 显示成具体版本。
 */

function fmtUsd(v: number): string {
  return `$${Number(v || 0).toFixed(4)}`;
}

/** 连线几何：三次贝塞尔 + 中点标签。 */
export function TeamDesigner() {
  const [catalog, setCatalog] = useState<TeamCatalog | null>(null);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [snapshot, setSnapshot] = useState<TeamSnapshot | null>(null);
  const [events, setEvents] = useState<TeamEventItem[]>([]);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [binding, setBinding] = useState<BindingView | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>('');
  // W7：会话面板是否展开（懒挂载，未展开时不发任何 /api/bus 请求）
  const [chatOpen, setChatOpen] = useState(false);

  // New-team form state
  const [templateId, setTemplateId] = useState('engineering');
  const [mode, setMode] = useState<'system_managed' | 'product_native'>('system_managed');
  const [defaultModel, setDefaultModel] = useState('');
  const [teamName, setTeamName] = useState('');
  const [goalDraft, setGoalDraft] = useState('');

  const cursorRef = useRef(0);

  const loadCatalog = useCallback(async () => {
    try {
      const c = await teamsApi.catalog();
      setCatalog(c);
      setDefaultModel((prev) => prev || c.models[0]?.model_id || '');
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  const loadTeams = useCallback(async () => {
    try {
      const r = await teamsApi.list();
      setTeams(r.items);
      if (r.items.length > 0) await openTeam(r.items[0].id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  const openTeam = useCallback(async (teamId: string) => {
    setError(null);
    try {
      const snap = await teamsApi.get(teamId);
      setSnapshot(snap);
      cursorRef.current = 0;
      const ev = await teamsApi.events(teamId, 0);
      setEvents(ev.items);
      cursorRef.current = ev.next_cursor;
      setSelectedRole((prev) => prev ?? snap.members[0]?.role ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await loadCatalog();
      await loadTeams();
    })();
  }, [loadCatalog, loadTeams]);

  const selected: TeamMemberView | null = useMemo(
    () => snapshot?.members.find((m) => m.role === selectedRole) ?? null,
    [snapshot, selectedRole],
  );

  const hostFor = useCallback(
    (agentHost: string): HostCapability | undefined =>
      catalog?.hosts.find((h) => h.agent_host === agentHost),
    [catalog],
  );

  const modelsForHost = useCallback(
    (agentHost: string): ModelOption[] => {
      const cap = hostFor(agentHost);
      if (!cap?.supports_per_member_model) return [];
      return catalog?.models ?? [];
    },
    [catalog, hostFor],
  );

  // Resolve the selected node's inheritance chain whenever the selection or the
  // team changes, so the sidebar always shows the true source of the request.
  useEffect(() => {
    if (!snapshot || !selectedRole) { setBinding(null); return; }
    let cancelled = false;
    void (async () => {
      try {
        const data = await teamsApi.resolveBinding(snapshot.team.id, selectedRole);
        if (!cancelled) setBinding(data);
      } catch { /* the node panel still renders from the snapshot */ }
    })();
    return () => { cancelled = true; };
  }, [snapshot, selectedRole]);

  async function refreshEvents() {
    if (!snapshot) return;
    try {
      const ev = await teamsApi.events(snapshot.team.id, cursorRef.current);
      if (ev.items.length > 0) {
        setEvents((prev) => [...prev, ...ev.items]);
        cursorRef.current = ev.next_cursor;
      }
    } catch { /* transient; the next action refreshes */ }
  }

  async function run<T>(fn: () => Promise<T>, okNotice: string): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      const out = await fn();
      setNotice(okNotice);
      return out;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function createTeam() {
    const tmpl = catalog?.templates.find((t) => t.id === templateId);
    const binding = defaultModel
      ? { provider_id: catalog?.models.find((m) => m.model_id === defaultModel)?.provider_id ?? '', model_id: defaultModel }
      : {};
    await run(async () => {
      const snap = await teamsApi.create({
        name: teamName.trim() || tmpl?.name || '新建团队',
        mode,
        template_id: templateId,
        default_binding: binding,
        budget_ref: { root_budget_usd: 0.5, member_reserve_cap_usd: 0.05 },
        reason: '在团队画布新建',
      });
      setTeamName('');
      const list = await teamsApi.list();
      setTeams(list.items);
      setSnapshot(snap);
      setSelectedRole(null);
      const ev = await teamsApi.events(snap.team.id, 0);
      setEvents(ev.items);
      cursorRef.current = ev.next_cursor;
    }, '草稿已创建。启动前会重新核验模型、凭据、预算与依赖。');
  }

  async function startTeam() {
    if (!snapshot) return;
    await run(async () => {
      const snap = await teamsApi.start(snapshot.team.id, snapshot.team.version);
      setSnapshot(snap);
      setSelectedRole((prev) => prev ?? snap.members[0]?.role ?? null);
      await refreshEvents();
    }, '团队已启动：每位成员使用独立会话，模型解析已冻结。');
  }

  async function control(operation: string, extra: Record<string, unknown> = {}, note = '') {
    if (!snapshot || !selected) return;
    await run(async () => {
      await teamsApi.control(snapshot.team.id, {
        operation,
        role: selected.role,
        expected_version: selected.version,
        scope: extra,
        reason: note || `owner ${operation}`,
      });
      const snap = await teamsApi.get(snapshot.team.id);
      setSnapshot(snap);
      await refreshEvents();
    }, note);
  }

  async function switchModel(modelId: string) {
    if (!snapshot || !selected) return;
    const disabled = selected.control.disabled_operations;
    if (disabled.includes('switch_model')) {
      setError(`宿主 ${selected.agent_host} 不支持运行中切换模型（${hostFor(selected.agent_host)?.reason ?? ''}）。`);
      return;
    }
    await control('switch_model', { model_id: modelId }, `切换到 ${modelId}：等待安全边界后建立新批次。`);
  }

  async function applyBinding(modelId: string) {
    if (!snapshot || !selectedRole) return;
    const opt = catalog?.models.find((m) => m.model_id === modelId);
    await run(async () => {
      await teamsApi.setMemberBinding(snapshot.team.id, selectedRole, {
        provider_id: opt?.provider_id ?? '',
        model_id: modelId,
        reason: modelId ? `节点覆盖为 ${modelId}` : '恢复继承',
      });
      const snap = await teamsApi.get(snapshot.team.id);
      setSnapshot(snap);
      await refreshEvents();
    }, modelId ? `已设为节点覆盖：${modelId}` : '已恢复继承；在途批次不受影响。');
  }

  async function updateGoal() {
    if (!snapshot || !goalDraft.trim()) return;
    await run(async () => {
      const r = await teamsApi.updateGoal(snapshot.team.id, goalDraft.trim(), 'owner 修改目标');
      setGoalDraft('');
      const snap = await teamsApi.get(snapshot.team.id);
      setSnapshot(snap);
      await refreshEvents();
      setNotice(`目标已更新到计划版本 ${r.plan_version}，影响 ${r.affected_members.length} 位成员。`);
    }, '目标已更新。');
  }

  // ------------------------------------------------------------------ render
  if (!catalog) {
    return <div className="card"><div className="muted" role="status">正在加载团队能力目录…</div></div>;
  }

  const v = snapshot?.validation;
  const activeHost = hostFor(
    snapshot?.members.find((m) => m.role === selectedRole)?.agent_host ?? 'find_yourself',
  );
  const nodeModels = selected ? modelsForHost(selected.agent_host) : [];
  const perMemberModelDisabled = !selected?.control.supports_per_member_model;

  return (
    <div>
      <header className="page-head">
        <div>
          <h2>一个入口，组织你的 AI 团队</h2>
          <p className="muted" style={{ margin: '0.25rem 0 0' }}>
            分工、模型、执行与验收，都在当前任务内。
          </p>
        </div>
        <span className="badge accent">
          {catalog.real_model_configured ? '真实模型已配置' : '真实模型未配置 · BLOCKED_EXTERNAL'}
        </span>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}

      <section className="card glass-panel" aria-label="团队工具条">
        <div className="team-toolbar">
          <label>
            运行模式
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as 'system_managed' | 'product_native')}
            >
              <option value="system_managed">系统管理团队（推荐）</option>
              <option value="product_native">产品原生团队</option>
            </select>
          </label>
          <label>
            团队模板
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {catalog.templates.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </label>
          <label>
            团队默认模型
            <select value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}>
              <option value="">（不设置，需逐节点指定）</option>
              {catalog.models.map((m) => (
                <option key={`${m.provider_id}:${m.model_id}`} value={m.model_id}>
                  {m.model_id}
                  {m.synthetic ? '（合成）' : ''} · {m.provider_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            团队名称
            <input
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              placeholder="留空则用模板名"
            />
          </label>
          <button type="button" className="primary" onClick={() => void createTeam()} disabled={busy}>
            新建团队草稿
          </button>
        </div>

        <p className="team-hint">
          {mode === 'system_managed'
            ? '同一供应商也可组成团队；每位成员使用独立会话。模型按 节点 → 角色 → 团队 → 全局 顺序继承，启动时冻结解析结果。'
            : '映射外部产品实际创建的子 Agent。能力未经核验时显示「待核验」并禁用对应控件；没有可编程接口时仅提供人工交接。'}
        </p>

        {teams.length > 0 && (
          <div className="tabs-container" style={{ marginTop: '0.75rem' }}>
            {teams.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`tab-btn btn btn-sm ${snapshot?.team.id === t.id ? 'active' : ''}`}
                onClick={() => void openTeam(t.id)}
              >
                <span className={`badge ${t.state === 'running' ? 'ok' : ''}`}>{t.state}</span>
                {t.name}
              </button>
            ))}
          </div>
        )}
      </section>

      {snapshot && (
        <section className="card glass-panel" aria-label="团队画布">
          <div className="team-grid">
            <div className="team-map">
              <TeamCanvas
                snapshot={snapshot}
                selectedRole={selectedRole}
                onSelectRole={setSelectedRole}
              />
            </div>
            {/* ------------------------------------------------ 节点侧栏 */}
            <aside className="team-inspector" aria-label="节点设置">
              <div className="muted">选中成员</div>
              {selected ? (
                <>
                  <h3>{selected.title || selected.role}</h3>
                  <div className="kv"><span>状态</span><span>{selected.state}</span></div>
                  <div className="kv"><span>角色</span><span>{selected.role}</span></div>
                  <div className="kv"><span>Agent 宿主</span><span>{selected.agent_host}</span></div>
                  <div className="kv"><span>供应商</span><span>{selected.provider_id || '未设置'}</span></div>
                  <div className="kv">
                    <span>独立会话</span>
                    <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.72rem' }}>
                      {selected.session_id.slice(0, 18)}…
                    </span>
                  </div>
                  <div className="kv"><span>依赖</span><span>{selected.depends_on.join('、') || '无'}</span></div>
                  <div className="kv"><span>运行批次</span><span>#{selected.run_batch}</span></div>
                  <div className="kv"><span>计划版本</span><span>v{selected.plan_version}</span></div>
                  <div className="kv">
                    <span>凭据引用</span>
                    <span>
                      {selected.credential_ref || '未设置'} ·{' '}
                      {selected.credential_configured ? '已配置' : '未配置'}
                    </span>
                  </div>
                  <div className="kv">
                    <span>成员预留</span>
                    <span>{fmtUsd(selected.budget_reserved_usd)}</span>
                  </div>
                  <div className="kv">
                    <span>成员已用</span>
                    <span>{fmtUsd(selected.budget_spent_usd)}</span>
                  </div>

                  <label htmlFor="node-model">请求模型（节点覆盖）</label>
                  <select
                    id="node-model"
                    value={selected.requested_model}
                    disabled={busy || perMemberModelDisabled}
                    onChange={(e) => void applyBinding(e.target.value)}
                  >
                    <option value="">（继承，不覆盖）</option>
                    {nodeModels.map((m) => (
                      <option key={`${m.provider_id}:${m.model_id}`} value={m.model_id}>
                        {m.model_id}{m.synthetic ? '（合成）' : ''}
                      </option>
                    ))}
                  </select>
                  <p className="small-text">
                    继承来源：{SCOPE_LABEL[binding?.inherited_from ?? selected.inherited_from] ?? '未解析'}
                    {binding && binding.chain.length > 0 && (
                      <> · 顺序 {binding.chain.map((c) => SCOPE_LABEL[c.scope] ?? c.scope).join(' → ')}</>
                    )}
                  </p>

                  {perMemberModelDisabled && (
                    <p className="team-hint">
                      逐成员模型覆盖：宿主 {selected.agent_host} 不支持
                      {activeHost ? `（${activeHost.reason}）` : ''}。控件已禁用，未伪装设置成功。
                    </p>
                  )}

                  <label htmlFor="switch-model">运行中切换模型</label>
                  <select
                    id="switch-model"
                    value=""
                    disabled={busy || selected.control.disabled_operations.includes('switch_model')}
                    onChange={(e) => { if (e.target.value) void switchModel(e.target.value); }}
                  >
                    <option value="">选择目标模型…</option>
                    {catalog.models
                      .filter((m) => m.model_id !== selected.requested_model)
                      .map((m) => (
                        <option key={m.model_id} value={m.model_id}>
                          {m.model_id}{m.synthetic ? '（合成）' : ''}
                        </option>
                      ))}
                  </select>
                  <p className="small-text">
                    切换只在安全边界之后的新批次生效；旧批次保留，副作用未知时先核对。
                  </p>

                  <div className="row">
                    <button
                      type="button" className="small"
                      disabled={busy}
                      onClick={() => void control(selected.state === 'paused' ? 'resume' : 'pause')}
                    >
                      {selected.state === 'paused' ? '恢复' : '暂停'}
                    </button>
                    <button
                      type="button" className="small"
                      disabled={busy}
                      onClick={() => void control('rework', {}, '要求返工')}
                    >
                      要求返工
                    </button>
                    <button
                      type="button" className="small danger"
                      disabled={busy}
                      onClick={() => void control('cancel', {}, '用户取消')}
                    >
                      取消
                    </button>
                  </div>

                  <details>
                    <summary>权限、预算与高级参数</summary>
                    <div className="kv">
                      <span>根预算</span>
                      <span>{fmtUsd(Number(snapshot.team.budget_ref.root_budget_usd ?? 0))}</span>
                    </div>
                    <p>成员共享根预算，重试同样计费；重试与继续派生不能绕过根限额。</p>
                    <div className="kv">
                      <span>单成员预留上限</span>
                      <span>{fmtUsd(Number(snapshot.team.budget_ref.member_reserve_cap_usd ?? 0))}</span>
                    </div>
                    <div className="kv">
                      <span>最大步骤</span>
                      <span>{String(snapshot.team.budget_ref.max_steps ?? 8)}</span>
                    </div>
                    <div className="kv">
                      <span>自动回退</span>
                      <span>{snapshot.team.permission_ref.allow_fallback ? '已授权' : '未授权'}</span>
                    </div>
                    <div className="kv">
                      <span>跨供应商</span>
                      <span>{snapshot.team.permission_ref.allow_cross_provider ? '已授权' : '未授权'}</span>
                    </div>
                    <p className="small-text">
                      角色分工不授予额外权限；凭据只以服务端引用传递，不进入提示词、导出或日志。
                    </p>
                  </details>

                  <details>
                    <summary>阻塞与能力状态</summary>
                    {activeHost ? (
                      <>
                        <p>能力探测来源：{activeHost.probe_source}</p>
                        <ul className="blocker-list">
                          {Object.entries(activeHost.capabilities).map(([op, state]) => (
                            <li
                              key={op}
                              style={{
                                background: state === 'verified'
                                  ? 'rgba(224, 242, 254, 0.85)'
                                  : state === 'unsupported'
                                    ? 'var(--rose-bg)'
                                    : 'var(--amber-bg)',
                                color: state === 'verified'
                                  ? 'var(--sky-deep)'
                                  : state === 'unsupported' ? 'var(--rose)' : 'var(--amber)',
                                borderColor: state === 'verified'
                                  ? 'rgba(56, 189, 248, 0.4)'
                                  : state === 'unsupported' ? 'var(--rose-border)' : 'var(--amber-border)',
                              }}
                            >
                              {op}：{state === 'verified' ? '已核验' : state === 'unsupported' ? '不支持' : '待核验'}
                            </li>
                          ))}
                        </ul>
                        <p className="small-text">{activeHost.reason}</p>
                      </>
                    ) : (
                      <p className="muted">该宿主尚无能力记录。</p>
                    )}
                    {selected.blocked_reason && (
                      <div className="notice danger">{selected.blocked_reason}</div>
                    )}
                  </details>
                </>
              ) : (
                <p className="muted">点击左侧任一成员节点，在此调整职责、模型与控制操作。</p>
              )}

              {snapshot && (
                <details>
                  <summary>修改任务目标</summary>
                  <textarea
                    value={goalDraft}
                    onChange={(e) => setGoalDraft(e.target.value)}
                    placeholder="新的目标；保存后所有受影响成员看到新的计划版本"
                  />
                  <button
                    type="button" className="small"
                    style={{ marginTop: '0.5rem' }}
                    disabled={busy || !goalDraft.trim()}
                    onClick={() => void updateGoal()}
                  >
                    更新目标并传播
                  </button>
                </details>
              )}

              <details open>
                <summary>最近事件（断线可续传）</summary>
                <ul className="event-log">
                  {events.slice(-40).map((e) => (
                    <li key={e.seq}>
                      <span className="seq">#{e.seq}</span>
                      <span className="etype">{e.event_type}</span>
                      <span className="muted">
                        {e.run_batch != null ? `批次 ${e.run_batch} · ` : ''}
                        {String(e.details.role ?? '')}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>

              {/* ---- W7：团队房间会话（Agent 通信总线）。懒挂载：未展开时不发请求 ---- */}
              <details onToggle={(e) => setChatOpen((e.currentTarget as HTMLDetailsElement).open)}>
                <summary>会话 · @成员即可让它作答</summary>
                {chatOpen && (
                  <BusPanel
                    room={snapshot.team.id}
                    members={snapshot.team.members.map((m) => ({ role: m.role, title: m.title }))}
                  />
                )}
              </details>

              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => { if (snapshot) void openTeam(snapshot.team.id); }}
              >
                刷新状态
              </button>
            </aside>
          </div>

          {/* ------------------------------------------------ 启动门禁 */}
          <div style={{ marginTop: '1.25rem', borderTop: '1px solid var(--line)', paddingTop: '1rem' }}>
            <div className="row spread">
              <div>
                <strong>下一步：{snapshot.team.state === 'running' ? '观察执行与独立验证' : '核验模型与授权后启动'}</strong>
                <p className="muted" role="status" aria-live="polite" style={{ margin: '0.2rem 0 0' }}>
                  {notice || `草稿 v${snapshot.team.version} · 未产生真实费用`}
                </p>
              </div>
              <button
                type="button"
                className="primary"
                disabled={busy || !v?.can_start || snapshot.team.state === 'running'}
                onClick={() => void startTeam()}
              >
                检查并启动团队
              </button>
            </div>

            {v && v.blockers.length > 0 && (
              <ul className="blocker-list" style={{ marginTop: '0.75rem' }}>
                {v.blockers.map((b, i) => (
                  <li key={i}>{b.role ? `[${b.role}] ` : ''}{b.message}</li>
                ))}
              </ul>
            )}
            {v && v.warnings.length > 0 && (
              <ul className="blocker-list warning-list" style={{ marginTop: '0.5rem' }}>
                {v.warnings.map((b, i) => (
                  <li key={i}>{b.role ? `[${b.role}] ` : ''}{b.message}</li>
                ))}
              </ul>
            )}
            {!catalog.real_model_configured && (
              <div className="notice warn" style={{ marginTop: '0.75rem' }}>
                真实模型凭据未配置（FY_MODEL_API_KEY / FY_MODEL_BASE_URL）。当前只列出本地确定性
                合成模型；真实模型往返为 BLOCKED_EXTERNAL，本页不冒充真实模型成功。
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}