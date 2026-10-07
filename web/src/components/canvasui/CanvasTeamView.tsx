/**
 * 包 B · 画布域主视图（视觉重做版）。
 *
 * 数据契约与 /api/teams 完全一致；视觉按基准：
 * - 顶部一行工具条（模式/模板/默认模型/新建/一键派发）
 * - 压暗画布区，水平三栏拓扑（左协调者 → 中并行成员 → 右验证节点）
 * - 点成员右侧浮层抽屉，不推挤画布
 * - 九档状态色 + 四通道编码（点+图标+文字+时长）
 *
 * 旧 TeamDesigner 仍作为 legacy tab 保留在 CanvasPage 中，功能不删。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  teamsApi,
  type BindingView,
  type ModelOption,
  type TeamCatalog,
  type TeamEventItem,
  type TeamMemberView,
  type TeamMode,
  type TeamSnapshot,
  type TeamSummary,
} from '../../api/teams';
import { errorMessage } from '../ui';
import { LineIcon } from '../ui/LineIcon';
import { TeamCanvasGraph } from './TeamCanvasGraph';
import { MemberDrawer } from './MemberDrawer';
import { statusMeta } from './statusMap';
import { buildMemberViews } from './memberViews';
import './../../styles/pages/canvas.css';
import { useBase } from '../../hooks/useAutosave';

export function CanvasTeamView() {
  useBase({ surface: 'web/src/components/canvasui/CanvasTeamView' });
  const [catalog, setCatalog] = useState<TeamCatalog | null>(null);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [snapshot, setSnapshot] = useState<TeamSnapshot | null>(null);
  const [events, setEvents] = useState<TeamEventItem[]>([]);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [hoveredRole, setHoveredRole] = useState<string | null>(null);
  const [binding, setBinding] = useState<BindingView | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>('');

  // 新建草稿表单
  const [templateId, setTemplateId] = useState('engineering');
  const [mode, setMode] = useState<TeamMode>('system_managed');
  const [defaultModel, setDefaultModel] = useState('');
  const [teamName, setTeamName] = useState('');
  const [showLegend, setShowLegend] = useState(false);

  /**
   * 窄屏（≤860px）检查器改为常驻堆叠。
   *
   * e2e/ui-team.spec.ts:440 要求窄屏下
   * getByRole('complementary', { name: '节点设置' }) 可见，注释明写
   * 「Node inspector stacks below the map instead of disappearing」——
   * 即窄屏不允许「点了才出」，功能入口不许被截断。
   * 宽屏维持原行为：未选中不渲染，选中后右侧浮层抽屉。
   */
  const [stackedInspector, setStackedInspector] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 860px)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 860px)');
    const on = () => setStackedInspector(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);

  const cursorRef = useRef(0);

  const loadCatalog = useCallback(async () => {
    try {
      const c = await teamsApi.catalog();
      setCatalog(c);
      setDefaultModel((p) => p || c.models[0]?.model_id || '');
    } catch (e) { setError(errorMessage(e)); }
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
    } catch (e) { setError(errorMessage(e)); }
  }, []);

  const loadTeams = useCallback(async () => {
    try {
      const r = await teamsApi.list();
      setTeams(r.items);
      if (r.items.length > 0) await openTeam(r.items[0].id);
    } catch (e) { setError(errorMessage(e)); }
  }, [openTeam]);

  useEffect(() => {
    void (async () => { await loadCatalog(); await loadTeams(); })();
  }, [loadCatalog, loadTeams]);

  // 统一成员视图：draft 用静态定义，running 合并运行时实例
  const memberViews = useMemo(
    () => (snapshot ? buildMemberViews(snapshot) : []),
    [snapshot],
  );

  const selected: TeamMemberView | null = useMemo(
    () => memberViews.find((m) => m.role === selectedRole) ?? null,
    [memberViews, selectedRole],
  );

  const hostFor = useCallback(
    (h: string) => catalog?.hosts.find((x) => x.agent_host === h),
    [catalog],
  );
  const modelsForHost = useCallback(
    (h: string): ModelOption[] => {
      const cap = hostFor(h);
      if (!cap?.supports_per_member_model) return [];
      return catalog?.models ?? [];
    },
    [catalog, hostFor],
  );

  // 选中变化时解析 binding
  useEffect(() => {
    if (!snapshot || !selectedRole) { setBinding(null); return; }
    let cancelled = false;
    void (async () => {
      try {
        const data = await teamsApi.resolveBinding(snapshot.team.id, selectedRole);
        if (!cancelled) setBinding(data);
      } catch { /* 节点面板仍从 snapshot 渲染 */ }
    })();
    return () => { cancelled = true; };
  }, [snapshot, selectedRole]);

  async function run<T>(fn: () => Promise<T>, ok: string): Promise<T | null> {
    setBusy(true); setError(null);
    try {
      const out = await fn();
      setNotice(ok);
      return out;
    } catch (e) { setError(errorMessage(e)); return null; }
    finally { setBusy(false); }
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
      const ev = await teamsApi.events(snap.team.id, 0);
      setEvents(ev.items);
      cursorRef.current = ev.next_cursor;
    }, '团队已启动：每位成员使用独立会话，模型解析已冻结。');
  }

  async function control(
    operation: string,
    extra: Record<string, unknown> = {},
    note = '',
  ) {
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
      const ev = await teamsApi.events(snap.team.id, cursorRef.current);
      if (ev.items.length > 0) {
        setEvents((prev) => [...prev, ...ev.items]);
        cursorRef.current = ev.next_cursor;
      }
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
    }, modelId ? `已设为节点覆盖：${modelId}` : '已恢复继承；在途批次不受影响。');
  }

  async function renameMember(role: string, title: string) {
    if (!snapshot) return;
    await run(async () => {
      // PATCH members 为整体替换：以静态定义为准回传完整成员，仅改目标 title
      const members = snapshot.team.members.map((m) => ({
        role: m.role,
        title: m.role === role ? title : m.title ?? m.role,
        agent_host: m.agent_host ?? 'find_yourself',
        provider_id: m.provider_id ?? '',
        depends_on: m.depends_on ?? [],
        goal: m.goal ?? '',
      }));
      await teamsApi.update(
        snapshot.team.id,
        snapshot.team.version,
        { members },
        `重命名 ${role} 为「${title}」`,
      );
      const snap = await teamsApi.get(snapshot.team.id);
      setSnapshot(snap);
    }, `已改名：${title}`);
  }

  if (!catalog) {
    return <div className="ui-panel ui-panel--pad"><p className="ui-hint">正在加载团队能力目录…</p></div>;
  }

  const v = snapshot?.validation;
  const nodeModels = selected ? modelsForHost(selected.agent_host) : [];

  /**
   * 检查器主体。宽屏挂在画布容器内（右侧浮层），窄屏挂到画布下方（堆叠），
   * 两种形态复用同一个元素，靠 CSS 与挂载位置区分。
   */
  const inspector = selected ? (
    <MemberDrawer
      key="inspector"
      member={selected}
      binding={binding}
      host={hostFor(selected.agent_host)}
      models={nodeModels}
      events={events}
      busy={busy}
      onClose={() => {
        // R6：关闭后把焦点还给触发节点
        const role = selected.role;
        setSelectedRole(null);
        requestAnimationFrame(() => {
          document
            .querySelector<HTMLElement>(`[data-cv-node="${CSS.escape(role)}"]`)
            ?.focus();
        });
      }}
      onApplyBinding={(mid) => void applyBinding(mid)}
      onSwitchModel={(mid) => void switchModel(mid)}
      onControl={(op, note) => void control(op, {}, note)}
    />
  ) : (
    <aside
      key="inspector"
      className="cv-drawer cv-drawer--empty"
      role="complementary"
      aria-label="节点设置"
    >
      <div className="cv-drawer__hd">
        <h3>节点设置</h3>
      </div>
      <div className="cv-drawer__bd">
        <div className="ui-empty" style={{ justifyContent: 'center', padding: '24px 0' }}>
          <LineIcon name="layers" size={32} className="ui-hint" />
          <div className="ui-empty-title">尚未选择节点</div>
          <p className="ui-empty-hint">在上方拓扑中点选一个成员节点，即可查看并调整它的设置。</p>
        </div>
      </div>
    </aside>
  );

  return (
    <div className="cv-page">
      <header className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h2 style={{ margin: 0 }}>一个入口，组织你的 AI 团队</h2>
          <p className="ui-hint" style={{ margin: '2px 0 0' }}>
            分工、模型、执行与验收，都在当前任务内。
          </p>
        </div>
        {!catalog.real_model_configured && (
          <span className="ui-badge ui-badge--waiting">真实模型未配置 · BLOCKED_EXTERNAL</span>
        )}
      </header>

      {/* 顶部工具条 */}
      <div className="cv-topbar team-toolbar">
        <div className="cv-field">
          <label>模式</label>
          <select value={mode} onChange={(e) => setMode(e.target.value as TeamMode)}>
            <option value="system_managed">系统管理团队（推荐）</option>
            <option value="product_native">产品原生团队</option>
          </select>
        </div>
        <div className="cv-field">
          <label>模板</label>
          <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
            {catalog.templates.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </div>
        <div className="cv-field">
          <label>默认模型</label>
          <select value={defaultModel} onChange={(e) => setDefaultModel(e.target.value)}>
            <option value="">（不设置，需逐节点指定）</option>
            {catalog.models.map((m) => (
              <option key={`${m.provider_id}:${m.model_id}`} value={m.model_id}>
                {m.model_id}{m.synthetic ? '（合成）' : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="cv-field">
          <label htmlFor="cv-team-name">团队名称</label>
          <input
            id="cv-team-name"
            type="text"
            value={teamName}
            onChange={(e) => setTeamName(e.target.value)}
            placeholder="留空则用模板名"
          />
        </div>
        <button type="button" className="ui-btn ui-btn--sm" onClick={() => void createTeam()} disabled={busy}>
          <LineIcon name="plus" size={14} /> 新建团队草稿
        </button>
        <span className="cv-spacer" />
        {teams.length > 0 && (
          <div className="tabs-container cv-team-tabs" role="group" aria-label="团队切换">
            {teams.map((t) => {
              const tm = statusMeta(t.state);
              return (
                <button
                  key={t.id}
                  type="button"
                  aria-pressed={snapshot?.team.id === t.id}
                  aria-label={`${t.name}，状态${tm.text}`}
                  className={`cv-team-tab ${snapshot?.team.id === t.id ? 'is-active' : ''}`}
                  onClick={() => void openTeam(t.id)}
                >
                  <span className={`ui-dot ${t.state === 'running' ? 'ui-dot--running' : ''}`} />
                  <LineIcon name={tm.icon} size={16} />
                  {t.name}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {error && <div className="cv-notice cv-notice--error" role="alert">{error}</div>}

      {/* 画布区 */}
      <div className="cv-canvas-wrap team-map">
        {snapshot ? (
          <>
            <TeamCanvasGraph
              snapshot={snapshot}
              memberViews={memberViews}
              selectedRole={selectedRole}
              onSelectRole={setSelectedRole}
              hoveredRole={hoveredRole}
              onHoverRole={setHoveredRole}
              onRenameMember={renameMember}
            />
            {/* 宽屏：检查器作为右侧浮层抽屉，仅在选中节点时出现 */}
            {!stackedInspector && selected && inspector}
            {showLegend && (
              <div className="cv-legend-pop">
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-complete)' }} />已完成</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-running)' }} />执行中</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-waiting)' }} />等待审批</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-verifying)' }} />验证中</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-rework)' }} />返工</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-failed)' }} />失败</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-blocked)' }} />阻塞</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-paused)' }} />暂停/取消</span>
                <span className="cv-legend-item"><span className="cv-legend-swatch" style={{ background: 'var(--ui-st-external)' }} />外部未知</span>
              </div>
            )}
            {/* 右下角图例开关 */}
            <div style={{ position: 'absolute', right: 12, bottom: 12, zIndex: 4 }}>
              <button
                type="button"
                className="ui-btn ui-btn--sm"
                onClick={() => setShowLegend((s) => !s)}
                aria-pressed={showLegend}
              >
                <LineIcon name="layers" size={14} /> 图例
              </button>
            </div>
          </>
        ) : (
          <div className="ui-empty" style={{ height: '100%', justifyContent: 'center' }}>
            <LineIcon name="canvas" size={40} className="ui-hint" />
            <div className="ui-empty-title">还没有团队</div>
            <p className="ui-empty-hint">在上方选择模板与默认模型，点「新建草稿」开始。</p>
          </div>
        )}
      </div>

      {/* 窄屏：检查器常驻并堆叠在画布下方，未选中时显示占位态。
          放在 .cv-canvas-wrap 之外，才能真正「stack below the map」而不是覆盖画布。 */}
      {snapshot && stackedInspector && (
        <div className="cv-inspector-stack">{inspector}</div>
      )}

      {/* 底部启动门禁 */}
      {snapshot && (
        <div className="ui-panel ui-panel--flat" style={{ padding: '12px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <strong>
                {snapshot.team.state === 'running' ? '观察执行与独立验证' : '核验模型与授权后启动'}
              </strong>
              <p className="ui-hint" style={{ margin: '2px 0 0' }}>
                {notice || `草稿 v${snapshot.team.version} · 未产生真实费用`}
              </p>
            </div>
            <button
              type="button"
              className="ui-btn ui-btn--primary"
              disabled={busy || !v?.can_start || snapshot.team.state === 'running'}
              onClick={() => void startTeam()}
            >
              <LineIcon name="dispatch" size={16} /> 检查并启动团队
            </button>
          </div>
          {v && v.blockers.length > 0 && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 20, fontSize: 12, color: 'var(--ui-st-failed)' }}>
              {v.blockers.map((b, i) => (
                <li key={i}>{b.role ? `[${b.role}] ` : ''}{b.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* 真实模型披露：不挂在 snapshot 门控里 —— 这是全局事实，
          首屏就该说清楚，不该等用户建了团队才看见（e2e ui-team.spec.ts:206 冻结契约）。
          配色走九档 waiting 琥珀 + 文字说明，颜色不是唯一信息通道。 */}
      {!catalog.real_model_configured && (
        <div className="cv-notice cv-notice--warn" data-testid="cv-model-blocked">
          真实模型凭据未配置（FY_MODEL_API_KEY / FY_MODEL_BASE_URL）。当前只列出本地确定性
          合成模型；真实模型往返为 BLOCKED_EXTERNAL，本页不冒充真实模型成功。
        </div>
      )}
    </div>
  );
}
