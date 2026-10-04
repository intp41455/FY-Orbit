import { describe, it, expect, vi, beforeEach } from 'vitest';
import { workbenchApi } from './workbench';

// Verify the workbench UI client hits the real backend endpoints the 18 规格
// §A–F requires (file tree, file read/write, terminal, git diff, preview).
// Network is mocked; we only assert URL + method + body shape.
// Typed mock (not `vi.mocked(fetch)`): `fetch` is declared as a plain function
// in lib.dom, so `vi.mocked()` yields no `.mock` and `tsc --noEmit` fails.
const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>();

function mockFetchOnce(body: unknown, status = 200) {
  return fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

describe('workbenchApi endpoint wiring', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    // Avoid leaking a CSRF token from a prior test into headers.
    document.querySelectorAll('meta[name="csrf-token"]').forEach((el) => el.remove());
  });

  it('lists workspaces via GET /api/workbench/workspaces', async () => {
    mockFetchOnce({ items: [{ id: 'w1', project_name: 'p', mode: 'local', authorized_root: 'C:/x', branch: '', data_domain: 'work', state: 'active' }], count: 1 });
    const r = await workbenchApi.listWorkspaces();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/workbench/workspaces');
    expect(r.items[0].id).toBe('w1');
  });

  it('fetches the file tree with path/depth query', async () => {
    mockFetchOnce({ workspace_id: 'w1', path: '', entries: [], total: 0, offset: 0, limit: 200, truncated: false, blocked_credential_entries: 0 });
    await workbenchApi.getTree('w1', '', 1);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/api/workbench/workspaces/w1/tree');
    expect(url).toContain('depth=1');
  });

  it('reads a file with the path query', async () => {
    mockFetchOnce({ workspace_id: 'w1', path: 'a/b.py', content: 'x', encoding: 'utf-8', binary: false, editable: true, size_bytes: 1, sha256: null, revision: 3, read_only: false });
    const fc = await workbenchApi.readFile('w1', 'a/b.py');
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/api/workbench/workspaces/w1/file?path=a%2Fb.py');
    expect(fc.revision).toBe(3);
  });

  it('writes a file as POST with expected_revision body', async () => {
    mockFetchOnce({ rel_path: 'a/b.py', revision: 4, sha256: null, exists: true });
    await workbenchApi.writeFile('w1', 'a/b.py', { content: 'y', expected_revision: 3 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/workbench/workspaces/w1/file?path=a%2Fb.py');
    expect(init!.method).toBe('POST');
    expect(JSON.parse(String(init!.body))).toMatchObject({ content: 'y', expected_revision: 3 });
  });

  it('creates a terminal via POST and reads with wait_seconds', async () => {
    mockFetchOnce({ id: 't1', workspace_id: 'w1', state: 'running', pty_backend: 'conpty', interactive: true });
    await workbenchApi.createTerminal('w1', { cols: 120, rows: 24 });
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/workbench/workspaces/w1/terminals');
    expect(fetchMock.mock.calls[0][1]!.method).toBe('POST');

    mockFetchOnce({ id: 't1', output: 'hi', cursor: 0, new_cursor: 2, state: 'running', exit_code: null, interactive: true });
    await workbenchApi.readTerminal('t1', 0.5);
    const url = String(fetchMock.mock.calls[1][0]);
    expect(url).toContain('/api/workbench/terminals/t1/read');
    expect(url).toContain('wait_seconds=0.5');
  });

  it('writes terminal input as POST with the raw payload', async () => {
    mockFetchOnce({ id: 't1', written: 5 });
    await workbenchApi.writeTerminal('t1', 'echo x\r');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/workbench/terminals/t1/write');
    // Payload must reach the PTY byte-for-byte (line ending included).
    expect(JSON.parse(String(init!.body))).toEqual({ data: 'echo x\r' });
  });

  it('fetches git status and posts a diff for selected paths', async () => {
    mockFetchOnce({ workspace_id: 'w1', branch: 'main', ahead: 0, behind: 0, staged: [], unstaged: [{ path: 'a.py', state: 'M' }], untracked: [], conflicts: [], has_conflicts: false, clean: false });
    const st = await workbenchApi.gitStatus('w1');
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/workbench/workspaces/w1/git/status');
    expect(st.unstaged[0].path).toBe('a.py');

    mockFetchOnce({ workspace_id: 'w1', staged: false, paths: ['a.py'], text: 'diff', truncated: false, additions: 1, deletions: 0 });
    await workbenchApi.gitDiff('w1', ['a.py'], false);
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain('/api/workbench/workspaces/w1/git/diff?staged=false');
    expect(JSON.parse(String(init!.body))).toMatchObject({ paths: ['a.py'] });
  });

  it('starts a preview via POST with a command array', async () => {
    mockFetchOnce({ id: 'p1', workspace_id: 'w1', kind: 'http', entry_path: '/', target_port: 5173, url: 'http://127.0.0.1:5173', state: 'running' });
    await workbenchApi.startPreview('w1', { command: ['npm', 'run', 'dev'] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/workbench/workspaces/w1/preview');
    expect(JSON.parse(String(init!.body))).toMatchObject({ command: ['npm', 'run', 'dev'] });
  });
});
