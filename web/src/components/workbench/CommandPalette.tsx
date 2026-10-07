import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LineIcon, type LineIconName } from '../ui/LineIcon';
import { workbenchApi, type TreeEntry } from '../../api/workbench';
import { useBase } from '../../hooks/useAutosave';

/**
 * Ctrl/⌘+K 命令面板（08-包A-体验规范 §13）。
 *
 * 外壳：`.ui-overlay` + `.ui-modal` 轻量变体，`role=combobox`，
 *       宽度 `min(640px, 92vw)`，输入行高 48。
 * 分组：最近（≤4）→ 命令（工作台本域）→ 文件（需工作区）→ 跳转（路由）。
 * 键盘：↑↓ 选择、Enter 执行、Tab 切文件搜索模式（name ↔ content）、Esc 关闭。
 * 动效：面板淡入 180ms + ≤10px 位移；reduced-motion 降到 1ms（由 workbench.css §12 兜底）。
 */

export interface CommandPaletteProps {
  open: boolean;
  workspaceId: string | null;
  onClose: () => void;
  onSelectFile: (path: string) => void;
  onRun: (id: string) => void;
}

/* ---------- 最近记录（fy.cmd.recent） ---------- */

interface RecentEntry {
  type: 'command' | 'file';
  id: string;       // command id 或 file path
  label: string;
  at: number;       // timestamp
}

const RECENT_KEY = 'fy.cmd.recent';
const MAX_RECENT = 8;

function readRecent(): RecentEntry[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RecentEntry[];
    if (!Array.isArray(parsed)) return [];
    return parsed.slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

function pushRecent(entry: RecentEntry): void {
  try {
    const cur = readRecent().filter((e) => !(e.type === entry.type && e.id === entry.id));
    cur.unshift(entry);
    const next = cur.slice(0, MAX_RECENT);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* private mode / quota — 最近记录退化为会话内不持久 */
  }
}

function removeRecent(type: RecentEntry['type'], id: string): RecentEntry[] {
  const cur = readRecent().filter((e) => !(e.type === type && e.id === id));
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(cur));
  } catch {
    /* noop */
  }
  return cur;
}

/* ---------- 命令清单（§13） ---------- */

interface CmdItem {
  id: string;
  label: string;
  hint?: string;
  icon: LineIconName;
  group: 'command';
}

const COMMANDS: CmdItem[] = [
  { id: 'toggle-dock', label: '折叠/展开终端坞', icon: 'terminal', group: 'command' },
  { id: 'section-1', label: '聚焦「差异审查」区段', icon: 'flow', group: 'command' },
  { id: 'section-2', label: '聚焦「文档树」区段', icon: 'file', group: 'command' },
  { id: 'section-3', label: '聚焦「提交图」区段', icon: 'branch', group: 'command' },
  { id: 'section-4', label: '聚焦「备份与回滚」区段', icon: 'archive', group: 'command' },
  { id: 'focus-editor', label: '聚焦中区「编辑器·预览」', icon: 'code', group: 'command' },
  { id: 'focus-diff', label: '聚焦中区「差异审查」', icon: 'flow', group: 'command' },
  { id: 'focus-git', label: '聚焦中区「Git 工作区」', icon: 'branch', group: 'command' },
  { id: 'save-file', label: '保存当前文件', icon: 'save', group: 'command' },
  { id: 'reload-tree', label: '重新加载文件树', icon: 'refresh', group: 'command' },
  { id: 'refresh-git', label: '刷新 Git 状态', icon: 'refresh', group: 'command' },
  { id: 'shortcuts', label: '打开本页快捷键速查 ?', icon: 'info', group: 'command' },
];

/* ---------- 跳转清单（§13） ---------- */

interface JumpItem {
  id: string;
  label: string;
  icon: LineIconName;
  group: 'jump';
}

const JUMPS: JumpItem[] = [
  { id: 'goto-approvals', label: '审批中心', icon: 'approvals', group: 'jump' },
  { id: 'goto-dispatch', label: '子 Agent 派发', icon: 'dispatch', group: 'jump' },
  { id: 'goto-chat-debug', label: 'Chat 调试', icon: 'chat', group: 'jump' },
  { id: 'goto-canvas', label: '协作画布', icon: 'canvas', group: 'jump' },
  { id: 'goto-settings', label: '设置与数据', icon: 'settings', group: 'jump' },
  { id: 'goto-skills', label: 'Agent 与技能', icon: 'skills', group: 'jump' },
];

/* ---------- 统一行模型 ---------- */

interface Row {
  key: string;
  group: 'recent' | 'command' | 'file' | 'jump';
  label: string;
  hint?: string;
  icon: LineIconName;
  kind: 'command' | 'file' | 'jump';
  rawId: string;   // command id / file path / jump id
}

const GROUP_TITLES: Record<Row['group'], string> = {
  recent: '最近',
  command: '命令',
  file: '文件',
  jump: '跳转',
};

const GROUP_ORDER: Row['group'][] = ['recent', 'command', 'file', 'jump'];

/* ---------- 组件 ---------- */

export function CommandPalette({ open, workspaceId, onClose, onSelectFile, onRun }: CommandPaletteProps) {
  useBase({ surface: 'web/src/components/workbench/CommandPalette' });
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<RecentEntry[]>([]);
  const [files, setFiles] = useState<TreeEntry[]>([]);
  const [fileMode, setFileMode] = useState<'name' | 'content'>('name');
  const [searching, setSearching] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // 打开时：聚焦输入框、清 query、读最近记录
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    setFiles([]);
    setFileError(null);
    setFileMode('name');
    setRecent(readRecent());
    // 延迟聚焦，让 DOM 先挂载
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  // 搜索文件（debounce 200ms）
  useEffect(() => {
    if (!open || !workspaceId || !query.trim()) {
      setFiles([]);
      setFileError(null);
      return;
    }
    const q = query.trim();
    // 只在非命令关键字时搜索文件：如果 query 以 ">" 或 "/" 开头，不搜文件
    if (q.startsWith('>') || q.startsWith('/')) return;
    let cancelled = false;
    setSearching(true);
    setFileError(null);
    const t = window.setTimeout(async () => {
      try {
        const r = await workbenchApi.search(workspaceId, q, fileMode, 8);
        if (cancelled) return;
        setFiles(r.hits.filter((h) => h.type === 'file'));
      } catch (e) {
        if (cancelled) return;
        setFileError(e instanceof Error ? e.message : '搜索失败');
        setFiles([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [open, workspaceId, query, fileMode]);

  // 构建行列表
  const rows = useMemo<Row[]>(() => {
    const q = query.trim().toLowerCase();
    const out: Row[] = [];

    // 最近（≤4，空时不显示分组）
    if (!q) {
      const top4 = recent.slice(0, 4);
      for (const r of top4) {
        if (r.type === 'command') {
          const cmd = COMMANDS.find((c) => c.id === r.id);
          if (cmd) {
            out.push({ key: `recent-cmd-${cmd.id}`, group: 'recent', label: cmd.label, icon: cmd.icon, kind: 'command', rawId: cmd.id });
          }
        } else {
          out.push({ key: `recent-file-${r.id}`, group: 'recent', label: r.id, hint: '最近文件', icon: 'file', kind: 'file', rawId: r.id });
        }
      }
    }

    // 命令
    for (const cmd of COMMANDS) {
      if (!q || cmd.label.toLowerCase().includes(q) || cmd.id.includes(q)) {
        out.push({ key: `cmd-${cmd.id}`, group: 'command', label: cmd.label, icon: cmd.icon, kind: 'command', rawId: cmd.id });
      }
    }

    // 文件（需工作区）
    if (workspaceId) {
      for (const f of files) {
        out.push({ key: `file-${f.path}`, group: 'file', label: f.name, hint: f.path, icon: 'file', kind: 'file', rawId: f.path });
      }
    }

    // 跳转
    for (const j of JUMPS) {
      if (!q || j.label.toLowerCase().includes(q) || j.id.includes(q)) {
        out.push({ key: `jump-${j.id}`, group: 'jump', label: j.label, icon: j.icon, kind: 'jump', rawId: j.id });
      }
    }

    return out;
  }, [query, recent, files, workspaceId]);

  // active clamp
  useEffect(() => {
    if (active >= rows.length) setActive(0);
  }, [rows.length, active]);

  // 滚动 active 行可见
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  // 执行选中行
  const execute = useCallback(
    (row: Row) => {
      if (row.kind === 'file') {
        onSelectFile(row.rawId);
        pushRecent({ type: 'file', id: row.rawId, label: row.label, at: Date.now() });
      } else if (row.kind === 'command') {
        onRun(row.rawId);
        pushRecent({ type: 'command', id: row.rawId, label: row.label, at: Date.now() });
      } else {
        // jump
        onRun(row.rawId);
      }
      onClose();
    },
    [onSelectFile, onRun, onClose],
  );

  // 键盘
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (rows[active]) execute(rows[active]);
      } else if (e.key === 'Tab') {
        // Tab 切文件搜索模式 name ↔ content
        e.preventDefault();
        setFileMode((m) => (m === 'name' ? 'content' : 'name'));
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        // Del 删除最近记录中的条目（仅当 active 行是 recent 组时）
        const row = rows[active];
        if (row && row.group === 'recent') {
          e.preventDefault();
          const type = row.kind === 'file' ? 'file' : 'command';
          setRecent(removeRecent(type, row.rawId));
        }
      }
    },
    [rows, active, execute, onClose],
  );

  if (!open) return null;

  // 分组渲染：跳过空组
  const grouped: { group: Row['group']; rows: Row[] }[] = [];
  for (const g of GROUP_ORDER) {
    const items = rows.filter((r) => r.group === g);
    if (items.length > 0) grouped.push({ group: g, rows: items });
  }

  let runningIdx = 0;

  return (
    <div className="ui-overlay" onClick={onClose} role="presentation">
      <div
        className="ui-modal wb-cmd-palette"
        role="combobox"
        aria-expanded="true"
        aria-haspopup="listbox"
        aria-activedescendant={rows[active] ? `wb-cp-row-${active}` : undefined}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
        style={{ width: 'min(640px, 92vw)' }}
      >
        {/* 输入行（高 48） */}
        <div className="wb-cp-input-bar">
          <LineIcon name="search" size={20} />
          <input
            ref={inputRef}
            type="text"
            className="wb-cp-input"
            placeholder="搜索文件与命令…（Tab 切换文件名/内容搜索）"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            role="combobox"
            aria-autocomplete="list"
            aria-controls="wb-cp-list"
          />
          {workspaceId && (
            <span className="ui-kbd wb-cp-mode" data-mode={fileMode} title="Tab 切换搜索模式">
              {fileMode === 'name' ? '文件名' : '内容'}
            </span>
          )}
          <button type="button" className="wb-cp-close" aria-label="关闭命令面板" onClick={onClose}>
            <LineIcon name="x" size={18} />
          </button>
        </div>

        {/* 结果区 */}
        <div className="wb-cp-list" id="wb-cp-list" ref={listRef} role="listbox">
          {rows.length === 0 ? (
            <div className="wb-cp-empty">
              <p className="wb-cp-empty-title">没有匹配的结果</p>
              <p className="wb-cp-empty-hint">试试文件名的前几个字，或输入命令名。</p>
            </div>
          ) : (
            grouped.map(({ group, rows: grows }) => (
              <div key={group} className="wb-cp-group">
                <div className="wb-cp-group-title" role="presentation">
                  {GROUP_TITLES[group]}
                  {group === 'file' && searching && <span className="wb-cp-searching">搜索中…</span>}
                  {group === 'file' && !workspaceId && (
                    <span className="wb-cp-hint">未选择工作区，仅能引用最近打开的文件</span>
                  )}
                  {group === 'file' && fileError && (
                    <span className="wb-cp-hint wb-cp-error">{fileError}</span>
                  )}
                </div>
                {grows.map((row) => {
                  const idx = runningIdx++;
                  return (
                    <button
                      key={row.key}
                      id={`wb-cp-row-${idx}`}
                      data-idx={idx}
                      type="button"
                      className={`wb-cp-row${idx === active ? ' is-active' : ''}`}
                      role="option"
                      aria-selected={idx === active}
                      onMouseEnter={() => setActive(idx)}
                      onClick={() => execute(row)}
                    >
                      <span className="wb-cp-row-ico">
                        <LineIcon name={row.icon} size={16} />
                      </span>
                      <span className="wb-cp-row-label">{row.label}</span>
                      {row.hint && <span className="wb-cp-row-hint muted small">{row.hint}</span>}
                      {row.group === 'recent' && (
                        <span className="wb-cp-row-tag">最近</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
