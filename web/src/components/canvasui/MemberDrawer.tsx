/**
 * 包 B · 成员抽屉（浮层，不推挤画布）。
 * 点节点即开；Esc / 点遮罩 / 点关闭按钮即关，关闭后焦点还给触发节点。
 */
import { useEffect, useRef } from 'react';
import type {
  BindingView,
  HostCapability,
  ModelOption,
  TeamEventItem,
  TeamMemberView,
} from '../../api/teams';
import { LineIcon } from '../ui/LineIcon';
import { SCOPE_LABEL } from '../agent-teams/teamVisual';
import { statusMeta } from './statusMap';

interface Props {
  member: TeamMemberView;
  binding: BindingView | null;
  host: HostCapability | undefined;
  models: ModelOption[];
  events: TeamEventItem[];
  busy: boolean;
  onClose: () => void;
  onApplyBinding: (modelId: string) => void;
  onSwitchModel: (modelId: string) => void;
  onControl: (operation: string, note?: string) => void;
}

function fmtUsd(v: number): string { return `$${Number(v || 0).toFixed(4)}`; }

export function MemberDrawer({
  member,
  binding,
  host,
  models,
  events,
  busy,
  onClose,
  onApplyBinding,
  onSwitchModel,
  onControl,
}: Props) {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const meta = statusMeta(member.state);
  const perMemberDisabled = !member.control.supports_per_member_model;

  return (
    <aside
      className="cv-drawer"
      role="complementary"
      aria-label="节点设置"
    >
      <div className="cv-drawer__hd">
        <span className={`cv-status cv-status--${meta.tier}`}>
          <span className="cv-status__dot" />
          <LineIcon name={meta.icon} size={16} />
          {meta.text}
        </span>
        <h3>{member.title || member.role}</h3>
        <span style={{ marginLeft: 'auto' }} />
        <button
          ref={closeRef}
          type="button"
          className="ui-btn ui-btn--icon ui-btn--sm"
          onClick={onClose}
          aria-label="关闭抽屉"
        >
          <LineIcon name="close" size={16} />
        </button>
      </div>

      <div className="cv-drawer__bd">
        <div className="cv-kv"><span>角色</span><span>{member.role}</span></div>
        <div className="cv-kv"><span>宿主</span><span>{member.agent_host}</span></div>
        <div className="cv-kv"><span>供应商</span><span>{member.provider_id || '未设置'}</span></div>
        <div className="cv-kv"><span>独立会话</span><span style={{ fontFamily: 'ui-monospace,monospace', fontSize: 11 }}>
          {member.session_id.slice(0, 18)}…
        </span></div>
        <div className="cv-kv"><span>依赖</span><span>{member.depends_on.join('、') || '无'}</span></div>
        <div className="cv-kv"><span>运行批次</span><span>#{member.run_batch}</span></div>
        <div className="cv-kv"><span>计划版本</span><span>v{member.plan_version}</span></div>
        <div className="cv-kv">
          <span>凭据引用</span>
          <span>
            {member.credential_ref || '未设置'} ·{' '}
            {member.credential_configured ? '已配置' : '未配置'}
          </span>
        </div>
        <div className="cv-kv"><span>预留</span><span>{fmtUsd(member.budget_reserved_usd)}</span></div>
        <div className="cv-kv"><span>已用</span><span>{fmtUsd(member.budget_spent_usd)}</span></div>

        {member.current_goal && (
          <div>
            <span className="ui-label">当前任务</span>
            <p className="ui-hint" style={{ margin: 0, lineHeight: 1.5 }}>{member.current_goal}</p>
          </div>
        )}

        <label className="ui-label" style={{ marginTop: 8 }} htmlFor="cv-node-model">请求模型（节点覆盖）</label>
        <select
          id="cv-node-model"
          className="ui-select"
          value={member.requested_model}
          disabled={busy || perMemberDisabled}
          onChange={(e) => onApplyBinding(e.target.value)}
        >
          <option value="">（继承，不覆盖）</option>
          {models.map((m) => (
            <option key={`${m.provider_id}:${m.model_id}`} value={m.model_id}>
              {m.model_id}{m.synthetic ? '（合成）' : ''}
            </option>
          ))}
        </select>
        <p className="ui-hint">
          继承来源：{SCOPE_LABEL[binding?.inherited_from ?? member.inherited_from] ?? '未解析'}
          {binding && binding.chain.length > 0 && (
            <> · {binding.chain.map((c) => SCOPE_LABEL[c.scope] ?? c.scope).join(' → ')}</>
          )}
        </p>
        {perMemberDisabled && (
          <p className="ui-error-text">
            逐成员模型覆盖：宿主 {member.agent_host} 不支持
            {host ? `（${host.reason}）` : ''}。控件已禁用，未伪装设置成功。
          </p>
        )}

        <details>
          <summary>运行中切换模型</summary>
          <select
            className="ui-select"
            /* 可访问名必须落在 select 上：<summary> 只是折叠标题，不是控件的标签。
               缺了它，这个 select 唯一的名字就是「选择目标模型…」这个占位 option，
               读屏用户听不出它是干什么的；e2e/ui-team.spec.ts:331 的
               getByLabel(/运行中切换模型/) 也就永远匹配不到（它认 label，不认 summary）。 */
            aria-label="运行中切换模型"
            value=""
            disabled={busy || member.control.disabled_operations.includes('switch_model')}
            onChange={(e) => { if (e.target.value) onSwitchModel(e.target.value); }}
          >
            <option value="">选择目标模型…</option>
            {models.filter((m) => m.model_id !== member.requested_model).map((m) => (
              <option key={m.model_id} value={m.model_id}>{m.model_id}</option>
            ))}
          </select>
          <p className="ui-hint">切换只在安全边界后的新批次生效；旧批次保留。</p>
        </details>

        <details>
          <summary>权限、预算与高级参数</summary>
          <div className="cv-kv"><span>能力来源</span><span>{host?.probe_source ?? '—'}</span></div>
          {member.blocked_reason && (
            <div className="cv-notice cv-notice--error" style={{ marginTop: 8 }}>
              {member.blocked_reason}
            </div>
          )}
        </details>

        <details open>
          <summary>最近事件</summary>
          <ul className="cv-event-log">
            {events.slice(-30).map((e) => (
              <li key={e.seq}>
                <span className="cv-seq">#{e.seq}</span>
                <span className="cv-et">{e.event_type}</span>
                <span className="ui-hint">
                  {e.run_batch != null ? `批次${e.run_batch} ` : ''}
                  {String(e.details.role ?? '')}
                </span>
              </li>
            ))}
          </ul>
        </details>
      </div>

      <div className="cv-drawer__ft">
        <button
          type="button"
          className="ui-btn ui-btn--sm"
          disabled={busy}
          onClick={() => onControl(member.state === 'paused' ? 'resume' : 'pause')}
        >
          <LineIcon name={member.state === 'paused' ? 'play' : 'pause'} size={16} />
          {member.state === 'paused' ? '恢复' : '暂停'}
        </button>
        <button
          type="button"
          className="ui-btn ui-btn--sm"
          disabled={busy}
          onClick={() => onControl('rework', '要求返工')}
        >
          <LineIcon name="refresh" size={16} />
          要求返工
        </button>
        <button
          type="button"
          className="ui-btn ui-btn--sm ui-btn--danger"
          disabled={busy}
          onClick={() => onControl('cancel', '用户取消')}
        >
          <LineIcon name="xCircle" size={16} />
          取消
        </button>
      </div>
    </aside>
  );
}
