/**
 * 包 C · 对话与记录域 · 局部线条图标（chatui 私有）
 * ---------------------------------------------------------------------------
 * LineIcon.tsx 是冻结的全局单一写入者产物，本包不得修改。
 * 本包需要的 4 枚图标在 LineIcon 里不存在：clock / send / archive / quote。
 * 因此在自己的域内建局部图标组件，规范与 LineIcon 完全一致：
 *   - 24×24 画布、stroke-width 1.5、round linecap + round linejoin
 *   - fill:none、颜色继承 currentColor、无绿色、无图标库依赖
 * ⚠ 收口时需向总纲补录这 4 枚：clock / send / archive / quote。
 */
import type { ReactNode } from 'react';

const S = { fill: 'none', stroke: 'currentColor' } as const;

const LOCAL_ICONS = {
  clock: (
    <>
      <circle cx="12" cy="12" r="8.6" {...S} />
      <path d="M12 7.2V12l3.4 2.2" {...S} />
    </>
  ),
  send: (
    <>
      <path d="M20.4 3.6 3.9 10.7l6.2 2.6 2.6 6.2Z" {...S} />
      <path d="m20.4 3.6-7.7 9.7" {...S} />
    </>
  ),
  archive: (
    <>
      <rect x="3.2" y="4.4" width="17.6" height="4.4" rx="1.4" {...S} />
      <path d="M4.8 8.8v9.2a1.6 1.6 0 0 0 1.6 1.6h11.2a1.6 1.6 0 0 0 1.6-1.6V8.8" {...S} />
      <path d="M9.6 12.6h4.8" {...S} />
    </>
  ),
  quote: (
    <>
      <path d="M9.4 6.6C6.6 7.9 5 10.1 5 12.9c0 2.3 1.3 3.8 3.1 3.8 1.7 0 2.9-1.2 2.9-2.9 0-1.6-1.1-2.7-2.7-2.7-.5 0-1 .1-1.3.2" {...S} />
      <path d="M18.6 6.6c-2.8 1.3-4.4 3.5-4.4 6.3 0 2.3 1.3 3.8 3.1 3.8 1.7 0 2.9-1.2 2.9-2.9 0-1.6-1.1-2.7-2.7-2.7-.5 0-1 .1-1.3.2" {...S} />
    </>
  ),
} as const;

export type ChatIconName = keyof typeof LOCAL_ICONS;

/** 需向总纲补录的图标名（供收口审计）。 */
export const CHAT_ICONS_TO_BACKFILL: readonly ChatIconName[] = ['clock', 'send', 'archive', 'quote'];

export interface ChatIconProps {
  name: ChatIconName;
  /** 默认 20；密集区 16 */
  size?: number;
  strokeWidth?: number;
  className?: string;
  /** 传了即有语义（role=img + title），不传一律 aria-hidden（纯装饰） */
  title?: string;
}

export function ChatIcon({ name, size = 20, strokeWidth = 1.5, className, title }: ChatIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      style={{ flex: '0 0 auto', display: 'block' }}
    >
      {title ? <title>{title}</title> : null}
      {LOCAL_ICONS[name] as ReactNode}
    </svg>
  );
}
