// 健康状态徽标。三态而非两态：从未探活 = 灰（不是「通过」也不是「失败」）。
// 颜色不是唯一信息：图标 + 文案 + 详情同时给出（UI_BASELINE §状态色）。
import type { HubHealth } from '../../api/hub';

export type HealthTone = 'ok' | 'fail' | 'unknown' | 'pending';

export function healthTone(health: HubHealth | null | undefined): HealthTone {
  if (!health) return 'unknown';
  if (health.ok === true) return 'ok';
  if (health.ok === false) return 'fail';
  return 'unknown';
}

const TONE_TEXT: Record<HealthTone, string> = {
  ok: '可用',
  fail: '不可用',
  unknown: '未探活',
  pending: '探活中',
};

const TONE_ICON: Record<HealthTone, string> = {
  ok: '✓',
  fail: '✕',
  unknown: '—',
  pending: '…',
};

export function HubHealthBadge({
  health,
  pending = false,
  latencyMs,
}: {
  health: HubHealth | null | undefined;
  pending?: boolean;
  latencyMs?: number | null;
}) {
  const tone: HealthTone = pending ? 'pending' : healthTone(health);
  const latency = latencyMs ?? health?.latency_ms ?? null;
  return (
    <span className={`hub-health hub-health-${tone}`} data-testid="hub-health-badge" data-tone={tone}>
      <span aria-hidden="true">{TONE_ICON[tone]}</span>
      <span>{TONE_TEXT[tone]}</span>
      {tone === 'ok' && latency !== null && <span className="muted">{latency}ms</span>}
    </span>
  );
}

export function HealthDetail({ health }: { health: HubHealth | null | undefined }) {
  if (!health) return null;
  if (health.ok !== false) return null;
  if (!health.detail) return null;
  return (
    <p className="hub-health-detail" data-testid="hub-health-detail">
      失败原因：{health.detail}
    </p>
  );
}
