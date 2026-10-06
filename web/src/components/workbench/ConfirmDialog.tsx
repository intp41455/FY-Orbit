import { useCallback, useEffect, useRef } from 'react';
import { LineIcon } from '../ui/LineIcon';
import { WbIcon } from './WbIcon';
import { isComposingLike } from './wbKeys';

/**
 * 包 A 破坏性动作的统一确认弹窗。
 *
 * 规范落地：
 *  - .ui-modal（tokens.css 共享层）+ role="dialog" aria-modal
 *  - 焦点陷阱：Tab / Shift+Tab 在弹窗内循环
 *  - 初始焦点落在「取消」；回车/空格即取消（默认不执行破坏动作）
 *  - Esc 取消；关闭后焦点归还触发控件
 *  - 不使用 homotethethy 缩放转场（共享层 .ui-modal 自带 260ms pop，已足够）
 */

export interface ConfirmSpec {
  title: string;
  /** 逐条说明这个动作到底会做什么——不允许只有标题。 */
  body: React.ReactNode;
  confirmLabel: string;
  /** 破坏性默认 true：确认按钮走 danger 语义。 */
  destructive?: boolean;
  busyLabel?: string;
}

interface Props extends ConfirmSpec {
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  destructive = true,
  busyLabel,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  // 关闭后焦点归还触发控件，避免焦点掉到 body（键盘用户丢失上下文）。
  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => {
      const back = restoreRef.current;
      if (back && document.contains(back)) back.focus();
    };
  }, []);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // IME 组字期间不接管任何键。
      if (isComposingLike(e)) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
        return;
      }
      if (e.key !== 'Tab' || !boxRef.current) return;
      const nodes = Array.from(boxRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (n) => n.offsetParent !== null,
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      } else if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    },
    [onCancel],
  );

  return (
    <div className="ui-overlay" onMouseDown={onCancel}>
      <div
        className="ui-modal wb-confirm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wb-confirm-title"
        ref={boxRef}
        onKeyDown={onKeyDown}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="wb-confirm-hd">
          <span className={`wb-confirm-glyph${destructive ? ' is-danger' : ''}`} aria-hidden="true">
            <LineIcon name="alert" size={20} />
          </span>
          <h3 className="wb-confirm-title" id="wb-confirm-title">
            {title}
          </h3>
        </div>
        <div className="wb-confirm-bd">{body}</div>
        <div className="wb-confirm-ft">
          <button
            type="button"
            ref={cancelRef}
            className="ui-btn ui-btn--sm wb-confirm-cancel"
            onClick={onCancel}
            disabled={busy}
          >
            取消
          </button>
          <button
            type="button"
            className={`ui-btn ui-btn--sm ${destructive ? 'ui-btn--danger' : 'ui-btn--primary'}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? busyLabel ?? '处理中…' : confirmLabel}
          </button>
        </div>
        <p className="wb-confirm-foot">
          <span className="ui-kbd">Esc</span> 取消 · 初始焦点在「取消」，空格/回车不会误执行。
        </p>
      </div>
      {/* 视觉冗余：右上角关闭×Esc 等价入口（部分用户只找指针路径）。 */}
      <button
        type="button"
        className="wb-confirm-fab"
        aria-label="取消并关闭"
        onClick={onCancel}
        disabled={busy}
      >
        <WbIcon name="x" size={16} />
      </button>
    </div>
  );
}
