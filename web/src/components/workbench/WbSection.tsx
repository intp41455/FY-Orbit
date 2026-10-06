import { useId, type ReactNode } from 'react';
import { LineIcon } from '../ui/LineIcon';
import { WbIcon } from './WbIcon';

/**
 * 包 A「区段 + 聚焦」基元。
 *
 * 关键约定：**折叠不是卸载**。无论是否折叠，children 始终挂载在 DOM 里
 * （塌陷段只是 body 的 max-height:0 + overflow:hidden），因此：
 *   - commit-graph-ws-input / backup-btn / diff-view 这类被冻结测试直接定位的
 *     节点不会因为用户折叠而消失；
 *   - 终端 PTY、预览节流、SSE 订阅等副作用也不会因为切换视图被重启。
 * 「互斥卸载」的标签页写法会同时打穿功能保持与 4 条冻结 e2e，这里明确不用。
 */
interface Props {
  /** 1 起的序号，用于 kbd 提示与「第 k/n 段」播报。 */
  index: number;
  title: string;
  icon: Parameters<typeof LineIcon>[0]['name'];
  /** 快捷键文案，同时给 sighted + sr（aria-keyshortcuts）。 */
  hotkey?: string;
  collapsed: boolean;
  onToggle: () => void;
  /** 标题条右侧的状态位（状态 = 颜色 + 图标 + 中文文字，三通道）。 */
  statusSlot?: ReactNode;
  children: ReactNode;
}

export function WbSection({
  index,
  title,
  icon,
  hotkey,
  collapsed,
  onToggle,
  statusSlot,
  children,
}: Props) {
  const bodyId = useId();
  return (
    <section className={`wb-sec${collapsed ? ' is-collapsed' : ''}`} data-index={index}>
      <h3 className="wb-sec-hd">
        <button
          type="button"
          className="wb-sec-bar"
          aria-expanded={!collapsed}
          aria-controls={bodyId}
          aria-keyshortcuts={hotkey}
          onClick={onToggle}
        >
          <span className="wb-sec-no">{index}</span>
          <span className="wb-sec-ico" aria-hidden="true">
            <LineIcon name={icon} size={18} />
          </span>
          <span className="wb-sec-title">{title}</span>
          {statusSlot}
          {hotkey && <span className="ui-kbd wb-sec-kbd">{hotkey}</span>}
          <span className="wb-sec-caret" aria-hidden="true">
            <WbIcon name={collapsed ? 'chevronDown' : 'chevronUp'} size={16} />
          </span>
        </button>
      </h3>
      <div className="wb-sec-bd" id={bodyId}>
        {children}
      </div>
    </section>
  );
}
