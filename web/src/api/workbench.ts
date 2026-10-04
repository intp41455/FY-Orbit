// Typed client for the 18 工程代码工作台 API (src/find_yourself/api/routes/workbench.py).
// Talks only to the real backend per FROZEN_CONTRACT: session is an HttpOnly
// cookie, writes echo the CSRF token, non-2xx is normalized by `request`.
import { buildApiUrl, request } from './client';

const BASE = '/api/workbench';

// ---------------------------------------------------------------------------
// Types (mirror the backend response envelopes)
// ---------------------------------------------------------------------------
export interface WorkspaceSummary {
  id: string;
  project_name: string;
  mode: string;
  authorized_root: string;
  branch: string;
  data_domain: string;
  state: string;
}

export interface TreeEntry {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size_bytes: number | null;
  mtime: number;
  read_only: boolean;
  encoding: string | null;
  binary: boolean;
  revision?: number;
  has_children?: boolean;
  error?: string | null;
}

export interface TreeResponse {
  workspace_id: string;
  path: string;
  entries: TreeEntry[];
  total: number;
  offset: number;
  limit: number;
  truncated: boolean;
  blocked_credential_entries: number;
}

export interface FileContent {
  workspace_id: string;
  path: string;
  content: string;
  encoding: string;
  binary: boolean;
  editable: boolean;
  size_bytes: number;
  sha256: string | null;
  revision: number;
  read_only: boolean;
}

export interface WriteResult {
  rel_path: string;
  revision: number;
  sha256: string | null;
  exists: boolean;
}

export interface TerminalSession {
  id: string;
  workspace_id: string;
  actor_identity: string;
  shell: string;
  pid: number | null;
  cols: number;
  rows: number;
  state: string;
  pty_backend: string;
  interactive: boolean;
  presentation: string;
  exit_code: number | null;
  stop_reason: string | null;
  timeout_seconds: number;
  cursor: number;
  created_at: string;
  ended_at: string | null;
}

export interface TerminalRead {
  id: string;
  output: string;
  cursor: number;
  new_cursor: number;
  state: string;
  exit_code: number | null;
  interactive: boolean;
}

export interface GitFileState {
  path: string;
  state: string;
}

export interface GitStatus {
  workspace_id: string;
  branch: string;
  ahead: number;
  behind: number;
  staged: GitFileState[];
  unstaged: GitFileState[];
  untracked: GitFileState[];
  conflicts: GitFileState[];
  has_conflicts: boolean;
  clean: boolean;
}

export interface GitDiff {
  workspace_id: string;
  staged: boolean;
  paths: string[];
  text: string;
  truncated: boolean;
  additions: number;
  deletions: number;
}

export interface PreviewSession {
  id: string;
  workspace_id: string;
  kind: string;
  entry_path: string;
  target_port: number | null;
  url: string | null;
  state: string;
  created_at: string;
}

/** P1-A: 统一预览源（static=工作区内 html/md；process=隔离进程预览）。 */
export interface PreviewSource {
  id: string;
  workspace_id: string;
  kind: 'static' | 'process';
  path: string;
  media_type: string;
  version: string;
  state: string;
  content_url?: string;
  preview_session_id?: string;
  url?: string | null;
  created_at: string;
}

/** P1-A: 预览源版本探测响应（前端轮询热刷新用）。 */
export interface PreviewSourceVersion {
  id: string;
  kind: string;
  version: string;
  state: string;
  preview_session_id?: string;
}

// ---------------------------------------------------------------------------
// Workspaces
// ---------------------------------------------------------------------------
export const workbenchApi = {
  listWorkspaces: () => request<{ items: WorkspaceSummary[]; count: number }>(`${BASE}/workspaces`),

  registerWorkspace: (body: {
    project_name: string;
    authorized_root: string;
    mode?: string;
    data_domain?: string;
    branch?: string;
    task_refs?: string[];
  }) => request<WorkspaceSummary>(`${BASE}/workspaces`, { method: 'POST', body }),

  getTree: (workspaceId: string, path = '', depth = 1, offset = 0, limit = 200) =>
    request<TreeResponse>(`${BASE}/workspaces/${workspaceId}/tree`, {
      query: { path, depth, offset, limit },
    }),

  readFile: (workspaceId: string, path: string) =>
    request<FileContent>(`${BASE}/workspaces/${workspaceId}/file`, { query: { path } }),

  writeFile: (
    workspaceId: string,
    path: string,
    body: { content: string; expected_revision?: number; source_task_id?: string; encoding?: string },
  ) => request<WriteResult>(`${BASE}/workspaces/${workspaceId}/file`, { method: 'POST', query: { path }, body }),

  renameFile: (workspaceId: string, path: string, newPath: string, expectedRevision?: number) =>
    request<WriteResult>(`${BASE}/workspaces/${workspaceId}/rename`, {
      method: 'POST',
      query: { path },
      body: { new_path: newPath, expected_revision: expectedRevision },
    }),

  deleteFile: (workspaceId: string, path: string, expectedRevision?: number) =>
    request<WriteResult>(`${BASE}/workspaces/${workspaceId}/delete`, {
      method: 'POST',
      query: { path },
      body: { expected_revision: expectedRevision },
    }),

  search: (workspaceId: string, q: string, mode: 'name' | 'content' = 'name', limit = 50) =>
    request<{ workspace_id: string; query: string; mode: string; hits: TreeEntry[] }>(
      `${BASE}/workspaces/${workspaceId}/search`,
      { query: { q, mode, limit } },
    ),

  // -------------------------------------------------------------------------
  // Terminal
  // -------------------------------------------------------------------------
  createTerminal: (
    workspaceId: string,
    body: { shell?: string; cols?: number; rows?: number; timeout_seconds?: number; rel_cwd?: string } = {},
  ) => request<TerminalSession>(`${BASE}/workspaces/${workspaceId}/terminals`, { method: 'POST', body }),

  listTerminals: (workspaceId: string) =>
    request<{ items: TerminalSession[]; count: number }>(`${BASE}/workspaces/${workspaceId}/terminals`),

  describeTerminal: (sessionId: string) =>
    request<TerminalSession>(`${BASE}/terminals/${sessionId}`),

  writeTerminal: (sessionId: string, data: string) =>
    request<{ id: string; written: number }>(`${BASE}/terminals/${sessionId}/write`, {
      method: 'POST',
      body: { data },
    }),

  readTerminal: (sessionId: string, waitSeconds = 0.5) =>
    request<TerminalRead>(`${BASE}/terminals/${sessionId}/read`, { query: { wait_seconds: waitSeconds } }),

  resizeTerminal: (sessionId: string, cols: number, rows: number) =>
    request<{ id: string; cols: number; rows: number; resized: boolean }>(
      `${BASE}/terminals/${sessionId}/resize`,
      { method: 'POST', body: { cols, rows } },
    ),

  stopTerminal: (sessionId: string, reason = 'user_stop') =>
    request<{ id: string; state: string; stop_reason: string }>(`${BASE}/terminals/${sessionId}/stop`, {
      method: 'POST',
      body: { reason },
    }),

  // -------------------------------------------------------------------------
  // Git
  // -------------------------------------------------------------------------
  gitStatus: (workspaceId: string) => request<GitStatus>(`${BASE}/workspaces/${workspaceId}/git/status`),

  gitBranches: (workspaceId: string) =>
    request<{ workspace_id: string; current: string; branches: string[] }>(
      `${BASE}/workspaces/${workspaceId}/git/branches`,
    ),

  gitDiff: (workspaceId: string, paths: string[], staged = false) =>
    request<GitDiff>(`${BASE}/workspaces/${workspaceId}/git/diff`, {
      method: 'POST',
      query: { staged: staged ? 'true' : 'false' },
      body: { paths },
    }),

  gitStage: (workspaceId: string, paths: string[]) =>
    request<{ workspace_id: string; staged: string[]; count: number; bulk_stage_forbidden: boolean }>(
      `${BASE}/workspaces/${workspaceId}/git/stage`,
      { method: 'POST', body: { paths } },
    ),

  gitCommit: (workspaceId: string, message: string, paths: string[]) =>
    request<{ workspace_id: string; commit: string; message: string; paths: string[]; count: number }>(
      `${BASE}/workspaces/${workspaceId}/git/commit`,
      { method: 'POST', body: { message, paths } },
    ),

  gitRevertPreview: (workspaceId: string, paths: string[]) =>
    request<{ workspace_id: string; paths: string[]; previews: unknown[] }>(
      `${BASE}/workspaces/${workspaceId}/git/revert-preview`,
      { method: 'POST', body: { paths } },
    ),

  gitRevert: (workspaceId: string, paths: string[]) =>
    request<{ workspace_id: string; paths: string[]; count: number }>(
      `${BASE}/workspaces/${workspaceId}/git/revert`,
      { method: 'POST', body: { paths } },
    ),

  // -------------------------------------------------------------------------
  // Preview
  // -------------------------------------------------------------------------
  startPreview: (
    workspaceId: string,
    body: {
      command: string[];
      target_port?: number | null;
      kind?: string;
      entry_path?: string;
      rel_cwd?: string;
      lease_seconds?: number;
    },
  ) => request<PreviewSession>(`${BASE}/workspaces/${workspaceId}/preview`, { method: 'POST', body }),

  listPreviews: (workspaceId: string) =>
    request<{ items: PreviewSession[]; count: number }>(`${BASE}/workspaces/${workspaceId}/preview`),

  stopPreview: (sessionId: string) =>
    request<{ id: string; state: string }>(`${BASE}/preview/${sessionId}/stop`, { method: 'POST' }),

  // -------------------------------------------------------------------------
  // Preview sources (P1-A 统一预览源注册协议)
  // -------------------------------------------------------------------------
  registerStaticPreviewSource: (workspaceId: string, path: string) =>
    request<PreviewSource>(`${BASE}/workspaces/${workspaceId}/preview-sources`, {
      method: 'POST',
      body: { kind: 'static', path },
    }),

  registerProcessPreviewSource: (
    workspaceId: string,
    body: {
      command: string[];
      target_port?: number | null;
      process_kind?: string;
      entry_path?: string;
      rel_cwd?: string;
      lease_seconds?: number;
    },
  ) =>
    request<PreviewSource>(`${BASE}/workspaces/${workspaceId}/preview-sources`, {
      method: 'POST',
      body: { kind: 'process', ...body },
    }),

  listPreviewSources: (workspaceId: string) =>
    request<{ items: PreviewSource[]; count: number }>(
      `${BASE}/workspaces/${workspaceId}/preview-sources`,
    ),

  previewSourceVersion: (sourceId: string) =>
    request<PreviewSourceVersion>(`${BASE}/preview-sources/${sourceId}/version`),

  unregisterPreviewSource: (sourceId: string) =>
    request<{ id: string; state: string }>(`${BASE}/preview-sources/${sourceId}`, {
      method: 'DELETE',
    }),

  /**
   * Fetch static preview content as a Blob for the sandboxed iframe. Blob URLs
   * keep the auth in our own fetch (explicit 401/404 error states instead of an
   * iframe silently rendering an error page) while the response's
   * `CSP: sandbox allow-scripts` still governs the framed document.
   */
  fetchPreviewSourceContent: async (sourceId: string): Promise<{ blob: Blob; version: string }> => {
    const res = await fetch(buildApiUrl(`${BASE}/preview-sources/${sourceId}/content`), {
      credentials: 'same-origin',
    });
    if (!res.ok) {
      throw new Error(`预览内容加载失败（HTTP ${res.status}）`);
    }
    const version = res.headers.get('x-preview-version') ?? '';
    return { blob: await res.blob(), version };
  },
};
