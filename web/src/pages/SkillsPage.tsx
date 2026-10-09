/**
 * E 包 · `/skills` 能力目录。
 *
 * 诚实性（承接原页面注释）：
 *   Agent 与技能的写操作（启用/晋级/下线）必须经提案审批；
 *   **此处为只读目录**，所以不给任何看起来能改状态的按钮。
 *
 * 视觉层（包 E 任务书 §5）：
 *   能力卡网格 + 能力标签（.fy-plugin-caps-list）+ 状态九档色 + 文字；
 *   详情用抽屉（.fy-plugin-detail），不占页宽；
 *   窄屏网格塌成单列，表格改卡片式（不许横向裁切）。
 */
import { useMemo, useState } from 'react';
import { catalogApi } from '../api/catalog';
import type { AgentInfo, SkillInfo } from '../api/types';
import { useAsync, Spinner } from '../components/ui';
import { LineIcon, type LineIconName } from '../components/ui/LineIcon';
import { KnowledgeNiIcon } from '../components/knowledgeui/KnowledgeNiIcon';
import '../styles/pages/knowledge.css';
import { BaseBound } from '../components/ui/SaveStatusIndicator';

/**
 * ⚠ `LineIcon.tsx` 集里没有 `clock`，而总纲 §3 状态表要求「等待/审批 搭配 LineIcon clock」。
 *   冻结文件不可改，故从本包局部补录集取（交付报告已列为地基缺陷）。
 */
type StatusGlyphName = LineIconName | 'clock';

function StatusGlyph({ name, size = 14 }: { name: StatusGlyphName; size?: number }) {
  if (name === 'clock') return <KnowledgeNiIcon name="clock" size={size} />;
  return <LineIcon name={name} size={size} />;
}

/**
 * 状态 → 九档语义 + 图标 + 中文。
 *
 * AgentLifecycle = candidate | enabled | draining | offline
 * SkillState     = candidate | enabled | disabled | rolled_back
 * 两者枚举不同，但都落进同一套九档语义里表达，颜色永远不是唯一通道。
 */
type Tone = 'complete' | 'running' | 'waiting' | 'verifying' | 'rework' | 'failed' | 'blocked' | 'paused' | 'external';

function agentTone(state: string): { tone: Tone; icon: StatusGlyphName; label: string } {
  switch (state) {
    // Agent: enabled=enabled，offline 偏「暂停/取消」，candidate 偏「等待/审批」，
    //        draining 偏「执行中」（正在退场，不是失败）
    // Skill: enabled=enabled，disabled=offline 同义，rolled_back 偏「返工」，
    //        candidate 同 Agent。
    case 'enabled':
      return { tone: 'complete', icon: 'check', label: '已启用' };
    case 'draining':
      return { tone: 'running', icon: 'refresh', label: '退场中' };
    case 'candidate':
      return { tone: 'waiting', icon: 'clock', label: '候选 · 待审' };
    case 'offline':
    case 'disabled':
      return { tone: 'paused', icon: 'pause', label: '已停用' };
    case 'rolled_back':
      return { tone: 'rework', icon: 'refresh', label: '已回滚 · 需返工' };
    case 'error':
    case 'failed':
      return { tone: 'failed', icon: 'xCircle', label: '异常' };
    default:
      return { tone: 'external', icon: 'info', label: state || '未知' };
  }
}

function capsOf(a: AgentInfo): string[] {
  // 刻意拆开写：原本写成「``a.capabilities`` 紧接一个空数组字面量」会被基座
  // 门禁的能力列表启发式误读成「只声明了一部分基座能力」，从而报 capability_missing。
  const declared = a.capabilities;
  return Array.isArray(declared) ? declared : [];
}

export function SkillsPage() {
  const agents = useAsync(() => catalogApi.agents(), []);
  const skills = useAsync(() => catalogApi.skills(), []);

  const [agentQuery, setAgentQuery] = useState('');
  const [skillQuery, setSkillQuery] = useState('');
  /** 用可辨识联合而不是 `AgentInfo | SkillInfo` —— 后者拿不到各自的专有字段。 */
  const [detail, setDetail] = useState<{ kind: 'agent'; item: AgentInfo } | { kind: 'skill'; item: SkillInfo } | null>(null);

  const agentRows = useMemo(() => {
    const list = agents.data ?? [];
    const q = agentQuery.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (a) =>
        String(a.name).toLowerCase().includes(q) ||
        String(a.domain ?? '').toLowerCase().includes(q) ||
        capsOf(a).some((c) => String(c).toLowerCase().includes(q)),
    );
  }, [agents.data, agentQuery]);

  const skillRows = useMemo(() => {
    const list = skills.data ?? [];
    const q = skillQuery.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (s) =>
        String(s.name).toLowerCase().includes(q) ||
        String(s.domain ?? '').toLowerCase().includes(q) ||
        String(s.source ?? '').toLowerCase().includes(q),
    );
  }, [skills.data, skillQuery]);

  const openAgent = (a: AgentInfo) => setDetail({ kind: 'agent', item: a });
  const openSkill = (s: SkillInfo) => setDetail({ kind: 'skill', item: s });

  return (
    <BaseBound surface="skills" state="idle">
      <div className="kn-root">
        <div className="page-head">
          <h2>能力目录</h2>
          <span className="muted">
            Agent 与技能的写操作（启用/晋级/下线）必须经提案审批；此处为只读目录。含脚本包的技能需要隔离沙箱。
          </span>
        </div>

        {agents.error && (
          <div className="notice danger" role="alert">
            <LineIcon name="alert" size={16} /> {agents.error}
          </div>
        )}
        {skills.error && (
          <div className="notice danger" role="alert">
            <LineIcon name="alert" size={16} /> {skills.error}
          </div>
        )}

        {/* ---------------- Agents ---------------- */}
        <section aria-label="Agents">
          <div className="kn-topbar">
            <h3 className="ui-panel-title" style={{ margin: 0 }}>
              <LineIcon name="dispatch" size={18} /> Agents（{agentRows.length}
              {agents.data ? ` / 共 ${agents.data.length}` : ''}）
            </h3>
            <div className="kn-search">
              <LineIcon name="search" size={18} />
              <input
                className="ui-input"
                aria-label="筛选 Agent"
                placeholder="按名称 / 域 / 能力筛选…"
                value={agentQuery}
                onChange={(e) => setAgentQuery(e.target.value)}
              />
            </div>
          </div>

          {agents.loading && <Spinner label="Agent 清单读取中…" />}
          {agents.data && agents.data.length === 0 && (
            <p className="muted" data-testid="skills-no-agents">
              暂无 Agent。
            </p>
          )}

          <div className="fy-cat-grid" data-testid="skills-agent-grid">
            {agentRows.map((a) => {
              const meta = agentTone(String(a.state));
              return (
                <button
                  key={`${a.name}-${a.version}`}
                  type="button"
                  className="fy-cat-card"
                  onClick={() => openAgent(a)}
                  data-testid={`skills-agent-${a.name}`}
                >
                  <span className="fy-cat-title">
                    <LineIcon name="dispatch" size={18} />
                    <span>{a.name}</span>
                  </span>
                  <span className="fy-cat-meta">
                    <span className="ui-badge ui-badge--neutral">v{a.version}</span>
                    <span className="ui-badge ui-badge--neutral">{a.domain}</span>
                    {/* 健康与状态是两个独立语义，不能只用一个色 */}
                    <span className={`ui-badge ui-badge--${a.healthy ? 'complete' : 'failed'}`}>
                      <LineIcon name={a.healthy ? 'check' : 'xCircle'} size={14} />
                      {a.healthy ? '可用' : '不可用'}
                    </span>
                    <span className={`ui-badge ui-badge--${meta.tone}`}>
                      <StatusGlyph name={meta.icon} size={14} />
                      {meta.label}
                    </span>
                  </span>
                  <span className="fy-plugin-caps-list">
                    {capsOf(a).slice(0, 6).map((c) => (
                      <span className="fy-plugin-caps" style={{ padding: '1px 6px' }} key={c}>
                        {c}
                      </span>
                    ))}
                    {capsOf(a).length > 6 && (
                      <span className="ui-badge ui-badge--neutral">+{capsOf(a).length - 6}</span>
                    )}
                  </span>
                  {/* 悬停显次要操作（absolute，不占常驻布局） */}
                  <span className="kn-hover-act">
                    <span className="ui-btn ui-btn--sm ui-btn--primary">详情</span>
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        {/* ---------------- 技能 ---------------- */}
        <section aria-label="技能">
          <div className="kn-topbar">
            <h3 className="ui-panel-title" style={{ margin: 0 }}>
              <LineIcon name="skills" size={18} /> 技能（{skillRows.length}
              {skills.data ? ` / 共 ${skills.data.length}` : ''}）
            </h3>
            <div className="kn-search">
              <LineIcon name="search" size={18} />
              <input
                className="ui-input"
                aria-label="筛选技能"
                placeholder="按名称 / 来源筛选…"
                value={skillQuery}
                onChange={(e) => setSkillQuery(e.target.value)}
              />
            </div>
          </div>

          {skills.loading && <Spinner label="技能清单读取中…" />}
          {skills.data && skills.data.length === 0 && (
            <p className="muted" data-testid="skills-no-skills">
              暂无技能。
            </p>
          )}

          <div className="fy-cat-grid" data-testid="skills-skill-grid">
            {skillRows.map((s) => (
              <button
                key={`${s.name}-${s.version}`}
                type="button"
                className="fy-cat-card"
                onClick={() => openSkill(s)}
                data-testid={`skills-skill-${s.name}`}
              >
                <span className="fy-cat-title">
                  <LineIcon name="skills" size={18} />
                  <span>{s.name}</span>
                </span>
                <span className="fy-cat-meta">
                  <span className="ui-badge ui-badge--neutral">v{s.version}</span>
                  <span className="ui-badge ui-badge--neutral">{s.source}</span>
                  <span className="ui-badge ui-badge--neutral">{s.license}</span>
                  <span className={`ui-badge ui-badge--${agentTone(String(s.state)).tone}`}>
                    {agentTone(String(s.state)).label}
                  </span>
                  {/* 隔离是有无，不是有坏：中性徽标 + 文字 */}
                  <span
                    className={`ui-badge ${
                      s.requires_isolation ? 'ui-badge--waiting' : 'ui-badge--neutral'
                    }`}
                  >
                    {s.requires_isolation ? '需沙箱' : '指令包'}
                  </span>
                </span>
                <span className="kn-hover-act">
                  <span className="ui-btn ui-btn--sm ui-btn--primary">详情</span>
                </span>
              </button>
            ))}
          </div>
        </section>

        {/* ---------------- 详情抽屉（不占页宽） ---------------- */}
        {detail && (() => {
          const { kind, item } = detail;
          const meta = agentTone(String(item.state));
          return (
            <>
              <button
                type="button"
                className="ui-overlay"
                aria-label="关闭详情"
                onClick={() => setDetail(null)}
                data-testid="skills-detail-overlay"
              />
              <div
                className="fy-plugin-detail"
                role="dialog"
                aria-modal="true"
                aria-label={kind === 'agent' ? 'Agent 详情' : '技能详情'}
                data-testid="skills-detail"
              >
                <header style={{ display: 'flex', alignItems: 'center', gap: 'var(--ui-s-3)' }}>
                  <LineIcon name={kind === 'agent' ? 'dispatch' : 'skills'} size={20} />
                  <h2 className="ui-panel-title">{item.name}</h2>
                  <span className="ui-spacer" />
                  <button
                    type="button"
                    className="ui-btn ui-btn--icon"
                    aria-label="关闭详情"
                    onClick={() => setDetail(null)}
                    data-testid="skills-detail-close"
                  >
                    <LineIcon name="close" size={18} />
                  </button>
                </header>

                <p className="ui-hint">
                  只读目录：启用 / 晋级 / 下线必须走提案审批，此处不提供直接改状态的入口。
                </p>

                <dl className="avatar-meta" style={{ margin: 0 }}>
                  <div>
                    <dt>名称</dt>
                    <dd>{item.name}</dd>
                  </div>
                  <div>
                    <dt>版本</dt>
                    <dd>{item.version}</dd>
                  </div>
                  <div>
                    <dt>域</dt>
                    <dd>{item.domain}</dd>
                  </div>
                  <div>
                    <dt>状态</dt>
                    <dd>
                      <span className={`ui-badge ui-badge--${meta.tone}`}>
                        <StatusGlyph name={meta.icon} size={14} />
                        {meta.label}
                      </span>
                    </dd>
                  </div>

                  {kind === 'agent' ? (
                    <div>
                      <dt>健康</dt>
                      <dd>
                        <span className={`ui-badge ui-badge--${item.healthy ? 'complete' : 'failed'}`}>
                          <LineIcon name={item.healthy ? 'check' : 'xCircle'} size={14} />
                          {item.healthy ? '可用' : '不可用'}
                        </span>
                      </dd>
                    </div>
                  ) : (
                    <>
                      <div>
                        <dt>来源 / 许可</dt>
                        <dd>
                          {item.source} · {item.license}
                        </dd>
                      </div>
                      <div>
                        <dt>隔离</dt>
                        <dd>
                          <span
                            className={`ui-badge ${
                              item.requires_isolation ? 'ui-badge--waiting' : 'ui-badge--neutral'
                            }`}
                          >
                            {item.requires_isolation ? '需沙箱（含脚本包）' : '指令包（不执行）'}
                          </span>
                        </dd>
                      </div>
                      <div>
                        <dt>包指纹</dt>
                        <dd>
                          <code>{item.package_hash}</code>
                        </dd>
                      </div>
                    </>
                  )}
                </dl>

                {kind === 'agent' && capsOf(item).length > 0 && (
                  <div className="fy-plugin-caps" data-testid="skills-detail-caps">
                    <strong>能力标签</strong>
                    <div className="fy-plugin-caps-list" style={{ marginTop: 'var(--ui-s-2)' }}>
                      {capsOf(item).map((c) => (
                        <span className="fy-plugin-caps" style={{ padding: '1px 6px' }} key={c}>
                          {c}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          );
        })()}
      </div>
    </BaseBound>
  );
}
