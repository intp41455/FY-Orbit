import { useCallback, useEffect, useRef, useState } from 'react';
import { workbenchApi, type TreeEntry, type FileContent } from '../../api/workbench';
import { errorMessage } from '../ui';

/**
 * P1-22 文档树图 — a standalone document tree over a real workspace directory.
 *
 * Differences from the workbench FileTree: this view auto-expands first-level
 * directories so a real project renders many visible document nodes at once,
 * and clicking a document opens its real content inline (read-only preview).
 * Directories collapse/expand on demand; children are lazy-loaded from the
 * real tree API.
 */

interface Props {
  workspaceId: string;
}

interface DirState {
  expanded: boolean;
  children: TreeEntry[] | null;
  loading: boolean;
  error: string | null;
}

async function listDir(workspaceId: string, path: string): Promise<TreeEntry[]> {
  const res = await workbenchApi.getTree(workspaceId, path, 1, 0, 500);
  return res.entries;
}

export function DocumentTree({ workspaceId }: Props) {
  const [rootEntries, setRootEntries] = useState<TreeEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [opened, setOpened] = useState<FileContent | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const openSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDirs({});
    setOpened(null);
    setOpenError(null);
    setOpenPath(null);
    listDir(workspaceId, '')
      .then(async (entries) => {
        if (cancelled) return;
        setRootEntries(entries);
        setLoading(false);
        // Auto-expand first-level directories so the tree shows a dense,
        // real document listing without forcing a click per folder.
        const dirEntries = entries.filter((e) => e.type === 'dir' && e.has_children !== false);
        const results = await Promise.all(
          dirEntries.map(async (d) => {
            try {
              return [d.path, await listDir(workspaceId, d.path)] as const;
            } catch {
              return [d.path, null] as const;
            }
          }),
        );
        if (cancelled) return;
        setDirs((cur) => {
          const next = { ...cur };
          for (const [p, children] of results) {
            next[p] =
              children === null
                ? { expanded: true, children: null, loading: false, error: '子目录读取失败' }
                : { expanded: true, children, loading: false, error: null };
          }
          return next;
        });
      })
      .catch((e) => {
        if (!cancelled) {
          setError(errorMessage(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  const toggleDir = useCallback(
    async (entry: TreeEntry) => {
      const cur = dirs[entry.path];
      if (cur?.expanded && cur.children) {
        setDirs((s) => ({ ...s, [entry.path]: { ...s[entry.path], expanded: false } }));
        return;
      }
      if (cur?.children) {
        setDirs((s) => ({ ...s, [entry.path]: { ...s[entry.path], expanded: true } }));
        return;
      }
      setDirs((s) => ({
        ...s,
        [entry.path]: { expanded: true, children: s[entry.path]?.children ?? null, loading: true, error: null },
      }));
      try {
        const children = await listDir(workspaceId, entry.path);
        setDirs((s) => ({ ...s, [entry.path]: { expanded: true, children, loading: false, error: null } }));
      } catch (e) {
        setDirs((s) => ({ ...s, [entry.path]: { expanded: true, children: null, loading: false, error: errorMessage(e) } }));
      }
    },
    [dirs, workspaceId],
  );

  async function openDoc(entry: TreeEntry) {
    const seq = ++openSeq.current;
    setOpenPath(entry.path);
    setOpenError(null);
    setOpened(null);
    try {
      const fc = await workbenchApi.readFile(workspaceId, entry.path);
      if (seq === openSeq.current) setOpened(fc);
    } catch (e) {
      if (seq === openSeq.current) setOpenError(errorMessage(e));
    }
  }

  return (
    <div className="card doc-tree-pane">
      <div className="row spread">
        <strong>文档树图</strong>
        <span className="muted small">展开 / 折叠目录，点击文档打开内容</span>
      </div>
      {loading && <div className="muted">加载中…</div>}
      {error && <div className="error-text" role="alert">{error}</div>}
      {rootEntries && (
        <ul className="tree doc-tree" data-testid="doc-tree">
          {rootEntries.map((entry) => (
            <DocRow
              key={entry.path}
              entry={entry}
              depth={0}
              dirs={dirs}
              openPath={openPath}
              onToggleDir={toggleDir}
              onOpenDoc={openDoc}
            />
          ))}
          {rootEntries.length === 0 && <li className="muted">（空目录）</li>}
        </ul>
      )}
      {(openPath || openError) && (
        <div className="doc-tree-preview" data-testid="doc-tree-preview">
          {openError && <div className="error-text" role="alert">{openError}</div>}
          {opened && (
            <>
              <div className="row spread">
                <strong className="small" title={opened.path}>{opened.path}</strong>
                <span className="muted small">
                  {opened.binary ? '二进制' : opened.encoding} · {opened.size_bytes} 字节
                </span>
              </div>
              {opened.binary ? (
                <div className="muted small">二进制文件，不渲染文本内容。</div>
              ) : (
                <pre className="doc-tree-content">{opened.content}</pre>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

interface RowProps {
  entry: TreeEntry;
  depth: number;
  dirs: Record<string, DirState>;
  openPath: string | null;
  onToggleDir: (entry: TreeEntry) => void;
  onOpenDoc: (entry: TreeEntry) => void;
}

function DocRow({ entry, depth, dirs, openPath, onToggleDir, onOpenDoc }: RowProps) {
  const isDir = entry.type === 'dir';
  const state = dirs[entry.path];
  const expanded = isDir && (state?.expanded ?? false);
  const pad = { paddingLeft: `${depth * 14 + 6}px` };
  const isOpenDoc = !isDir && openPath === entry.path;

  return (
    <>
      <li
        className={`tree-row ${isDir ? 'dir' : 'file'} ${isOpenDoc ? 'selected' : ''}`}
        style={pad}
        data-path={entry.path}
        onClick={() => (isDir ? onToggleDir(entry) : onOpenDoc(entry))}
      >
        <span className="twisty">{isDir ? (expanded ? '▾' : '▸') : '·'}</span>
        <span className="name">{entry.name}</span>
        {typeof entry.size_bytes === 'number' && entry.size_bytes >= 1024 && (
          <span className="muted small">{Math.round(entry.size_bytes / 1024)} KB</span>
        )}
        {entry.binary && <span className="badge warn">bin</span>}
        {state?.loading && <span className="muted">…</span>}
      </li>
      {isDir && expanded && (
        <ul className="tree">
          {state?.error && <li className="error-text" style={pad}>{state.error}</li>}
          {state?.children?.length === 0 && <li className="muted" style={pad}>（空目录）</li>}
          {state?.children?.map((child) => (
            <DocRow
              key={child.path}
              entry={child}
              depth={depth + 1}
              dirs={dirs}
              openPath={openPath}
              onToggleDir={onToggleDir}
              onOpenDoc={onOpenDoc}
            />
          ))}
        </ul>
      )}
    </>
  );
}
