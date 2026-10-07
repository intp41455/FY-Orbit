import { useCallback, useEffect, useRef, useState } from 'react';
import { gitRepoApi, type GitCommit } from '../../api/gitRepo';
import { errorMessage } from '../ui';
import { parseUnifiedDiff, type ParsedDiff } from './diffParse';
import { useBase } from '../../hooks/useAutosave';

/**
 * P1-12 git 提交树图 — renders the REAL commit history of a git-repo workspace
 * (GET /api/git-repo/workspaces/{name}/commits, delivered by P1-03) as a
 * simplified DAG: commits are laid out newest-first on lanes derived from the
 * parent relations, with SVG edges child→parent. Nodes show sha[:7] + message +
 * author + relative time.
 *
 * 点击联动 diff：单击节点 = 该节点与其第一前驱的 diff；再点第二个节点 = 两个
 * 选中提交互比。diff 走 P1-11 的真实端点 GET /api/git-repo/.../diff，解析复用
 * P1-11 的 diffParse（DiffView 组件为独立面板，这里内嵌同一解析器的简版渲染）。
 */

const ROW_H = 48;
const LANE_W = 16;
const GRAPH_LEFT = 12;
/**
 * 提交图 lane 配色 —— 07-收口与验收.md §3 B 的**具名例外**（逐条判定结论）。
 *
 * 这是数据可视化的分类色板：六条 lane 必须彼此可分辨，色相分离度是功能需求。
 * tokens.css 的九档状态令牌是为「状态语义」设计的，不是为「分类系列」设计的，
 * 把六条 lane 硬塞进状态令牌会让相邻 lane 撞色（例如 sky-400 与 sky-600
 * 在细线宽度下难以区分），那是拿可分辨性换治理度，属于功能倒退。
 *
 * 例外成立的前提是「不含绿色」，此处逐值核过：
 *   #38bdf8 R56/G189/B248  非绿
 *   #a78bfa R167/G139/B250 非绿
 *   #a5b4fc R165/G180/B252 非绿
 *   #fbbf24 R251/G191/B36  非绿
 *   #f87171 R248/G113/B113 非绿
 *   #f472b6 R244/G114/B182 非绿
 * 原先第三色是 #5eead4（R94/G234/B212，G 明显高于 R 与 B，判定为绿），已剔除，
 * 换成靛蓝 #a5b4fc 保持六色分离度。
 *
 * 若日后要收编，建议在 tokens.css 新增一条 --ui-cat-1..6 的分类色阶，
 * 而不是挪用状态令牌。已列入总报告「下一批」。
 */
const LANE_COLORS = ['#38bdf8', '#a78bfa', '#a5b4fc', '#fbbf24', '#f87171', '#f472b6'];
const STORAGE_KEY = 'fy.commitgraph.workspace';

export interface LaidOutCommit extends GitCommit {
  lane: number;
  row: number;
}

export interface GraphEdge {
  fromRow: number;
  fromLane: number;
  toRow: number;
  toLane: number;
}

export interface GraphLayout {
  nodes: LaidOutCommit[];
  laneCount: number;
  edges: GraphEdge[];
}

/**
 * Simplified git-graph lane assignment: walk commits newest→oldest; each commit
 * occupies the lane its sha already holds (or the first free lane), hands its
 * lane to the first parent, and opens lanes for additional (merge) parents.
 */
export function layoutCommits(commits: GitCommit[]): GraphLayout {
  const lanes: (string | null)[] = [];
  const nodes: LaidOutCommit[] = [];
  const rowBySha = new Map<string, number>();
  const laneBySha = new Map<string, number>();

  commits.forEach((c, row) => {
    let lane = lanes.indexOf(c.sha);
    if (lane === -1) {
      lane = lanes.findIndex((x) => x === null);
      if (lane === -1) {
        lanes.push(null);
        lane = lanes.length - 1;
      }
    }
    const parents = c.parents ?? [];
    if (parents.length === 0) {
      lanes[lane] = null;
    } else {
      const firstTaken = lanes.indexOf(parents[0]);
      lanes[lane] = firstTaken === -1 ? parents[0] : null;
      for (let i = 1; i < parents.length; i++) {
        if (lanes.indexOf(parents[i]) === -1) {
          const free = lanes.findIndex((x) => x === null);
          if (free === -1) lanes.push(parents[i]);
          else lanes[free] = parents[i];
        }
      }
    }
    rowBySha.set(c.sha, row);
    laneBySha.set(c.sha, lane);
    nodes.push({ ...c, lane, row });
  });

  const edges: GraphEdge[] = [];
  for (const n of nodes) {
    for (const p of n.parents ?? []) {
      const toRow = rowBySha.get(p);
      const toLane = laneBySha.get(p);
      if (toRow === undefined || toLane === undefined) continue;
      edges.push({ fromRow: n.row, fromLane: n.lane, toRow, toLane });
    }
  }
  return { nodes, laneCount: Math.max(1, lanes.length), edges };
}

export function relativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  return new Date(t).toLocaleDateString('zh-CN');
}

function laneColor(lane: number): string {
  return LANE_COLORS[lane % LANE_COLORS.length];
}

interface DiffState {
  loading: boolean;
  error: string | null;
  parsed: ParsedDiff | null;
  truncated: boolean;
  empty: boolean;
}

const IDLE_DIFF: DiffState = { loading: false, error: null, parsed: null, truncated: false, empty: false };

export function CommitGraph() {
  useBase({ surface: 'web/src/components/workbench/CommitGraph' });
  const [nameInput, setNameInput] = useState<string>(() => {
    try {
      return window.localStorage.getItem(STORAGE_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [activeName, setActiveName] = useState<string>('');
  const [commits, setCommits] = useState<GitCommit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [second, setSecond] = useState<string | null>(null);

  const [diff, setDiff] = useState<DiffState>(IDLE_DIFF);
  const diffSeq = useRef(0);

  const load = useCallback(async (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    setSecond(null);
    setDiff(IDLE_DIFF);
    try {
      const res = await gitRepoApi.commitHistory(trimmed, 100);
      try {
        window.localStorage.setItem(STORAGE_KEY, trimmed);
      } catch {
        /* private mode — the name just does not survive a refresh */
      }
      setActiveName(trimmed);
      setCommits(res.commits);
    } catch (e) {
      setCommits(null);
      setActiveName(trimmed);
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const layout = commits ? layoutCommits(commits) : null;
  const selectedCommit = commits?.find((c) => c.sha === selected) ?? null;
  const secondCommit = commits?.find((c) => c.sha === second) ?? null;

  // Click → diff: single selection diffs against its first parent; two
  // selections diff with each other (the older commit is the diff base).
  useEffect(() => {
    if (!activeName || !selected) {
      setDiff(IDLE_DIFF);
      return;
    }
    let base: string;
    let target: string;
    if (second) {
      const selRow = layout?.nodes.find((n) => n.sha === selected)?.row ?? 0;
      const secRow = layout?.nodes.find((n) => n.sha === second)?.row ?? 0;
      base = selRow > secRow ? selected : second;
      target = base === selected ? second : selected;
    } else {
      const parent = commits?.find((c) => c.sha === selected)?.parents?.[0];
      if (!parent) {
        // 根提交没有前驱：与工作区对照仍可见其引入的全部内容。
        base = selected;
        target = 'worktree';
      } else {
        base = parent;
        target = selected;
      }
    }
    const seq = ++diffSeq.current;
    setDiff({ ...IDLE_DIFF, loading: true });
    gitRepoApi
      .getDiff(activeName, base, target)
      .then((r) => {
        if (seq !== diffSeq.current) return;
        const parsed = parseUnifiedDiff(r.text ?? '');
        setDiff({ loading: false, error: null, parsed, truncated: r.truncated, empty: parsed.files.length === 0 });
      })
      .catch((e) => {
        if (seq !== diffSeq.current) return;
        setDiff({ loading: false, error: errorMessage(e), parsed: null, truncated: false, empty: false });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, second, activeName, commits]);

  function onNodeClick(sha: string) {
    if (!selected) {
      setSelected(sha);
      return;
    }
    if (selected === sha) {
      setSelected(null);
      setSecond(null);
      return;
    }
    if (second === sha) {
      setSecond(null);
      return;
    }
    setSecond(sha);
  }

  const svgWidth = GRAPH_LEFT * 2 + Math.max(1, layout?.laneCount ?? 1) * LANE_W;
  const totalAdd = diff.parsed?.files.reduce((n, f) => n + f.additions, 0) ?? 0;
  const totalDel = diff.parsed?.files.reduce((n, f) => n + f.deletions, 0) ?? 0;

  return (
    <div className="card commit-graph-pane" data-testid="commit-graph">
      <div className="row spread">
        <strong>提交树图</strong>
        <span className="muted small">按 parents 关系布局 · 点击节点联动 diff，选中两个节点可互比</span>
      </div>
      <div className="row commit-graph-controls">
        <label htmlFor="commit-graph-ws">git 工作区名称</label>
        <input
          id="commit-graph-ws"
          data-testid="commit-graph-ws-input"
          value={nameInput}
          onChange={(e) => setNameInput(e.target.value)}
          placeholder="如 p12-demo（/api/git-repo 下的工作区名）"
        />
        <button
          className="primary small"
          data-testid="commit-graph-load"
          onClick={() => void load(nameInput)}
          disabled={loading || !nameInput.trim()}
        >
          {loading ? '加载中…' : '加载历史'}
        </button>
      </div>

      {error && <div className="error-text" role="alert">{error}</div>}
      {loading && <div className="muted">加载提交历史…</div>}
      {layout && layout.nodes.length === 0 && (
        <div className="muted" data-testid="commit-graph-empty">
          工作区 {activeName} 尚无提交历史。
        </div>
      )}

      {layout && layout.nodes.length > 0 && (
        <div className="commit-graph-body" style={{ ['--cg-row-h' as string]: `${ROW_H}px` }}>
          <svg
            className="commit-graph-lanes"
            width={svgWidth}
            height={layout.nodes.length * ROW_H}
            aria-hidden="true"
          >
            {layout.edges.map((e, i) => {
              const x1 = GRAPH_LEFT + e.fromLane * LANE_W;
              const y1 = e.fromRow * ROW_H + ROW_H / 2;
              const x2 = GRAPH_LEFT + e.toLane * LANE_W;
              const y2 = e.toRow * ROW_H + ROW_H / 2;
              return (
                <path
                  key={i}
                  d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={laneColor(e.toLane)}
                  strokeWidth={1.6}
                  opacity={0.75}
                />
              );
            })}
            {layout.nodes.map((n) => (
              <circle
                key={n.sha}
                cx={GRAPH_LEFT + n.lane * LANE_W}
                cy={n.row * ROW_H + ROW_H / 2}
                r={5}
                fill={laneColor(n.lane)}
                stroke={selected === n.sha || second === n.sha ? 'var(--ui-ink-1)' : 'none'}
                strokeWidth={2}
              />
            ))}
          </svg>
          <ul className="commit-graph-rows" style={{ paddingLeft: svgWidth }}>
            {layout.nodes.map((n) => (
              <li
                key={n.sha}
                className={`commit-node ${selected === n.sha ? 'selected' : ''} ${second === n.sha ? 'second' : ''}`}
                data-sha={n.sha}
                data-testid="commit-node"
                style={{ height: ROW_H }}
                onClick={() => onNodeClick(n.sha)}
              >
                <span className="commit-node-sha">{n.sha.slice(0, 7)}</span>
                <span className="commit-node-msg" title={n.message}>{n.message}</span>
                <span className="commit-node-meta muted">
                  {n.author} · {relativeTime(n.date)}
                  {(n.parents?.length ?? 0) > 1 ? ' · merge' : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selectedCommit && (
        <div className="commit-detail" data-testid="commit-detail">
          <div className="row spread">
            <strong className="small" data-testid="commit-detail-range">
              {selectedCommit.sha.slice(0, 7)}
              {secondCommit ? ` ↔ ${secondCommit.sha.slice(0, 7)}` : ' ↔ 前驱'}
            </strong>
            <span className="muted small">
              {selectedCommit.author} · {relativeTime(selectedCommit.date)}
            </span>
          </div>
          <div className="commit-detail-msg">{selectedCommit.message}</div>
          {secondCommit && <div className="muted small">{secondCommit.message}</div>}
        </div>
      )}

      {diff.loading && <div className="muted" data-testid="commit-diff-loading">正在加载 diff…</div>}
      {diff.error && <div className="error-text" role="alert" data-testid="commit-diff-error">{diff.error}</div>}
      {diff.parsed && !diff.loading && !diff.error && (
        <div className="commit-diff" data-testid="commit-diff">
          <div className="row spread diff-summary">
            <span className="muted small">
              {diff.parsed.files.length} 个文件 · +{totalAdd} / -{totalDel}
              {diff.truncated ? '（diff 过大已截断）' : ''}
            </span>
          </div>
          {diff.parsed.files.length === 0 && (
            <div className="muted small" data-testid="commit-diff-empty">这两个提交之间没有文本差异。</div>
          )}
          {diff.parsed.files.map((f) => (
            <section className="diff-file" key={`${f.oldPath}->${f.newPath}`} data-testid="commit-diff-file">
              <header className="diff-file-header">
                <span className="diff-file-path" title={f.newPath || f.oldPath}>
                  {f.oldPath !== f.newPath ? `${f.oldPath} → ${f.newPath}` : f.newPath}
                </span>
                <span className="muted small">
                  <span className="diff-stat-add">+{f.additions}</span> / <span className="diff-stat-del">-{f.deletions}</span>
                </span>
              </header>
              {f.isBinary && <div className="muted small">二进制文件，不渲染文本差异。</div>}
              {f.hunks.map((h, hi) => (
                <div className="diff-hunk" key={hi}>
                  <div className="diff-hunk-header">{h.header}</div>
                  <div className="diff-lines">
                    {h.lines.map((ln, li) => (
                      <div
                        key={li}
                        className={`diff-row diff-${ln.kind}`}
                        data-testid={ln.kind === 'meta' ? 'diff-meta-row' : 'diff-row'}
                      >
                        <span className="diff-sign">{ln.kind === 'add' ? '+' : ln.kind === 'del' ? '-' : ''}</span>
                        <span className="diff-text">{ln.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
