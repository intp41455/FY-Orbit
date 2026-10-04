import { describe, it, expect, vi, beforeEach, afterAll } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CommitGraph, layoutCommits, relativeTime } from './CommitGraph';

/**
 * P1-12 git 提交树图 — 单元断言：
 *   1. ≥5 个真实结构（sha+parents+author+date+message）的提交节点渲染，
 *      含 merge 提交时的双 lane 布局；
 *   2. 节点显示 sha 前 7 位 + message + 作者 + 相对时间；
 *   3. 点击节点 → 详情面板出现，并调用真实 diff 端点（node vs 前驱）；
 *   4. 点击第二个节点 → 两节点互比 diff；
 *   5. 空历史展示空态。
 */

vi.mock('../../api/gitRepo', () => ({
  gitRepoApi: {
    commitHistory: vi.fn(),
    getDiff: vi.fn(),
  },
}));

import { gitRepoApi } from '../../api/gitRepo';

const SHA = (n: number) => `${n.toString(16).padStart(4, '0')}abcdef1234567890abcdef1234567890ab`;
// 线性 5 个 + 1 个 merge（第 4 号提交有两个 parent）
const HISTORY = [
  { sha: SHA(1), parents: [SHA(2)], author: '张三', date: '2026-10-03T09:00:00+08:00', message: 'feat: 新增工具函数' },
  { sha: SHA(2), parents: [SHA(3)], author: '张三', date: '2026-10-03T08:30:00+08:00', message: 'docs: 更新 README' },
  { sha: SHA(3), parents: [SHA(4)], author: '李四', date: '2026-10-03T08:00:00+08:00', message: 'fix: 修复初始化边界' },
  { sha: SHA(4), parents: [SHA(5), SHA(6)], author: '李四', date: '2026-10-03T07:30:00+08:00', message: 'merge: 合入特性分支' },
  { sha: SHA(5), parents: [], author: '王五', date: '2026-10-03T07:00:00+08:00', message: 'init: 项目骨架' },
  { sha: SHA(6), parents: [SHA(5)], author: '王五', date: '2026-10-03T07:10:00+08:00', message: 'feat: 分支上的功能' },
];

const DIFF_TEXT = [
  'diff --git a/src/utils.py b/src/utils.py',
  'index 1111111..2222222 100644',
  '--- a/src/utils.py',
  '+++ b/src/utils.py',
  '@@ -1,2 +1,3 @@',
  ' def main():',
  '-    pass',
  '+    return 42',
  '+',
].join('\n');

/**
 * 冻结「现在」，让相对时间断言与真实墙钟解耦。
 *
 * 背景：HISTORY 的 date 是固定值（2026-10-03），而 `relativeTime()` 读 `Date.now()`。
 * 二者相差超过 24h 后文案从「N 小时前」变成「N 天前」，用 `/\d+ (秒|分钟|小时)前/`
 * 这类**白名单正则**断言就会随日历腐烂 —— 这条用例曾在 2026-10-04 变红。
 *
 * 修法治根因：固定输入的同时固定输出。
 * 基准时刻选在最后一个提交（09:00）之后 1 小时，于是每个节点的相对时间恒为
 * 「N 小时前」，且**冻结后无论何时运行结果都一致**（含跨 24h/跨月/跨年）。
 */
const NOW = new Date('2026-10-03T10:00:00+08:00').getTime();

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  window.localStorage.clear();
  vi.mocked(gitRepoApi.commitHistory).mockResolvedValue({
    workspace: 'p12-demo',
    count: HISTORY.length,
    commits: HISTORY,
  });
  vi.mocked(gitRepoApi.getDiff).mockResolvedValue({
    workspace: 'p12-demo',
    from: SHA(2),
    to: SHA(1),
    truncated: false,
    text: DIFF_TEXT,
  });
});

describe('P1-12 布局算法', () => {
  it('按 parents 分配 lane：主线同列，merge 第二父分支开出新 lane', () => {
    const g = layoutCommits(HISTORY);
    expect(g.nodes).toHaveLength(6);
    // 线性链 1→2→3→4 全在 lane 0
    expect(g.nodes[0].lane).toBe(0);
    expect(g.nodes[1].lane).toBe(0);
    expect(g.nodes[2].lane).toBe(0);
    expect(g.nodes[3].lane).toBe(0);
    // 第二父 SHA(6) 在另一条 lane
    const branch = g.nodes.find((n) => n.sha === SHA(6));
    expect(branch?.lane).toBeGreaterThan(0);
    // child→parent 连边数量 = parents 总数
    expect(g.edges).toHaveLength(6);
  });
});

describe('P1-12 渲染与点击联动', () => {
  it('渲染 ≥5 个真实提交节点：sha 前 7 位 + message + 作者 + 相对时间', async () => {
    render(<CommitGraph />);
    const input = screen.getByTestId('commit-graph-ws-input');
    await userEvent.type(input, 'p12-demo');
    await userEvent.click(screen.getByTestId('commit-graph-load'));

    const nodes = await screen.findAllByTestId('commit-node');
    expect(nodes.length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText('feat: 新增工具函数')).toBeVisible();
    expect(screen.getByText(SHA(1).slice(0, 7))).toBeVisible();
    expect(screen.getAllByText(/张三 · /).length).toBeGreaterThan(0);
    // 相对时间：断言**精确值**而非白名单正则。
    // 冻结于 NOW=2026-10-03T10:00+08:00，HISTORY 最后一个提交是 09:00 → 恰为「1 小时前」；
    // 07:00 的最早提交 → 「3 小时前」。若哪天相对时间实现坏了，这里会红，而不是被宽松正则放过。
    const meta = screen.getAllByTestId('commit-node').map((el) => el.textContent ?? '');
    expect(meta.some((t) => t.includes('1 小时前'))).toBe(true);
    expect(meta.some((t) => t.includes('3 小时前'))).toBe(true);
    // merge 提交标记（节点 meta 徽标）
    expect(screen.getAllByText(/· merge/).length).toBeGreaterThan(0);
  });

  it('点击节点 → 详情出现，并请求该节点与其前驱的 diff', async () => {
    render(<CommitGraph />);
    await userEvent.type(screen.getByTestId('commit-graph-ws-input'), 'p12-demo');
    await userEvent.click(screen.getByTestId('commit-graph-load'));
    const nodes = await screen.findAllByTestId('commit-node');
    await userEvent.click(nodes[0]);

    const detail = await screen.findByTestId('commit-detail');
    expect(detail).toHaveTextContent(`${SHA(1).slice(0, 7)} ↔ 前驱`);
    await waitFor(() => expect(gitRepoApi.getDiff).toHaveBeenCalled());
    expect(gitRepoApi.getDiff).toHaveBeenCalledWith('p12-demo', SHA(2), SHA(1));
    const diffPane = await screen.findByTestId('commit-diff');
    expect(diffPane).toHaveTextContent('src/utils.py');
    expect(diffPane).toHaveTextContent('+2 / -1');
    expect(screen.getByTestId('commit-diff-file')).toBeVisible();
  });

  it('点击第二个节点 → 两个选中提交互比（旧提交为基准）', async () => {
    render(<CommitGraph />);
    await userEvent.type(screen.getByTestId('commit-graph-ws-input'), 'p12-demo');
    await userEvent.click(screen.getByTestId('commit-graph-load'));
    const nodes = await screen.findAllByTestId('commit-node');
    await userEvent.click(nodes[0]);
    await userEvent.click(nodes[2]);
    await waitFor(() =>
      expect(gitRepoApi.getDiff).toHaveBeenLastCalledWith('p12-demo', SHA(3), SHA(1)),
    );
    expect(screen.getByTestId('commit-detail-range')).toHaveTextContent('↔');
  });

  it('无提交历史时展示空态', async () => {
    vi.mocked(gitRepoApi.commitHistory).mockResolvedValue({
      workspace: 'empty-ws',
      count: 0,
      commits: [],
    });
    render(<CommitGraph />);
    await userEvent.type(screen.getByTestId('commit-graph-ws-input'), 'empty-ws');
    await userEvent.click(screen.getByTestId('commit-graph-load'));
    expect(await screen.findByTestId('commit-graph-empty')).toBeVisible();
  });
});

afterAll(() => {
  // beforeEach 里只冻结了 Date，但仍显式还原，避免影响同文件后续 describe 的真实时间语义
  vi.useRealTimers();
});

describe('P1-12 相对时间', () => {
  it('按秒/分/时/天分层格式化', () => {
    // 用与上面一致的冻结基准，避免直接读 Date.now()（墙钟）导致跨边界时漂移
    const now = NOW;
    expect(relativeTime(new Date(now - 5_000).toISOString())).toBe('5 秒前');
    expect(relativeTime(new Date(now - 3 * 60_000).toISOString())).toBe('3 分钟前');
    expect(relativeTime(new Date(now - 2 * 3_600_000).toISOString())).toBe('2 小时前');
    expect(relativeTime(new Date(now - 3 * 86_400_000).toISOString())).toBe('3 天前');
  });

  it('冻结基准与 HISTORY 保持在 24h 内，节点文案不会退化成「天前」', () => {
    // 这条是本文件时间漂移 flake 的直接护栏：HISTORY 是固定日期，一旦 NOW 被改动
    // 或远离 fixture（例如有人把 NOW 调到别的月份），relativeTime 会输出「N 天前」，
    // 下面这条就会红 —— 而它红的时机恰好就是旧正则失配的那个时机。
    const hours = HISTORY.map((c) => {
      const t = Date.parse(c.date);
      return (NOW - t) / 3_600_000;
    });
    expect(Math.min(...hours)).toBeGreaterThanOrEqual(0); // fixture 不在未来
    expect(Math.max(...hours)).toBeLessThan(24); // 且全部落在同一天内
    // 同一输入重复求值结果一致（确定性）
    const first = HISTORY.map((c) => relativeTime(c.date));
    const second = HISTORY.map((c) => relativeTime(c.date));
    expect(second).toEqual(first);
  });
});
