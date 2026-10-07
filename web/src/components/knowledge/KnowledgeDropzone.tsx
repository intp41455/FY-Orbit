// W3 拖拽导入区：拖放 + 点击选择 + 键盘可达（Enter/Space 触发文件选择）。
// 只负责收集 File 列表并回调 onFiles；本地预检（扩展名/大小）交给 kbFormat。
import { useRef, useState } from 'react';
import type { DragEvent, KeyboardEvent } from 'react';
import { useBase } from '../../hooks/useAutosave';

export interface KnowledgeDropzoneProps {
  onFiles: (files: File[]) => void;
  busy?: boolean;
  hint?: string;
}

export function KnowledgeDropzone({ onFiles, busy = false, hint }: KnowledgeDropzoneProps) {
  useBase({ surface: 'web/src/components/knowledge/KnowledgeDropzone' });
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const emit = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    onFiles(Array.from(list));
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    emit(e.dataTransfer?.files ?? null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      inputRef.current?.click();
    }
  };

  return (
    <div
      className={`kb-dropzone${dragging ? ' dragging' : ''}`}
      role="button"
      tabIndex={0}
      aria-label="拖拽或选择本地文档导入知识库"
      data-testid="kb-dropzone"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      onKeyDown={onKeyDown}
    >
      <strong>{busy ? '正在导入…' : '拖入文档，或点击选择文件'}</strong>
      <span className="muted">
        {hint ?? '支持 .md / .txt / .pdf / .docx，单文件 ≤ 20MB；文件只留在本机。'}
      </span>
      <input
        ref={inputRef}
        type="file"
        multiple
        data-testid="kb-file-input"
        style={{ display: 'none' }}
        onChange={(e) => {
          emit(e.target.files);
          e.target.value = '';
        }}
      />
    </div>
  );
}