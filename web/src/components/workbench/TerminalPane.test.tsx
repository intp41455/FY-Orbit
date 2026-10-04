import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TerminalPane } from './TerminalPane';

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    listTerminals: vi.fn(),
    describeTerminal: vi.fn(),
    createTerminal: vi.fn(),
    readTerminal: vi.fn(),
    writeTerminal: vi.fn(),
    stopTerminal: vi.fn(),
  },
}));

import { workbenchApi } from '../../api/workbench';

const WS = 'ws-1';
const KEY = `fy.terminal.${WS}`;

function runningSession(id: string) {
  return {
    id, workspace_id: WS, actor_identity: 'owner/o', shell: 'cmd.exe', pid: 4242,
    cols: 120, rows: 24, state: 'running', pty_backend: 'conpty', interactive: true,
    presentation: 'interactive_pty', exit_code: null, stop_reason: null,
    timeout_seconds: 300, cursor: 0, created_at: '2026-10-02T00:00:00Z', ended_at: null,
  };
}

describe('TerminalPane 刷新重连', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    vi.mocked(workbenchApi.readTerminal).mockResolvedValue({
      id: 'term-1', output: 'C:\\>', cursor: 0, new_cursor: 4,
      state: 'running', exit_code: null, interactive: true,
    });
  });

  afterEach(() => { window.localStorage.clear(); });

  it('reattaches to a still-running server session instead of stranding it', async () => {
    window.localStorage.setItem(KEY, 'term-1');
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({
      items: [runningSession('term-1')], count: 1,
    });
    vi.mocked(workbenchApi.describeTerminal).mockResolvedValue(runningSession('term-1'));

    render(<TerminalPane workspaceId={WS} />);

    await waitFor(() => {
      expect(workbenchApi.describeTerminal).toHaveBeenCalledWith('term-1');
    });
    // No second PTY was spawned: the existing session was reused.
    expect(workbenchApi.createTerminal).not.toHaveBeenCalled();
    expect(await screen.findByText(/已重连到既有会话 term-1/)).toBeInTheDocument();
  });

  it('replays the missed buffer through the polling loop', async () => {
    window.localStorage.setItem(KEY, 'term-1');
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({
      items: [runningSession('term-1')], count: 1,
    });
    vi.mocked(workbenchApi.describeTerminal).mockResolvedValue(runningSession('term-1'));

    render(<TerminalPane workspaceId={WS} />);
    await waitFor(() => {
      expect(workbenchApi.readTerminal).toHaveBeenCalled();
    });
    expect(await screen.findByText(/C:\\>/)).toBeInTheDocument();
  });

  it('forgets a stale id and offers a fresh start when nothing survived', async () => {
    window.localStorage.setItem(KEY, 'term-gone');
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({ items: [], count: 0 });

    render(<TerminalPane workspaceId={WS} />);

    expect(await screen.findByText(/未找到可重连的会话/)).toBeInTheDocument();
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(screen.getByRole('button', { name: '启动终端' })).toBeInTheDocument();
  });

  it('starts a new session when asked', async () => {
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({ items: [], count: 0 });
    vi.mocked(workbenchApi.createTerminal).mockResolvedValue(runningSession('term-new'));

    render(<TerminalPane workspaceId={WS} />);
    await screen.findByText(/未找到可重连的会话/);
    await userEvent.setup().click(screen.getByRole('button', { name: '启动终端' }));

    await waitFor(() => {
      expect(workbenchApi.createTerminal).toHaveBeenCalledWith(WS, { cols: 120, rows: 24 });
    });
    expect(window.localStorage.getItem(KEY)).toBe('term-new');
  });

  it('sends CR on Enter so the ConPTY actually executes the command', async () => {
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({
      items: [runningSession('term-1')], count: 1,
    });
    vi.mocked(workbenchApi.describeTerminal).mockResolvedValue(runningSession('term-1'));

    render(<TerminalPane workspaceId={WS} />);
    await waitFor(() => {
      expect(workbenchApi.readTerminal).toHaveBeenCalled();
    });

    const input = await screen.findByLabelText('终端输入');
    await userEvent.type(input, 'echo hi{Enter}');
    await waitFor(() => {
      expect(workbenchApi.writeTerminal).toHaveBeenCalledWith('term-1', 'echo hi\r');
    });
  });

  it('stops the PTY and offers a fresh start instead of stranding the pane', async () => {
    window.localStorage.setItem(KEY, 'term-1');
    vi.mocked(workbenchApi.listTerminals).mockResolvedValue({
      items: [runningSession('term-1')], count: 1,
    });
    vi.mocked(workbenchApi.describeTerminal).mockResolvedValue(runningSession('term-1'));
    vi.mocked(workbenchApi.stopTerminal).mockResolvedValue({
      id: 'term-1', state: 'stopped', stop_reason: 'requested',
    });
    vi.mocked(workbenchApi.createTerminal).mockResolvedValue(runningSession('term-2'));

    render(<TerminalPane workspaceId={WS} />);
    const input = await screen.findByLabelText('终端输入');
    expect(input).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', { name: '停止' }));

    // The real process is released, the id is forgotten, and a dead session
    // cannot be "stopped" again or typed into.
    await waitFor(() => {
      expect(workbenchApi.stopTerminal).toHaveBeenCalledWith('term-1');
    });
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(screen.queryByLabelText('终端输入')).not.toBeInTheDocument();
    expect(await screen.findByText(/会话已停止/)).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', { name: '启动终端' }));
    await waitFor(() => {
      expect(workbenchApi.createTerminal).toHaveBeenCalledWith(WS, { cols: 120, rows: 24 });
    });
  });
});