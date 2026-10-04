// 一张连接卡片：图标 / 名称 / 类型 / 状态 / 健康 / 能力标签 / 探活与调用按钮。
// 明文凭证永远拿不到——卡片上只可能看到掩码，因此这里不做任何「显示密钥」入口。
import type { HubConnection } from '../../api/hub';
import { HubHealthBadge, HealthDetail } from './HubHealthBadge';
import { capabilityTags, isInvocable, kindLabel, stateText, stateTone } from './hubFormat';

export function ConnectionCard({
  conn,
  busy = false,
  onHealth,
  onInvoke,
  onDelete,
  onToggleState,
}: {
  conn: HubConnection;
  busy?: boolean;
  onHealth: (conn: HubConnection) => void;
  onInvoke: (conn: HubConnection) => void;
  onDelete: (conn: HubConnection) => void;
  onToggleState: (conn: HubConnection) => void;
}) {
  const { shown, extra } = capabilityTags(conn);
  return (
    <article className="hub-card" data-testid={`hub-card-${conn.id}`}>
      <header className="hub-card-head">
        <span className="hub-card-icon" aria-hidden="true">
          {conn.icon || '🔌'}
        </span>
        <div className="hub-card-title">
          <h3>{conn.name}</h3>
          <span className="muted">
            {kindLabel(conn.kind)}
            {conn.preset_id ? ` · 预置 ${conn.preset_id}` : ''}
            {conn.has_manifest ? ' · manifest' : ''}
          </span>
        </div>
        <span className={`hub-state hub-state-${stateTone(conn.state)}`}>{stateText(conn.state)}</span>
        <HubHealthBadge health={conn.health} />
      </header>

      {conn.description && <p className="hub-card-desc">{conn.description}</p>}
      <HealthDetail health={conn.health} />

      {shown.length > 0 && (
        <p className="hub-tags" data-testid={`hub-caps-${conn.id}`}>
          {shown.map((t) => (
            <span className="hub-tag" key={t}>
              {t}
            </span>
          ))}
          {extra > 0 && <span className="hub-tag hub-tag-more">+{extra}</span>}
        </p>
      )}

      <div className="hub-card-actions">
        <button type="button" onClick={() => onHealth(conn)} disabled={busy}>
          探活
        </button>
        <button type="button" onClick={() => onInvoke(conn)} disabled={busy || !isInvocable(conn)}>
          调用测试
        </button>
        <button type="button" onClick={() => onToggleState(conn)} disabled={busy}>
          {conn.state === 'disabled' ? '启用' : '停用'}
        </button>
        <button type="button" className="danger" onClick={() => onDelete(conn)} disabled={busy}>
          删除
        </button>
      </div>
    </article>
  );
}
