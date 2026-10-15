import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
// P1-A 实时预览窗：重型子组件按本文件口径打桩（本文件只关注工作区选择）。
vi.mock('../components/workbench/PreviewPanel', () => ({
  PreviewPanel: () => <div data-testid="preview-panel" />,
}));
vi.mock('../components/workbench/PreviewPane', () => ({
  PreviewPane: () => <div data-testid="preview-pane" />,
}));
vi.mock('../components/workbench/DispatchDialog', () => ({
  DispatchDialog: () => null,
}));
vi.mock('../components/workbench/WorkbenchDispatchDialog', () => ({
  WorkbenchDispatchDialog: () => null,
}));
vi.mock('../components/workbench/DocumentTree', () => ({
  DocumentTree: () => <div data-testid="document-tree" />,
}));
vi.mock('../components/workbench/CommitGraph', () => ({
  CommitGraph: () => <div data-testid="commit-graph" />,
}));
vi.mock('../components/workbench/DiffView', () => ({
  DiffView: () => <div data-testid="diff-view" />,
}));
vi.mock('../components/workbench/OutlinePanel', () => ({
  OutlinePanel: () => null,
}));
vi.mock('../components/workbench/BackupRestorePanel', () => ({
  BackupRestorePanel: () => <div data-testid="backup-panel" />,
}));
vi.mock('../components/workbench/TerminalDock', () => ({
  TerminalDock: ({ workspaceId }: { workspaceId: string }) => (
    <div data-testid="terminal-pane">{workspaceId}</div>
  ),
}));
vi.mock('../components/workbench/CommandPalette', () => ({
  CommandPalette: () => null,
}));
vi.mock('../components/workbench/VerifyStatusCard', () => ({
  VerifyStatusCard: () => <div data-testid="verify-card" />,
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

    render(<MemoryRouter><WorkbenchPage /></MemoryRouter>);

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

    render(<MemoryRouter><WorkbenchPage /></MemoryRouter>);
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

    render(<MemoryRouter><WorkbenchPage /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByTestId('terminal-pane')).toHaveTextContent('ws-1');
    });
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
});

describe('WorkbenchPage 中区折叠', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    // 给一个工作区：中区 Git 区才会挂载真正的 GitPanel（空工作区时是占位文案）。
    vi.mocked(workbenchApi.listWorkspaces).mockResolvedValue({
      items: [ws('ws-1', 'toggle-ws')], count: 1,
    });
  });

  afterEach(() => { window.localStorage.clear(); });

  it('中区 差异审查 / Git 工作区标题栏可折叠展开（折叠开关不是摆设）', async () => {
    const { container } = render(<MemoryRouter><WorkbenchPage /></MemoryRouter>);
    await screen.findAllByTestId('diff-view');

    // 中区三个 WbSection 的 data-index 为 1/2/3（底部四区段是另一套，
    // data-index 1..4 且同样含一个差异审查——用是否含 git-panel 区分 Git 区，
    // 用“第二个 diff-view 所在区”区分中区差异审查）。
    const middleDiffSection = [...container.querySelectorAll('section[data-index="2"]')]
      .find((s) => s.querySelector('[data-testid="diff-view"]'));
    const middleGitSection = [...container.querySelectorAll('section[data-index="3"]')]
      .find((s) => s.querySelector('[data-testid="git-panel"]'));
    expect(middleDiffSection).toBeTruthy();
    expect(middleGitSection).toBeTruthy();

    for (const section of [middleDiffSection!, middleGitSection!]) {
      const header = section.querySelector('button.wb-sec-bar');
      expect(header).not.toBeNull();
      // 初始展开。
      expect(header!).toHaveAttribute('aria-expanded', 'true');
      // 点一次折叠。
      await userEvent.setup().click(header as Element);
      expect(header!).toHaveAttribute('aria-expanded', 'false');
      expect(section.className).toMatch(/is-collapsed/);
      // 再点一次展开。
      await userEvent.setup().click(header as Element);
      expect(header!).toHaveAttribute('aria-expanded', 'true');
      expect(section.className).not.toMatch(/is-collapsed/);
    }
  });
});
