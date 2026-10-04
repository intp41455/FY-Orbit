/**
 * 团队画布共享视觉层（21 号状态色规范）。
 *
 * 颜色唯一信息源之外的冗余（状态文字/图标）由节点自身渲染；
 * 动画仅限执行/验证/传输状态，遵循 prefers-reduced-motion（styles.css）。
 * 全套状态禁止使用绿色。
 */

export const SCOPE_LABEL: Record<string, string> = {
  global: '继承全局',
  team: '继承团队默认',
  role: '继承角色默认',
  node: '节点覆盖',
  unresolved: '未解析',
};

export function stateBadge(state: string): { cls: string; text: string } {
  switch (state) {
    case 'running':
    case 'starting':
    case 'completed':
      return { cls: 'badge ok', text: '执行中 / 已完成' };
    case 'blocked':
    case 'failed':
      return { cls: 'badge danger', text: '阻塞 / 失败' };
    case 'paused':
    case 'waiting_rework':
      return { cls: 'badge warn', text: '暂停 / 待返工' };
    case 'cancelled':
      return { cls: 'badge', text: '已取消' };
    case 'unknown_needs_reconciliation':
      return { cls: 'badge warn', text: '待核对' };
    default:
      return { cls: 'badge', text: state || '草稿' };
  }
}

export interface Pt {
  x: number;
  y: number;
}

export interface EdgeGeom {
  key: string;
  d: string;
  mid: Pt;
  cls: string;
  label: string;
}

/** 成员状态 → 连线样式类。 */
export function edgeClassFor(state: string): string {
  switch (state) {
    case 'running':
    case 'starting':
      return 'edge-running';
    case 'completed':
    case 'succeeded':
      return 'edge-complete';
    case 'waiting_approval':
    case 'waiting_input':
      return 'edge-waiting';
    case 'waiting_rework':
      return 'edge-rework';
    case 'failed':
      return 'edge-failed';
    case 'blocked':
      return 'edge-blocked';
    case 'paused':
    case 'cancelled':
      return 'edge-paused';
    case 'unknown_needs_reconciliation':
      return 'edge-unknown';
    default:
      return 'edge-idle';
  }
}

/** 状态色 → 箭头 marker id。 */
export const EDGE_MARKERS: Record<string, string> = {
  'edge-idle': '#94a3b8',
  'edge-running': '#22b8e6',
  'edge-complete': '#2563eb',
  'edge-waiting': '#fbbf24',
  'edge-rework': '#fb7185',
  'edge-failed': '#ef4444',
  'edge-blocked': '#d97706',
  'edge-paused': '#94a3b8',
  'edge-unknown': '#8b5cf6',
};

/** 有向 S 曲线：控制点放在主轴中点。 */
export function bezier(from: Pt, to: Pt, key: string, cls: string, label: string): EdgeGeom {
  const dx = Math.abs(to.x - from.x);
  const dy = Math.abs(to.y - from.y);
  if (dy >= dx) {
    const my = (from.y + to.y) / 2;
    return {
      key, cls, label,
      mid: { x: (from.x + to.x) / 2, y: my },
      d: `M ${from.x} ${from.y} C ${from.x} ${my}, ${to.x} ${my}, ${to.x} ${to.y}`,
    };
  }
  const mx = (from.x + to.x) / 2;
  return {
    key, cls, label,
    mid: { x: mx, y: (from.y + to.y) / 2 },
    d: `M ${from.x} ${from.y} C ${mx} ${from.y}, ${mx} ${to.y}, ${to.x} ${to.y}`,
  };
}
