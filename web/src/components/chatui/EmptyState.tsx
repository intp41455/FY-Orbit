/**
 * 包 C · 空态（chatui 私有）
 * 铁律：禁用「暂无数据」这类无信息文案。空态 = 图标 + 标题 + hint + 行动按钮。
 */
import type { ReactNode } from 'react';
import { LineIcon, type LineIconName } from '../ui/LineIcon';
import { ChatIcon, type ChatIconName } from './ChatIcons';

export interface EmptyStateProps {
  /** LineIcon 名或本域局部图标名 */
  icon?: LineIconName | ChatIconName;
  iconLocal?: boolean;
  title: string;
  hint?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, iconLocal, title, hint, action }: EmptyStateProps) {
  return (
    <div className="ui-empty chatui-empty">
      {icon ? (
        <span className="chatui-empty-icon" aria-hidden="true">
          {iconLocal ? <ChatIcon name={icon as ChatIconName} size={30} /> : <LineIcon name={icon as LineIconName} size={30} />}
        </span>
      ) : null}
      <div className="ui-empty-title">{title}</div>
      {hint ? <p className="ui-empty-hint">{hint}</p> : null}
      {action ? <div className="chatui-empty-action">{action}</div> : null}
    </div>
  );
}
