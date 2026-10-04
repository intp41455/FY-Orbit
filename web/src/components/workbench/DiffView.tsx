import { useCallback, useState } from 'react';
import { gitRepoApi, type GitRepoCommit } from '../../api/gitRepo';
import { errorMessage } from '../ui';
import { parseUnifiedDiff, type ParsedDiff } from './diffParse';

const WORKTREE = 'worktree';

/**
 * P1-11 diff 可视化：选择两个 commit（或 commit vs 工作区）→ 拉取
 * unified diff 原文 → parseUnifiedDiff 解析 → 文件分组渲染。
 * 行级配色：新增绿 / 删除红 / 成对修改琥珀标记（.diff-mod），
 * 行内容与 git diff 原始输出逐行对应（见 diffParse 一致性测试）。
 */
export function DiffView({ initialWorkspace = '' }: { initialWorkspace?: string }) {
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [commits, setCommits] = useState<GitRepoCommit[]>([]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState(WORKTREE);
  const [parsed, setParsed] = useState<ParsedDiff | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCommits = useCallback(async () => {
    if (!workspace.trim()) {
      setError('请填写 git 工作区名称。');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await gitRepoApi.listCommits(workspace.trim());
      setCommits(r.commits);
      setFrom(r.commits[1]?.sha ?? r.commits[0]?.sha ?? '');
      setTo(WORKTREE);
      if (r.commits.length === 0) setError('该工作区还没有提交历史。');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  const loadDiff = useCallback(async () => {
    if (!workspace.trim() || !from) {
      setError('请先加载提交历史并选择基准 commit。');
      return;
    }
    setLoadingDiff(true);
    setError(null);
    try {
      const r = await gitRepoApi.getDiff(workspace.trim(), from, to || WORKTREE);
      setParsed(parseUnifiedDiff(r.text));
      setTruncated(r.truncated);
    } catch (e) {
      setError(errorMessage(e));
      setParsed(null);
    } finally {
      setLoadingDiff(false);
    }
  }, [workspace, from, to]);

  const totalAdd = parsed?.files.reduce((n, f) => n + f.additions, 0) ?? 0;
  const totalDel = parsed?.files.reduce((n, f) => n + f.deletions, 0) ?? 0;

  return (
    <div className="card diff-view" data-testid="diff-view">
      <div className="row spread">
        <strong>Diff 可视化（两次 commit 对照）</strong>
        <span className="muted small">新增绿 · 删除红 · 修改琥珀标记</span>
      </div>

      <div className="field-stack diff-toolbar">
        <label>
          git 工作区名称
          <input
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            placeholder="如 diff-consistency"
            data-testid="diff-workspace-input"
          />
        </label>
        <div className="row gap">
          <button className="small" onClick={() => void loadCommits()} disabled={loading}>
            {loading ? '加载中…' : '加载提交历史'}
          </button>
        </div>
        {commits.length > 0 && (
          <>
            <label>
              基准 commit（旧）
              <select value={from} onChange={(e) => setFrom(e.target.value)} data-testid="diff-from">
                {commits.map((c) => (
                  <option key={c.sha} value={c.sha}>
                    {c.sha.slice(0, 10)} · {c.message}
                  </option>
                ))}
              </select>
            </label>
            <label>
              目标 commit（新，可选工作区）
              <select value={to} onChange={(e) => setTo(e.target.value)} data-testid="diff-to">
                <option value={WORKTREE}>工作区（未提交改动）</option>
                {commits.map((c) => (
                  <option key={c.sha} value={c.sha}>
                    {c.sha.slice(0, 10)} · {c.message}
                  </option>
                ))}
              </select>
            </label>
            <div className="row gap">
              <button className="primary small" onClick={() => void loadDiff()} disabled={loadingDiff || !from}>
                {loadingDiff ? '生成中…' : '生成 diff 图'}
              </button>
            </div>
          </>
        )}
      </div>

      {error && <div className="error-text" role="alert">{error}</div>}

      {parsed && (
        <>
          <div className="row spread diff-summary" data-testid="diff-summary">
            <span className="muted small">
              {parsed.files.length} 个文件 · +{totalAdd} / -{totalDel}
              {truncated ? '（diff 过大已截断）' : ''}
            </span>
          </div>
          {parsed.files.map((f) => (
            <section className="diff-file" key={`${f.oldPath}->${f.newPath}`} data-testid="diff-file">
              <header className="diff-file-header">
                <span className="diff-file-path" title={f.newPath || f.oldPath}>
                  {f.oldPath !== f.newPath ? `${f.oldPath} → ${f.newPath}` : f.newPath}
                </span>
                <span className="muted small">
                  <span className="diff-stat-add">+{f.additions}</span>
                  {' / '}
                  <span className="diff-stat-del">-{f.deletions}</span>
                </span>
              </header>
              {f.isBinary && <div className="muted small diff-binary-note">二进制文件，不渲染文本差异。</div>}
              {!f.isBinary && f.hunks.length === 0 && <div className="muted small">（无差异内容）</div>}
              {f.hunks.map((h, hi) => (
                <div className="diff-hunk" key={hi}>
                  <div className="diff-hunk-header">{h.header}</div>
                  <div className="diff-lines">
                    {h.lines.map((ln, li) => (
                      <div
                        key={li}
                        className={`diff-row diff-${ln.kind}${ln.modified ? ' diff-mod' : ''}`}
                        data-testid={ln.kind === 'meta' ? 'diff-meta-row' : 'diff-row'}
                      >
                        <span className="diff-ln diff-ln-old">{ln.oldLine ?? ''}</span>
                        <span className="diff-ln diff-ln-new">{ln.newLine ?? ''}</span>
                        <span className="diff-sign">{ln.kind === 'add' ? '+' : ln.kind === 'del' ? '-' : ''}</span>
                        <span className="diff-text">{ln.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  );
}
