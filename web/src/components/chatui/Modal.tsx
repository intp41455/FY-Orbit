/**
 * 包 C · 模态（chatui 私有）
 * 只用于「重命名…」（本机备注，无后端 PATCH 接口，不伪装成服务端改名）。
 * Esc 优先级最低（联想 > 右键菜单 > 抽屉 > 模态），由本组件在自身内消费 Esc；
 * 焦点打开时落在输入框，关闭后交还给触发者（由调用方 onClose 负责回焦）。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useBase } from '../../hooks/useAutosave';

export interface ModalProps {
  title: string;
  labelId?: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  hint?: ReactNode;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}

export function Modal({
  title,
  labelId = 'chatui-modal-input',
  initialValue = '',
  placeholder = '',
  confirmLabel = '保存',
  hint,
  onCancel,
  onConfirm,
}: ModalProps) {
  useBase({ surface: 'web/src/components/chatui/Modal' });
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  return (
    <div className="chatui-modal-layer">
      <div className="ui-overlay chatui-overlay" onMouseDown={onCancel} />
      <div
        className="ui-modal chatui-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="chatui-modal-title"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            onCancel();
          }
        }}
      >
        <div className="ui-panel-hd chatui-modal-hd">
          <h3 className="ui-panel-title" id="chatui-modal-title">{title}</h3>
        </div>
        <div className="ui-panel-bd chatui-modal-bd">
          <label className="ui-label" htmlFor={labelId}>本机备注名（不改后端标题）</label>
          <input
            id={labelId}
            ref={inputRef}
            className="ui-input"
            value={value}
            placeholder={placeholder}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault();
                onConfirm(value);
              }
            }}
          />
          {hint ? <p className="ui-hint chatui-modal-hint">{hint}</p> : null}
        </div>
        <div className="ui-panel-ft chatui-modal-ft">
          <button type="button" className="ui-btn ui-btn--ghost" onClick={onCancel}>取消</button>
          <button type="button" className="ui-btn ui-btn--primary" onClick={() => onConfirm(value)}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
