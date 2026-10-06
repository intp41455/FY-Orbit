/**
 * 包 C · 骨架屏（chatui 私有）
 * ---------------------------------------------------------------------------
 * 铁律：铺满视口的 Spinner 一律换成骨架屏；Spinner 只许留在按钮内嵌「发送中…」。
 * 无障碍：容器 aria-busy="true" role="status"，内部图形 aria-hidden，配 sr-only 说明。
 */
export interface SkeletonProps {
  /** 骨架行数 */
  rows?: number;
  /** 读屏说明 */
  label?: string;
  /** 单行高度（px），默认 16 */
  height?: number;
  /** 变体：会话列表行 / 消息行 / 卡片 */
  variant?: 'line' | 'conv' | 'msg' | 'card';
  className?: string;
}

export function Skeleton({ rows = 3, label = '正在加载', height = 16, variant = 'line', className }: SkeletonProps) {
  return (
    <div className={`chatui-skel${className ? ` ${className}` : ''}`} role="status" aria-busy="true">
      <span className="chatui-sr-only">{label}</span>
      <div className="chatui-skel-body" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
          <div
            key={i}
            className={`ui-skel chatui-skel-row chatui-skel-row--${variant}`}
            style={variant === 'line' ? { height } : undefined}
          />
        ))}
      </div>
    </div>
  );
}
