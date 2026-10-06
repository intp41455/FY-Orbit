/**
 * 包 C · 右键菜单（chatui 私有）
 * 后端事实：无 DELETE 接口，因此菜单**不给删除**，只给
 * 继续对话 / 复制全文 / 导出会话 / 重命名… / 归档(或取消归档) / 复制会话 ID。
 * 归档与重命名是本机降级（localStorage），菜单项文字必须写明。
 */
import { useEffect, useRef, type ReactNode } from 'react';

export interface ContextMenuItem {
  key: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  onSelect: () => void;
}

export interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  label?: string;
  onClose: () => void;
}

export function ContextMenu({ x, y, items, label = '会话操作', onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])');
    el?.focus();
  }, []);

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose]);

  // 视口内收敛，避免菜单被裁掉
  const left = Math.max(8, Math.min(x, (typeof window === 'undefined' ? 1280 : window.innerWidth) - 232));
  const top = Math.max(8, Math.min(y, (typeof window === 'undefined' ? 800 : window.innerHeight) - 8 - items.length * 44));

  return (
    <div
      ref={ref}
      className="chatui-menu"
      role="menu"
      aria-label={label}
      style={{ left, top }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          role="menuitem"
          className="chatui-menu-item"
          disabled={it.disabled}
          onClick={() => {
            it.onSelect();
            onClose();
          }}
        >
          {it.icon ? <span className="chatui-menu-icon" aria-hidden="true">{it.icon}</span> : null}
          <span>{it.label}</span>
        </button>
      ))}
    </div>
  );
}
