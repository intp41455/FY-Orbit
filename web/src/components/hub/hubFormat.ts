// 连接状态文案：把后端 state 映射成中文，并区分「配置就绪」与「真的能用」。
// 诚实原则：needs_credentials / disabled 一律不算可用，绝不合并成绿色。
import type { HubConnection, HubKind } from '../../api/hub';
import { HUB_KIND_LABEL } from '../../api/hub';

export const STATE_TEXT: Record<string, string> = {
  active: '已启用',
  error: '异常',
  needs_credentials: '缺凭证',
  disabled: '已停用',
};

export function stateText(state: string): string {
  return STATE_TEXT[state] ?? state;
}

/** 卡片右上角的状态胶囊 class 后缀。 */
export function stateTone(state: string): string {
  switch (state) {
    case 'active':
      return 'ok';
    case 'error':
      return 'fail';
    case 'needs_credentials':
      return 'warn';
    default:
      return 'idle';
  }
}

export function kindLabel(kind: string): string {
  return HUB_KIND_LABEL[kind as HubKind] ?? kind;
}

export function isInvocable(conn: HubConnection): boolean {
  return conn.state !== 'disabled' && conn.state !== 'needs_credentials';
}

/**
 * 掩码值识别。后端 `mask_secret` 的两种产出（crypto.py:107-114）：
 *   - `len<=8` → 全星号 `****`
 *   - `len>8`  → `abc****xy`（前 3 + 后 2）
 * 两者都含 `****`，因此以该子串为准，避免把部分掩码误判成明文。
 */
export function isMasked(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  return value.includes('****') || /^\*+$/.test(value.trim());
}

/** 能力标签：一行展示，超出折叠为 +N。 */
export function capabilityTags(conn: HubConnection, limit = 6): { shown: string[]; extra: number } {
  const all = (conn.capabilities ?? []).map((c) => c.name).filter(Boolean);
  return { shown: all.slice(0, limit), extra: Math.max(0, all.length - limit) };
}
