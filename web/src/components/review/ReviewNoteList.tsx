/**
 * P9 · 意见卡片与列表（扩展点：承载圈选 / 画笔 / 语音三类定位）。
 *
 * 与既有 `ReviewMode.tsx` 内联卡片相比，这里抽成组件以便：
 * - 按 `mode` 渲染不同定位摘要（DOM 选择器 / 归一化区域 / 笔迹点数）；
 * - 展示 TOKEN 优化三项（真写标记 / 短码 / 结构差分）；
 * - 承载状态流转（open / resolved / dismissed）。
 */
import type { ReviewNoteView } from '../../api/review';
import { useBase } from '../../hooks/useAutosave';

const MODE_LABEL: Record<ReviewNoteView['mode'], string> = {
  dom: '点选',
  region: '圈选',
  freehand: '批注',
};

const STATE_LABEL: Record<ReviewNoteView['state'], string> = {
  open: '待改',
  resolved: '已改完',
  dismissed: '不采纳',
};

export interface ReviewNoteCardProps {
  note: ReviewNoteView;
  onNoteChange?: (id: string, text: string) => void;
  onStateChange?: (id: string, state: ReviewNoteView['state']) => void;
  onDelete?: (id: string) => void;
}

/** 单条意见卡片。 */
export function ReviewNoteCard({
  note,
  onNoteChange,
  onStateChange,
  onDelete,
}: ReviewNoteCardProps) {
  return (
    <div
      data-testid={`review-note-${note.short_code}`}
      data-mode={note.mode}
      data-state={note.state}
      style={{
        border: '1px solid var(--line)', borderRadius: 12, padding: 10,
        background: 'var(--glass-base)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span
          data-testid="note-short-code"
          style={{
            fontSize: 11, fontWeight: 700, color: '#fff',
            background: 'var(--sky)', borderRadius: 6, padding: '1px 6px',
          }}
        >
          {note.short_code}
        </span>
        <span data-testid="note-marker" style={{ fontSize: 12, color: 'var(--sky-deep)', fontWeight: 600 }}>
          {note.marker || `<${note.tag}>`}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {MODE_LABEL[note.mode]} · {STATE_LABEL[note.state]}
        </span>
        {note.changed && (
          <span data-testid="note-changed" style={{ fontSize: 11, color: 'var(--amber)' }}>
            ⚠️ 结构已变
          </span>
        )}
      </div>

      {note.mode === 'region' && (
        <div data-testid="note-region" style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          圈选：x={fmt(note.region.x)} y={fmt(note.region.y)} w={fmt(note.region.w)} h={fmt(note.region.h)}
        </div>
      )}
      {note.mode === 'freehand' && (
        <div data-testid="note-freehand" style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          批注：{note.strokes.length} 笔
          {note.audio_ref ? ` · 含语音` : ''}
          {note.audio_transcript ? `（${note.audio_transcript}）` : ''}
        </div>
      )}
      {note.mode === 'dom' && note.text && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
          文本：{note.text}
        </div>
      )}

      <textarea
        data-testid={`note-input-${note.short_code}`}
        value={note.note}
        onChange={(e) => onNoteChange?.(note.id, e.target.value)}
        placeholder="这里写修改意见…"
        style={{
          width: '100%', minHeight: 52, marginTop: 6, borderRadius: 8,
          border: '1px solid var(--line)', padding: 8, fontSize: 13,
          background: '#fff', color: 'var(--text-main)', resize: 'vertical',
          boxSizing: 'border-box',
        }}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
        <button data-testid={`note-resolve-${note.short_code}`} type="button"
          onClick={() => onStateChange?.(note.id, note.state === 'resolved' ? 'open' : 'resolved')}
          style={chip(note.state === 'resolved')}>
          {note.state === 'resolved' ? '撤销完成' : '标记已改完'}
        </button>
        <button data-testid={`note-dismiss-${note.short_code}`} type="button"
          onClick={() => onStateChange?.(note.id, note.state === 'dismissed' ? 'open' : 'dismissed')}
          style={chip(note.state === 'dismissed')}>
          不采纳
        </button>
        <button data-testid={`note-delete-${note.short_code}`} type="button"
          onClick={() => onDelete?.(note.id)}
          style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--rose)', background: 'none', border: 'none', cursor: 'pointer' }}>
          删除
        </button>
      </div>
    </div>
  );
}

export interface ReviewNoteListProps {
  notes: ReviewNoteView[];
  onNoteChange?: (id: string, text: string) => void;
  onStateChange?: (id: string, state: ReviewNoteView['state']) => void;
  onDelete?: (id: string) => void;
}

/** 意见列表（空态给引导语）。 */
export function ReviewNoteList({ notes, onNoteChange, onStateChange, onDelete }: ReviewNoteListProps) {
  useBase({ surface: 'web/src/components/review/ReviewNoteList' });
  if (notes.length === 0) {
    return (
      <p data-testid="review-empty" style={{ color: 'var(--text-faint)', fontSize: 13, textAlign: 'center', marginTop: 30 }}>
        还没有意见。点击元素、圈选区域或画笔批注，这里会出现卡片。
      </p>
    );
  }
  return (
    <div data-testid="review-note-list" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {notes.map((n) => (
        <ReviewNoteCard
          key={n.id}
          note={n}
          onNoteChange={onNoteChange}
          onStateChange={onStateChange}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function fmt(v: number | undefined): string {
  return typeof v === 'number' ? v.toFixed(3) : '?';
}

function chip(active: boolean): React.CSSProperties {
  return {
    fontSize: 12, padding: '3px 10px', borderRadius: 999,
    border: '1px solid var(--line)',
    background: active ? 'var(--sky)' : 'var(--glass-base)',
    color: active ? '#fff' : 'var(--text-main)', cursor: 'pointer',
  };
}
