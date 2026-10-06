import type { ReactNode, SVGProps } from 'react';

/**
 * 包 A 局部图标集（24×24 网格 · stroke-width 1.5 · round cap/join · fill:none）。
 *
 * 为什么不改 components/ui/LineIcon.tsx：那是冻结的单一写入者文件（总纲 §8.1）。
 * 本文件只补录 LineIcon 目前缺失、而包 A 视觉层必须用的图标；除此之外一律
 * 继续 import LineIcon，避免图标源分裂。
 *
 * 需补录进 LineIcon.tsx（交给收口包统一收口，本包不越界）：
 *   clock · send · shield · hash · chevronUp · undo · gitCommit
 *   （本文件额外自给：plus / x / chevronDown / rotateLeft / history / command）
 */

export type WbIconName =
  | 'clock'
  | 'send'
  | 'shield'
  | 'hash'
  | 'chevronUp'
  | 'undo'
  | 'gitCommit'
  | 'plus'
  | 'x'
  | 'chevronDown'
  | 'history'
  | 'command';

const PATHS: Record<WbIconName, ReactNode> = {
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3.2 2" />
    </>
  ),
  send: (
    <>
      <path d="M4.5 12 20 4.5l-5 15.5-3.6-6.4z" />
      <path d="M20 4.5 11.4 13.6" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.2 19 6v5.2c0 4.2-2.8 7.3-7 8.6-4.2-1.3-7-4.4-7-8.6V6z" />
      <path d="M9 12.2l2.2 2.2 4-4.2" />
    </>
  ),
  hash: (
    <>
      <path d="M9.5 4.5 7.8 19.5M16.2 4.5 14.5 19.5" />
      <path d="M4.8 9.5h14.4M4.2 14.5h14.4" />
    </>
  ),
  chevronUp: <path d="M6.5 14.5 12 9l5.5 5.5" />,
  undo: (
    <>
      <path d="M4.5 9.5h9.8a5.2 5.2 0 0 1 0 10.4H8.6" />
      <path d="M8.2 5.2 4.4 9.5l3.8 3.6" />
    </>
  ),
  gitCommit: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M3.5 12h5.3M15.2 12h5.3" />
    </>
  ),
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  x: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  chevronDown: <path d="M6.5 9.5 12 15l5.5-5.5" />,
  history: (
    <>
      <path d="M4.6 12a7.4 7.4 0 1 0 2.5-5.6" />
      <path d="M4.4 4.6v4.2h4.2" />
      <path d="M12 8.4V12l2.8 1.8" />
    </>
  ),
  command: (
    <path d="M9 6.5a2.5 2.5 0 1 0-2.5 2.5H17.5A2.5 2.5 0 1 0 15 6.5v11a2.5 2.5 0 1 0 2.5-2.5H6.5A2.5 2.5 0 1 0 9 17.5z" />
  ),
};

interface Props extends Omit<SVGProps<SVGSVGElement>, 'name' | 'children'> {
  name: WbIconName;
  size?: number;
  /** 语义性图标必须给 title；纯装饰传 aria-hidden（默认）。 */
  title?: string;
}

export function WbIcon({ name, size = 18, title, ...rest }: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      focusable="false"
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      {PATHS[name]}
    </svg>
  );
}
