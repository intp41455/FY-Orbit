import { useCallback, useEffect, useState } from 'react';
import { workbenchApi, type GitStatus, type GitDiff, type GitFileState } from '../../api/workbench';
import { errorMessage } from '../ui';

interface Props {
  workspaceId: string;
}

export function GitPanel({ workspaceId }: Props) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [diff, setDiff] = useState<GitDiff | null>(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await workbenchApi.gitStatus(workspaceId);
      setStatus(s);
      setSelected(new Set());
      setDiff(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function toggle(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function viewDiff(staged: boolean) {
    const paths = staged
      ? (status?.staged ?? []).map((f) => f.path)
      : [...selected];
    if (paths.length === 0) {
      setError('请先选择要查看差异的文件。');
      return;
    }
    setError(null);
    setNote(null);
    try {
      const d = await workbenchApi.gitDiff(workspaceId, paths, staged);
      setDiff(d);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function stage() {
    const paths = [...selected];
    if (paths.length === 0) return;
    try {
      await workbenchApi.gitStage(workspaceId, paths);
      setNote(`已暂存 ${paths.length} 个文件。`);
      void refresh();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function commit() {
    const paths = [...selected];
    if (paths.length === 0 || !message.trim()) {
      setError('请选择文件并填写提交信息。');
      return;
    }
    try {
      const r = await workbenchApi.gitCommit(workspaceId, message.trim(), paths);
      setNote(`已提交 ${r.commit}（${r.count} 个文件）。`);
      setMessage('');
      void refresh();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function revert(paths: string[]) {
    if (paths.length === 0) return;
    try {
      const r = await workbenchApi.gitRevert(workspaceId, paths);
      setNote(`已撤销 ${r.count} 个文件的改动。`);
      void refresh();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const allFiles: (GitFileState & { kind: string })[] = status
    ? [
        ...status.conflicts.map((f) => ({ path: f.path, state: `conflict(${f.state})`, kind: 'conflict' })),
        ...status.staged.map((f) => ({ path: f.path, state: `staged(${f.state})`, kind: 'staged' })),
        ...status.unstaged.map((f) => ({ path: f.path, state: `unstaged(${f.state})`, kind: 'unstaged' })),
        ...status.untracked.map((f) => ({ path: f.path, state: 'untracked', kind: 'untracked' })),
      ]
    : [];

  return (
    <div className="card git-pane">
      <div className="row spread">
        <strong>差异审查 / Git</strong>
        <span className="muted small">
          {status ? `分支 ${status.branch} · ahead ${status.ahead} / behind ${status.behind}` : ''}
          <button className="small" style={{ marginLeft: '0.5rem' }} onClick={() => void refresh()} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
        </span>
      </div>

      {error && <div className="error-text" role="alert">{error}</div>}
      {note && <div className="notice ok">{note}</div>}
      {status?.has_conflicts && <div className="notice danger">存在合并冲突，需先解决后再提交。</div>}

      {status && allFiles.length === 0 && <div className="muted">工作区干净，无待处理改动。</div>}

      <ul className="git-list">
        {allFiles.map((f) => (
          <li key={`${f.kind}:${f.path}`} className={`git-row ${selected.has(f.path) ? 'selected' : ''}`}>
            <input type="checkbox" checked={selected.has(f.path)} onChange={() => toggle(f.path)} />
            <span className="git-state">{f.state}</span>
            <span className="git-path" title={f.path}>{f.path}</span>
            <button className="small" onClick={() => void revert([f.path])}>撤销</button>
          </li>
        ))}
      </ul>

      <div className="row gap">
        <button className="small" onClick={() => void viewDiff(false)}>查看工作区差异</button>
        <button className="small" onClick={() => void viewDiff(true)}>查看已暂存差异</button>
        <button className="small" onClick={() => void stage()} disabled={selected.size === 0}>暂存选中</button>
      </div>

      {diff && (
        <div className="diff-box">
          <div className="row spread">
            <span className="muted small">
              {diff.staged ? '已暂存' : '工作区'} · +{diff.additions} / -{diff.deletions}
              {diff.truncated ? '（已截断）' : ''}
            </span>
          </div>
          <pre className="diff-content">{diff.text || '（无差异内容）'}</pre>
        </div>
      )}

      <div className="field-stack" style={{ marginTop: '0.5rem' }}>
        <label>提交信息
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="描述本次改动…"
          />
        </label>
        <button className="primary small" onClick={() => void commit()} disabled={selected.size === 0 || !message.trim()}>
          提交选中文件
        </button>
      </div>
    </div>
  );
}
