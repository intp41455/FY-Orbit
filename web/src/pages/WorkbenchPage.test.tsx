import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WorkbenchPage } from './WorkbenchPage';

// The heavy children are stubbed: this file is only about WHICH workspace the
// page selects, which is what decides whether a refresh can reattach the PTY.
vi.mock('../components/workbench/FileTree', () => ({
  FileTree: ({ workspaceId }: { workspaceId: string }) => (
    <div data-testid="file-tree">{workspaceId}</div>
  ),
}));
vi.mock('../components/workbench/CodeEditor', () => ({
  CodeEditor: () => <div data-testid="code-editor" />,
}));
vi.mock('../components/workbench/GitPanel', () => ({
  GitPanel: () => <div data-testid="git-panel" />,
}));
vi.mock('../components/workbench/TerminalPane', () => ({
  TerminalPane: ({ workspaceId }: { workspaceId: string }) => (
    <div data-testid="terminal-pane">{workspaceId}</div>
  ),
}));
// P1-A 实时预览窗：重型子组件按本文件口径打桩（本文件只关注工作区选择）。
vi.mock('../components/workbench/PreviewPanel', () => ({
  PreviewPanel: () => <div data-testid="preview-panel" />,
}));

vi.mock('../api/workbench', () => ({
  workbenchApi: { listWorkspaces: vi.fn() },
}));
vi.mock('../api/tasks', () => ({
  tasksApi: { create: vi.fn(), get: vi.fn(), cancel: vi.fn() },
}));
vi.mock('../api/sse', () => ({
  openTaskEventStream: vi.fn(() => ({ close: vi.fn() })),
}));

import { workbenchApi } from '../api/workbench';

const KEY = 'fy.workbench.workspace';

function ws(id: string, name: string) {
  return {
    id, project_name: name, mode: 'local', authorized_root: 'C:\\tmp\\x',
    branch: '', data_domain: 'work', state: 'active',
  };
}

describe('WorkbenchPage 工作区选择', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  afterEach(() => { window.localStorage.clear(); });

  it('refreshes back into the remembered workspace, not the first one', async () => {
    // The very bug: the page used to fall back to items[0], so a refresh moved
    // the terminal pane to an unrelated workspace and the live PTY looked lost.
    window.localStorage.setItem(KEY, 'ws-2');
    vi.mocked(workbenchApi.listWorkspaces).mockResolvedValue({
      items: [ws('ws-1', 'concurrency-ws'), ws('ws-2', 'e2e-term-abc')],
      count: 2,
    });

    render(<WorkbenchPage />);

    await waitFor(() => {
      expect(screen.getByTestId('terminal-pane')).toHaveTextContent('ws-2');
    });
    expect(screen.getByTestId('file-tree')).toHaveTextContent('ws-2');
    expect(screen.getByTestId('git-panel')).toBeInTheDocument();
  });

  it('remembers the workspace the user picks', async () => {
    vi.mocked(workbenchApi.listWorkspaces).mockResolvedValue({
      items: [ws('ws-1', 'concurrency-ws'), ws('ws-2', 'e2e-term-abc')],
      count: 2,
    });

    render(<WorkbenchPage />);
    await waitFor(() => expect(screen.getByTestId('terminal-pane')).toHaveTextContent('ws-1'));

    await userEvent.setup().click(screen.getByRole('button', { name: /e2e-term-abc/ }));

    expect(window.localStorage.getItem(KEY)).toBe('ws-2');
    expect(screen.getByTestId('terminal-pane')).toHaveTextContent('ws-2');
  });

  it('falls back to the first workspace when the remembered one is gone', async () => {
    window.localStorage.setItem(KEY, 'ws-deleted');
    vi.mocked(workbenchApi.listWorkspaces).mockResolvedValue({
      items: [ws('ws-1', 'concurrency-ws')], count: 1,
    });

    render(<WorkbenchPage />);

    await waitFor(() => {
      expect(screen.getByTestId('terminal-pane')).toHaveTextContent('ws-1');
    });
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
});
