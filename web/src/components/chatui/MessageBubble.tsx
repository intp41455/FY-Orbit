/**
 * 包 C · 消息气泡（chatui 私有）
 * ---------------------------------------------------------------------------
 * 视觉：用户气泡右对齐 --ui-glass-3；AI 气泡左对齐 --ui-glass-1 + 左侧 2px
 * --ui-sky-200 竖线；系统消息居中 --ui-glass-1 小字虚线边；AI 头像用
 * --ui-teal-300 圆底（纯装饰 aria-hidden）。
 * 诚实实现：Message 无 sources/citations 字段，引用卡只从 composer 里用户真实
 * 选中的 @ 引用渲染（按 @《文档名》 文本匹配），无引用整块不渲染，绝不塞示例数据。
 * 「停止生成」只在 submitting / 发送后重取期间出现在最后一条 AI 气泡右上角，
 * 因为后端无 SSE 流式，不做常驻停止按钮。
 */
import type { Message } from '../../api/types';
import type { KBDocument } from '../../api/knowledge';
import { LineIcon } from '../ui/LineIcon';
import { HoverActions } from './HoverActions';

const ROLE_LABEL: Record<Message['role'], string> = {
  user: '我',
  assistant: '助手',
  system: '系统',
};

/** 从正文里解析用户真实插入的 @《文档名》 引用。 */
export function parseCitations(content: string, refs: KBDocument[]): KBDocument[] {
  if (!content || refs.length === 0) return [];
  const out: KBDocument[] = [];
  const re = /@《([^》]+)》/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    const name = m[1].trim();
    const hit = refs.find((d) => d.name === name);
    if (hit && !out.some((d) => d.id === hit.id)) out.push(hit);
  }
  return out;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

export interface MessageBubbleProps {
  message: Message;
  flash: boolean;
  refs: KBDocument[];
  onCopy: (m: Message) => void;
  onQuote: (m: Message) => void;
  onOpenRef: (doc: KBDocument) => void;
  /** 仅最后一条 AI 气泡：submitting 或发送后重取期间 */
  generating?: boolean;
  onStop?: () => void;
}

export function MessageBubble({
  message,
  flash,
  refs,
  onCopy,
  onQuote,
  onOpenRef,
  generating = false,
  onStop,
}: MessageBubbleProps) {
  const role = message.role;
  const cites = role === 'user' ? parseCitations(message.content, refs) : [];

  return (
    <div
      id={`msg-${message.id}`}
      data-message-id={message.id}
      className={`bubble chatui-bubble chatui-bubble--${role}${flash ? ' msg-flash' : ''}`}
    >
      {role === 'assistant' ? (
        <span className="chatui-avatar" aria-hidden="true">
          <LineIcon name="sparkles" size={16} />
        </span>
      ) : null}

      <div className="chatui-bubble-main">
        <span className="chatui-sr-only">{ROLE_LABEL[role]}：</span>
        <div className="chatui-bubble-content">{message.content}</div>

        {cites.length > 0 ? (
          <div className="chatui-cites">
            <span className="chatui-cites-label">引用来源</span>
            {cites.map((d) => (
              <span
                key={d.id}
                className="chatui-cite"
                onDoubleClick={() => onOpenRef(d)}
                title="双击打开该文档所在的知识库"
              >
                <LineIcon name="link" size={14} />
                《{d.name}》
              </span>
            ))}
          </div>
        ) : null}

        <div className="chatui-bubble-meta">
          {ROLE_LABEL[role]} · {formatTime(message.created_at)}
        </div>
      </div>

      <HoverActions
        className="chatui-bubble-actions"
        actions={[
          { key: 'copy', label: '复制这条消息', icon: <LineIcon name="copy" size={16} />, onClick: () => onCopy(message) },
          { key: 'quote', label: '引用这条消息', icon: <LineIcon name="chat" size={16} />, onClick: () => onQuote(message) },
        ]}
      />

      {generating ? (
        <div className="chatui-generating">
          <span className="ui-dot ui-dot--running chatui-dot" aria-hidden="true" />
          <span className="chatui-generating-text">生成中…</span>
          <button type="button" className="ui-btn ui-btn--sm chatui-stop-btn" onClick={() => onStop?.()}>
            <LineIcon name="stop" size={16} /> 停止生成
          </button>
        </div>
      ) : null}
    </div>
  );
}
