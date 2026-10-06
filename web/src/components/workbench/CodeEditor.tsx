import { useEffect, useRef, useState } from 'react';
import { workbenchApi, type FileContent } from '../../api/workbench';
import { errorMessage } from '../ui';
import { publishPreviewDraft } from './previewBus'; // P1-10：向预览窗发布实时草稿
import type { CodeDispatchContext } from './WorkbenchDispatchDialog'; // P1 交互双件：代码区派发上下文

/**
 * P1-13 侧边定位：暴露给兄弟面板（OutlinePanel 等）的命令式跳转句柄。
 * jumpToLine 把 textarea 滚动到目标行、选中该行（或其中 highlight 文本）
 * 并触发一次短暂的高亮闪烁。
 */
export interface EditorJumpApi {
  jumpToLine: (line: number, highlightText?: string) => void;
}

interface Props {
  workspaceId: string;
  path: string | null;
  /** P1-13: 可选注入；传入时编辑器把 jumpToLine 句柄挂到该 ref 上。 */
  editorApiRef?: React.MutableRefObject<EditorJumpApi | null>;
  /** P1 交互双件：传入时显示「派发给 Agent」入口，点击带上选中片段弹出派发对话框。 */
  onDispatch?: (ctx: CodeDispatchContext) => void;
  /**
   * 读盘后把真实 FileContent（sha256 / revision / size_bytes）回传父层，
   * 供右区「验证状态卡」显示制品哈希——不另发一次请求，也不许自己算假哈希。
   */
  onFileMeta?: (fc: FileContent | null) => void;
}

export function CodeEditor({
  workspaceId,
  path,
  editorApiRef,
  onDispatch,
  onFileMeta,
}: Props) {
  const [content, setContent] = useState<string>('');
  const [meta, setMeta] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  // P1-09: oversized files are rejected by the backend (>2 MiB inline limit).
  // Surfaced as a dedicated lazy-load notice instead of a raw error string.
  const [largeFile, setLargeFile] = useState(false);
  // P1-13 侧边定位：textarea 引用 + 跳转后的高亮闪烁状态。
  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const [flash, setFlash] = useState(false);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 每次渲染都重新挂句柄（无依赖数组），保证 jumpToLine 闭包里的 content
  // 始终是最新值；卸载时清空句柄并回收闪烁定时器。
  useEffect(() => {
    if (!editorApiRef) return;
    editorApiRef.current = {
      jumpToLine(line, highlightText) {
        const ta = areaRef.current;
        if (!ta) return;
        const lines = content.split('\n');
        if (lines.length === 0) return;
        const idx = Math.min(Math.max(1, Math.round(line)), lines.length) - 1;
        let offset = 0;
        for (let i = 0; i < idx; i++) offset += lines[i].length + 1;
        const lineLen = lines[idx]?.length ?? 0;
        ta.focus();
        // 优先选中行内的目标关键词；否则整行选中，视觉上即“该行被高亮”。
        let selStart = offset;
        let selEnd = offset + lineLen;
        if (highlightText) {
          const kw = highlightText.toLowerCase();
          const rel = lines[idx]?.toLowerCase().indexOf(kw) ?? -1;
          if (rel >= 0) {
            selStart = offset + rel;
            selEnd = selStart + highlightText.length;
          }
        }
        ta.setSelectionRange(selStart, selEnd);
        // 滚动：目标行置于视口上 1/3 处（行高来自 computed style，兜底 20px）。
        const cs = getComputedStyle(ta);
        let lineHeight = parseFloat(cs.lineHeight);
        if (!Number.isFinite(lineHeight) || lineHeight <= 0) {
          lineHeight = (parseFloat(cs.fontSize) || 16) * 1.5;
        }
        ta.scrollTop = Math.max(0, idx * lineHeight - ta.clientHeight / 3);
        ta.dataset.lastJump = String(idx + 1);
        setFlash(true);
        if (flashTimer.current) clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setFlash(false), 1200);
      },
    };
    return () => {
      editorApiRef.current = null;
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  });

  useEffect(() => {
    if (!path) {
      setContent('');
      setMeta(null);
      setDirty(false);
      setError(null);
      setSavedNote(null);
      setLargeFile(false);
      onFileMeta?.(null);
      publishPreviewDraft({ path: null, content: '' }); // P1-10：清空预览
      return;
    }
    let cancelled = false;    setLoading(true);
    setError(null);
    setSavedNote(null);
    setLargeFile(false);
    workbenchApi
      .readFile(workspaceId, path)
      .then((fc) => {
        if (cancelled) return;
        setMeta(fc);
        onFileMeta?.(fc);
        setContent(fc.content);
        setDirty(false);
        publishPreviewDraft({ path, content: fc.content }); // P1-10：首载内容进入预览
      })
      .catch((e) => {
        if (cancelled) return;
        // The backend rejects files above the 2 MiB inline-edit limit with a
        // 422 whose message names the limit — render a dedicated 大文件 notice
        // (truncated/lazy-load guidance) instead of a raw error string.
        const msg = errorMessage(e);
        if (/inline-edit limit|exceeds the \d+ byte/i.test(msg)) setLargeFile(true);
        else setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, path]);

  async function save() {
    if (!path || !meta || meta.read_only || !meta.editable) return;
    setSaving(true);
    setError(null);
    setSavedNote(null);
    try {
      const res = await workbenchApi.writeFile(workspaceId, path, {
        content,
        expected_revision: meta.revision,
      });
      setMeta((m) => {
        const next = m ? { ...m, revision: res.revision, sha256: res.sha256 } : m;
        onFileMeta?.(next);
        return next;
      });
      setDirty(false);
      setSavedNote(`已保存（新版本 r${res.revision}）`);
    } catch (e) {
      // 409 conflict => expected_revision drift; reload to show latest.
      const status = (e as { status?: number }).status;
      if (status === 409) {
        setError('保存冲突：文件版本已变更，请重新加载后再编辑。');
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setSaving(false);
    }
  }

  // P1 交互双件：收集派发上下文——优先选中片段，其次光标所在行。
  function handleDispatch() {
    if (!onDispatch || !path) return;
    const ta = areaRef.current;
    let snippet = '';
    let line: number | null = null;
    if (ta && ta.selectionEnd > ta.selectionStart) {
      snippet = content.slice(ta.selectionStart, ta.selectionEnd);
      line = content.slice(0, ta.selectionStart).split('\n').length;
    } else if (ta) {
      line = content.slice(0, ta.selectionStart).split('\n').length;
      snippet = content.split('\n')[line - 1] ?? '';
    } else {
      snippet = content.slice(0, 2000);
    }
    onDispatch({ path, snippet: snippet.trim(), line });
  }

  if (!path) {
    return (
      <div className="card editor-pane">
        <div className="muted">从左侧文件树选择一个文件以编辑。</div>
      </div>
    );
  }

  return (
    <div className="card editor-pane">
      <div className="row spread">
        <strong title={path}>{path.split('/').pop()}</strong>
        <span className="row" style={{ gap: '0.5rem' }}>
          {onDispatch && (
            <button
              type="button"
              className="small"
              data-testid="code-dispatch-entry"
              onClick={handleDispatch}
              disabled={!content.trim()}
              aria-label="把选中代码派发给 Agent"
            >
              派发给 Agent
            </button>
          )}
          <span className="muted">{meta ? `r${meta.revision} · ${meta.encoding}` : ''}</span>
        </span>
      </div>
      <div className="muted small">{path}</div>
      {loading && <div className="muted">读取中…</div>}
      {error && <div className="error-text" role="alert">{error}</div>}
      {savedNote && <div className="notice ok">{savedNote}</div>}
      {meta && meta.read_only && <div className="notice warn">只读文件，不可编辑。</div>}
      {meta && meta.binary && (
        <div className="binary-placeholder" data-testid="binary-placeholder" role="note">
          <div className="binary-icon" aria-hidden="true">BIN</div>
          <div>
            <strong>二进制文件</strong>
            <div className="muted small">
              该文件不是 UTF-8 文本（encoding={meta.encoding}，{meta.size_bytes} 字节）。
              为避免乱码，已停止文本渲染；请使用终端或专用工具处理。
            </div>
          </div>
        </div>
      )}
      {largeFile && (
        <div className="notice warn" data-testid="large-file-notice" role="note">
          大文件已停止内联加载（超过 2 MiB 内联限制）。内容不会一次性载入：
          请通过终端分页查看，或按需下载后再打开，以避免卡顿与内存占用。
        </div>
      )}
      {error && <div className="error-text" role="alert">{error}</div>}
      {savedNote && <div className="notice ok">{savedNote}</div>}
      {!meta?.binary && !largeFile && (
        <textarea
          ref={areaRef}
          className={`code-area${flash ? ' code-flash' : ''}`}
          data-testid="code-area"
          value={content}
          spellCheck={false}
          readOnly={!meta || meta.read_only || !meta.editable}
          onChange={(e) => {
            setContent(e.target.value);
            setDirty(true);
            publishPreviewDraft({ path, content: e.target.value }); // P1-10：实时草稿进入预览
          }}
        />
      )}
      <div className="row spread" style={{ marginTop: '0.5rem' }}>
        <span className="muted small">{dirty ? '● 未保存' : '已同步'}</span>
        <button
          className="primary small"
          onClick={() => void save()}
          disabled={saving || !meta || meta.read_only || !meta.editable || !dirty}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  );
}
