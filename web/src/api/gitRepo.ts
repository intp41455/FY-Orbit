// Typed client for the P1-03 git-repo API (src/find_yourself/api/routes/git_repo.py).
// P1-11 diff 可视化与 P1-12 提交树图共用：提交历史 + 两个 commit（或 vs 工作区）
// 的 unified diff 原文。仅消费，不写仓库状态。
import { request } from './client';

export interface GitRepoCommit {
  sha: string;
  parents: string[];
  author: string;
  /** ISO-8601 author date, e.g. 2026-10-03T08:15:00+08:00 */
  date: string;
  message: string;
}

/** P1-12 侧使用的别名（与 GitRepoCommit 同一后端结构）。 */
export type GitCommit = GitRepoCommit;

export interface CommitHistory {
  workspace: string;
  count: number;
  commits: GitRepoCommit[];
}

export interface GitRepoDiff {
  workspace: string;
  from: string;
  to: string | null;
  context?: number;
  truncated: boolean;
  text: string;
}

export const gitRepoApi = {
  /** P1-11：提交历史（树图与 diff 下拉共用数据源）。 */
  listCommits: (workspace: string, limit = 100, branch?: string) =>
    request<CommitHistory>(`/api/git-repo/workspaces/${encodeURIComponent(workspace)}/commits`, {
      query: { limit, branch },
    }),

  /** P1-12：提交历史（listCommits 的别名，语义同 GET /commits）。 */
  commitHistory: (workspace: string, limit = 100, branch?: string) =>
    request<CommitHistory>(`/api/git-repo/workspaces/${encodeURIComponent(workspace)}/commits`, {
      query: { limit, branch },
    }),

  /** P1-11：两个 commit（或 to=worktree 即 commit vs 工作区）的 unified diff。 */
  getDiff: (workspace: string, fromRef: string, toRef?: string, context = 3) =>
    request<GitRepoDiff>(`/api/git-repo/workspaces/${encodeURIComponent(workspace)}/diff`, {
      query: { from: fromRef, to: toRef, context },
    }),
};
