/**
 * 包 C · 状态三件套（chatui 私有）
 * ---------------------------------------------------------------------------
 * 铁律：状态一律「状态色 + LineIcon + 中文文字」三件套，
 * 颜色永远不是唯一信息通道（读屏与色弱用户必须拿到文字与图标）。
 * 九档语义来自 tokens.css 锁定值，不自创。
 */
import type { ReactNode } from 'react';
import { LineIcon, type LineIconName } from '../ui/LineIcon';
import { ChatIcon } from './ChatIcons';

export type StatusKind =
  | 'complete'
  | 'running'
  | 'waiting'
  | 'verifying'
  | 'rework'
  | 'failed'
  | 'blocked'
  | 'paused'
  | 'external';

const ICONS: Record<StatusKind, LineIconName | 'clock'> = {
  complete: 'check',
  running: 'play',
  waiting: 'clock',
  verifying: 'target',
  rework: 'alert',
  failed: 'xCircle',
  blocked: 'lock',
  paused: 'pause',
  external: 'external',
};

export interface StatusTagProps {
  kind: StatusKind;
  /** 中文状态文字（必填 —— 颜色不是唯一通道） */
  text: string;
  size?: number;
  /** 补充说明（如失败原因原文） */
  detail?: string;
}

export function StatusTag({ kind, text, size = 14, detail }: StatusTagProps) {
  return (
    <span className={`ui-badge ui-badge--${kind} chatui-status`}>
      {ICONS[kind] === 'clock' ? (
        <ChatIcon name="clock" size={size} />
      ) : (
        <LineIcon name={ICONS[kind] as LineIconName} size={size} />
      )}
      <span className="chatui-status-text">{text}</span>
      {detail ? <span className="chatui-status-detail">{detail}</span> : null}
    </span>
  );
}

/** 仅用于需要「图标 + 文字」但不套徽标底色的行内场合（如提示行）。 */
export function StatusLine({ kind, icon, children }: { kind: StatusKind; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={`chatui-statusline chatui-statusline--${kind}`}>
      {icon ?? (ICONS[kind] === 'clock' ? <ChatIcon name="clock" size={14} /> : <LineIcon name={ICONS[kind] as LineIconName} size={14} />)}
      <span>{children}</span>
    </span>
  );
}
