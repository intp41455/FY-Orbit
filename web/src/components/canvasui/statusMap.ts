/**
 * 包 B · 状态映射（四通道编码：点 + 图标 + 文字 + 当前动作）
 * 颜色不得是唯一信息通道 —— 每个状态必须同时带图标与文字。
 */
import type { LineIconName } from '../ui/LineIcon';

export type StatusTier =
  | 'complete'
  | 'running'
  | 'waiting'
  | 'verifying'
  | 'rework'
  | 'failed'
  | 'blocked'
  | 'paused'
  | 'external';

export interface StatusMeta {
  tier: StatusTier;
  /** 中文状态文字（人读） */
  text: string;
  /** LineIcon 图标名 */
  icon: LineIconName;
}

/** 后端 MemberState → 九档状态。 */
export function statusMeta(state: string): StatusMeta {
  switch (state) {
    case 'running':
      return { tier: 'running', text: '执行中', icon: 'refresh' };
    case 'starting':
      return { tier: 'running', text: '启动中', icon: 'refresh' };
    case 'completed':
    case 'succeeded':
      return { tier: 'complete', text: '已完成', icon: 'check' };
    case 'waiting_approval':
    case 'waiting_input':
      return { tier: 'waiting', text: '等待审批', icon: 'clock' };
    case 'waiting_rework':
      return { tier: 'rework', text: '待返工', icon: 'refresh' };
    case 'failed':
      return { tier: 'failed', text: '失败', icon: 'xCircle' };
    case 'blocked':
      return { tier: 'blocked', text: '阻塞', icon: 'alert' };
    case 'paused':
      return { tier: 'paused', text: '已暂停', icon: 'pause' };
    case 'cancelled':
      return { tier: 'paused', text: '已取消', icon: 'pause' };
    case 'unknown_needs_reconciliation':
      return { tier: 'external', text: '待核对', icon: 'info' };
    case 'draft':
    default:
      return { tier: 'paused', text: '草稿', icon: 'sliders' };
  }
}

/** 状态 → 连线 class（与 canvas.css 中 .cv-edge--* 对应） */
export function edgeClassForState(state: string): string {
  switch (state) {
    case 'running':
    case 'starting':
      return 'cv-edge--running';
    case 'completed':
    case 'succeeded':
      return 'cv-edge--complete';
    case 'waiting_approval':
    case 'waiting_input':
      return 'cv-edge--waiting';
    case 'waiting_rework':
      return 'cv-edge--rework';
    case 'failed':
      return 'cv-edge--failed';
    case 'blocked':
      return 'cv-edge--blocked';
    case 'paused':
    case 'cancelled':
      return 'cv-edge--paused';
    case 'unknown_needs_reconciliation':
      return 'cv-edge--external';
    default:
      return 'cv-edge--idle';
  }
}

/** 该状态是否允许连线光点流动（仅 执行/验证/传输 三类） */
export function edgeFlowActive(state: string): boolean {
  return state === 'running' || state === 'starting' || state === 'verifying';
}

/** 已持续时长格式化。 */
export function fmtDuration(updatedAt: string | null): string {
  if (!updatedAt) return '尚未执行';
  const t = Date.parse(updatedAt);
  if (Number.isNaN(t)) return '—';
  const diff = Math.max(0, Date.now() - t);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
