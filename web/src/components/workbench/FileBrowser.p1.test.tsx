import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CodeEditor } from './CodeEditor';
import { FileTree } from './FileTree';

/**
 * P1-09 文件浏览增强 — DOM assertions for the three real-open behaviors:
 *   1. oversized files (>2 MiB) show a truncation / lazy-load notice;
 *   2. binary files render a dedicated placeholder, never mojibake text;
 *   3. hidden dotfiles obey the visibility toggle.
 */

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    getTree: vi.fn(),
    readFile: vi.fn(),
  },
}));

import { workbenchApi, type FileContent, type TreeEntry } from '../../api/workbench';

const WS = 'ws-p109';

function textFile(over: Partial<FileContent> = {}): FileContent {
  return {
    workspace_id: WS,
    path: 'notes/a.txt',
    content: 'hello world',
    encoding: 'utf-8',
    binary: false,
    editable: true,
    size_bytes: 11,
    sha256: 'abc',
    revision: 1,
    read_only: false,
    ...over,
  };
}

function entry(over: Partial<TreeEntry> & { name: string; path: string }): TreeEntry {
  return {
    type: 'file',
    size_bytes: 10,
    mtime: 0,
    read_only: false,
    encoding: 'utf-8',
    binary: false,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe('P1-09 大文件：截断/懒加载提示', () => {
  it('打开超过 2 MiB 限制的文件时展示大文件提示而非原始报错', async () => {
    vi.mocked(workbenchApi.readFile).mockRejectedValue(
      Object.assign(new Error('File exceeds the 2097152 byte inline-edit limit; use the terminal or download instead'), {
        status: 422,
      }),
    );

    render(<CodeEditor workspaceId={WS} path="logs/big.log" />);

    const notice = await screen.findByTestId('large-file-notice');
    expect(notice).toBeVisible();
    expect(notice).toHaveTextContent(/大文件已停止内联加载/);
    expect(notice).toHaveTextContent(/2 MiB/);
    // The raw English error string must not leak into the DOM.
    expect(screen.queryByText(/inline-edit limit/)).toBeNull();
    // No text area is offered for a file we refused to inline-load.
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('普通文本文件不出现大文件提示', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue(textFile());
    render(<CodeEditor workspaceId={WS} path="notes/a.txt" />);
    await screen.findByRole('textbox');
    expect(screen.queryByTestId('large-file-notice')).toBeNull();
    expect(screen.queryByTestId('binary-placeholder')).toBeNull();
  });
});

describe('P1-09 二进制：占位显示、不乱码', () => {
  it('二进制文件渲染专用占位并隐藏文本域', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue(
      textFile({
        path: 'assets/logo.bin',
        content: '',
        encoding: 'binary',
        binary: true,
        editable: false,
        size_bytes: 4096,
      }),
    );

    render(<CodeEditor workspaceId={WS} path="assets/logo.bin" />);

    const placeholder = await screen.findByTestId('binary-placeholder');
    expect(placeholder).toBeVisible();
    expect(placeholder).toHaveTextContent(/二进制文件/);
    expect(placeholder).toHaveTextContent(/为避免乱码/);
    // No editable text area: binary bytes never leak into the DOM as text.
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});

describe('P1-09 隐藏文件可见性开关', () => {
  const entries: TreeEntry[] = [
    entry({ name: 'README.md', path: 'README.md' }),
    entry({ name: '.hidden-note.txt', path: '.hidden-note.txt' }),
    entry({ name: '.env.example', path: '.env.example' }),
    entry({ name: 'docs', path: 'docs', type: 'dir', size_bytes: null, has_children: true }),
  ];

  function treeResponse(list: TreeEntry[]) {
    return {
      workspace_id: WS,
      path: '',
      entries: list,
      total: list.length,
      offset: 0,
      limit: 200,
      truncated: false,
      blocked_credential_entries: 0,
    };
  }

  it('默认隐藏 dotfile，打开开关后可见（持久化到 localStorage）', async () => {
    const user = userEvent.setup();
    vi.mocked(workbenchApi.getTree).mockResolvedValue(treeResponse(entries));

    render(<FileTree workspaceId={WS} selectedPath={null} onSelectFile={() => {}} />);

    await screen.findByText('README.md');
    expect(screen.queryByText('.hidden-note.txt')).toBeNull();
    expect(screen.queryByText('.env.example')).toBeNull();
    // The real directory still shows its non-hidden entries.
    expect(screen.getByText('docs')).toBeInTheDocument();

    const toggle = screen.getByTestId('show-hidden-toggle') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    await user.click(toggle);

    await waitFor(() => {
      expect(screen.getByText('.hidden-note.txt')).toBeInTheDocument();
    });
    expect(screen.getByText('.env.example')).toBeInTheDocument();
    expect(toggle.checked).toBe(true);
    expect(window.localStorage.getItem('fy.filetree.showHidden')).toBe('1');

    // Toggle back off hides them again.
    await user.click(toggle);
    await waitFor(() => {
      expect(screen.queryByText('.hidden-note.txt')).toBeNull();
    });
  });
});
