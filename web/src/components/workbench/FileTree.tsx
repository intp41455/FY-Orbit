import { useEffect, useRef, useState } from 'react';
import { workbenchApi, type TreeEntry } from '../../api/workbench';
import { errorMessage } from '../ui';
import { useBase } from '../../hooks/useAutosave';

interface Props {
  workspaceId: string;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
}

interface NodeState {
  expanded: boolean;
  children: TreeEntry[] | null;
  loading: boolean;
  error: string | null;
}

// P1-09: hidden (dotfile) visibility is a user choice and survives a reload.
const SHOW_HIDDEN_KEY = 'fy.filetree.showHidden';

function readShowHidden(): boolean {
  try {
    return window.localStorage.getItem(SHOW_HIDDEN_KEY) === '1';
  } catch {
    return false;
  }
}

function writeShowHidden(v: boolean): void {
  try {
    window.localStorage.setItem(SHOW_HIDDEN_KEY, v ? '1' : '0');
  } catch {
    /* private mode — the toggle just does not persist */
  }
}

export function FileTree({ workspaceId, selectedPath, onSelectFile }: Props) {
  useBase({ surface: 'web/src/components/workbench/FileTree' });
  const [root, setRoot] = useState<NodeState>({ expanded: true, children: null, loading: false, error: null });
  const [showHidden, setShowHidden] = useState<boolean>(readShowHidden);

  async function load(path: string): Promise<TreeEntry[]> {
    const res = await workbenchApi.getTree(workspaceId, path, 1);
    // Hidden files (dotfiles) are filtered on the client so the toggle is
    // instant; credential files never reach the client at all (server-side).
    return showHidden ? res.entries : res.entries.filter((e) => !e.name.startsWith('.'));
  }

  useEffect(() => {
    let cancelled = false;
    setRoot((s) => ({ ...s, loading: true, error: null }));
    load('')
      .then((entries) => {
        if (!cancelled) setRoot({ expanded: true, children: entries, loading: false, error: null });
      })
      .catch((e) => {
        if (!cancelled) setRoot((s) => ({ ...s, loading: false, error: errorMessage(e) }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  // Re-listing on toggle: expanded dirs reload lazily via `load` on next open,
  // and the root refresh below keeps the first level consistent immediately.
  useEffect(() => {
    let cancelled = false;
    load('')
      .then((entries) => {
        if (!cancelled) setRoot({ expanded: true, children: entries, loading: false, error: null });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHidden]);

  return (
    <div className="file-tree">
      <div className="row spread">
        <strong>文件树</strong>
        <label className="small" style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            data-testid="show-hidden-toggle"
            checked={showHidden}
            onChange={(e) => {
              setShowHidden(e.target.checked);
              writeShowHidden(e.target.checked);
            }}
          />
          显示隐藏文件
        </label>
      </div>
      {root.loading && <div className="muted">加载中…</div>}
      {root.error && <div className="error-text">{root.error}</div>}
      {root.children && (
        <ul className="tree">
          {root.children.map((entry) => (
            <TreeRow
              key={entry.path}
              entry={entry}
              depth={0}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
              load={load}
              showHidden={showHidden}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

interface RowProps {
  entry: TreeEntry;
  depth: number;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  load: (path: string) => Promise<TreeEntry[]>;
  showHidden: boolean;
}

function TreeRow({ entry, depth, selectedPath, onSelectFile, load, showHidden }: RowProps) {
  const [state, setState] = useState<NodeState>({ expanded: false, children: null, loading: false, error: null });

  // P1-09: when the hidden-file toggle flips, already-expanded directories
  // must re-list their children with the new visibility filter.
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    if (entry.type !== 'dir' || !state.children) return;
    let cancelled = false;
    load(entry.path)
      .then((children) => {
        if (!cancelled) setState((s) => ({ ...s, children }));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHidden]);

  async function toggle() {
    if (entry.type !== 'dir') return;
    if (state.expanded) {
      setState((s) => ({ ...s, expanded: false }));
      return;
    }
    if (state.children) {
      setState((s) => ({ ...s, expanded: true }));
      return;
    }
    setState((s) => ({ ...s, loading: true, expanded: true, error: null }));
    try {
      const children = await load(entry.path);
      setState({ expanded: true, children, loading: false, error: null });
    } catch (e) {
      setState((s) => ({ ...s, loading: false, error: errorMessage(e) }));
    }
  }

  const pad = { paddingLeft: `${depth * 14 + 6}px` };
  const isSelected = selectedPath === entry.path;

  return (
    <>
      <li
        className={`tree-row ${entry.type} ${isSelected ? 'selected' : ''}`}
        style={pad}
        onClick={() => (entry.type === 'dir' ? void toggle() : onSelectFile(entry.path))}
      >
        <span className="twisty">{entry.type === 'dir' ? (state.expanded ? '▾' : '▸') : '·'}</span>
        <span className="name">{entry.name}</span>
        {entry.read_only && <span className="badge warn">ro</span>}
        {state.loading && <span className="muted">…</span>}
      </li>
      {entry.type === 'dir' && state.expanded && state.children && (
        <ul className="tree">
          {state.children.length === 0 && <li className="muted" style={pad}>（空目录）</li>}
          {state.children.map((child) => (
            <TreeRow
              key={child.path}
              entry={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
              load={load}
              showHidden={showHidden}
            />
          ))}
        </ul>
      )}
      {state.error && <li className="error-text" style={pad}>{state.error}</li>}
    </>
  );
}
