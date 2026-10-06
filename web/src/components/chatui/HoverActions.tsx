/**
 * 包 C · hover 浮现操作组（chatui 私有）
 * ---------------------------------------------------------------------------
 * 最少点击守则：容器常驻 padding-right 预留 88px，避免浮现时布局跳动；
 * 视觉 28×28，用 ::after inset:-8px 把命中区扩到 44×44；
 * 触发用 :hover 加 :focus-within（键盘可达）；@media (hover:none) 时常驻可见。
 */
import type { ReactNode } from 'react';

export interface HoverAction {
  key: string;
  label: string;
  icon: ReactNode;
  onClick: (e?: React.MouseEvent) => void;
}

export function HoverActions({ actions, className }: { actions: HoverAction[]; className?: string }) {
  return (
    <span className={`chatui-hover-actions${className ? ` ${className}` : ''}`}>
      {actions.map((a) => (
        <button
          key={a.key}
          type="button"
          className="chatui-icon-btn"
          title={a.label}
          aria-label={a.label}
          onClick={(e) => {
            e.stopPropagation();
            a.onClick(e);
          }}
        >
          <span aria-hidden="true">{a.icon}</span>
        </button>
      ))}
    </span>
  );
}
