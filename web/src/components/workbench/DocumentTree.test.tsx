import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DocumentTree } from './DocumentTree';
import type { TreeEntry } from '../../api/workbench';

/**
 * P1-22 文档树图 — DOM assertions against a simulated real directory with
 * more than 10 document nodes: expand/collapse works and clicking a document
 * opens its real content.
 */

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    getTree: vi.fn(),
    readFile: vi.fn(),
  },
}));

import { workbenchApi } from '../../api/workbench';

const WS = 'ws-p122';

function file(name: string, path: string, size = 512): TreeEntry {
  return {
    name,
    path,
    type: 'file',
    size_bytes: size,
    mtime: 0,
    read_only: false,
    encoding: 'utf-8',
    binary: false,
  };
}

function dir(name: string, path: string, hasChildren = true): TreeEntry {
  return {
    name,
    path,
    type: 'dir',
    size_bytes: null,
    mtime: 0,
    read_only: false,
    encoding: null,
    binary: false,
    has_children: hasChildren,
  };
}

// A realistic small project: 12 documents across 3 directories + root files.
const ROOT: TreeEntry[] = [
  dir('docs', 'docs'),
  dir('notes', 'notes'),
  dir('specs', 'specs'),
  file('README.md', 'README.md'),
  file('big.log', 'big.log', 3_145_728),
  file('logo.bin', 'logo.bin'),
];
const DOCS = ['a1', 'a2', 'a3', 'a4'].map((n) => file(`${n}.md`, `docs/${n}.md`));
const NOTES = ['n1', 'n2', 'n3', 'n4'].map((n) => file(`${n}.txt`, `notes/${n}.txt`));
const SPECS = ['s1', 's2', 's3', 's4'].map((n) => file(`${n}.md`, `specs/${n}.md`));

function treeRsp(path: string, entries: TreeEntry[]) {
  return {
    workspace_id: WS,
    path,
    entries,
    total: entries.length,
    offset: 0,
    limit: 500,
    truncated: false,
    blocked_credential_entries: 0,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(workbenchApi.getTree).mockImplementation(async (_ws, p) => {
    const path = p ?? '';
    if (path === '' || path === '.') return treeRsp('', ROOT);
    if (path === 'docs') return treeRsp(path, DOCS);
    if (path === 'notes') return treeRsp(path, NOTES);
    if (path === 'specs') return treeRsp(path, SPECS);
    return treeRsp(path, []);
  });
});

describe('P1-22 文档树图', () => {
  it('渲染真实目录文档 ≥10 个节点（首层目录自动展开）', async () => {
    render(<DocumentTree workspaceId={WS} />);

    const tree = await screen.findByTestId('doc-tree');
    await waitFor(() => {
      expect(workbenchApi.getTree).toHaveBeenCalledWith(WS, 'specs', 1, 0, 500);
    });
    await screen.findByText('s4.md');

    // All 15 document files (3 root + 4×3 nested) are rendered as tree rows;
    // directories render as separate `.dir` rows.
    const docNodes = Array.from(tree.querySelectorAll('li.tree-row.file'));
    const dirNodes = Array.from(tree.querySelectorAll('li.tree-row.dir'));
    expect(docNodes.length).toBeGreaterThanOrEqual(10);
    expect(docNodes.length).toBe(15);
    expect(dirNodes.length).toBe(3);
    // Root-level rows include real files and directories.
    expect(within(tree).getByText('README.md')).toBeInTheDocument();
    expect(within(tree).getByText('docs')).toBeInTheDocument();
  });

  it('目录可折叠与再展开', async () => {
    const user = userEvent.setup();
    render(<DocumentTree workspaceId={WS} />);
    const tree = await screen.findByTestId('doc-tree');
    await screen.findByText('a4.md');

    const docsRow = within(tree).getByText('docs').closest('li')!;
    await user.click(docsRow);
    await waitFor(() => {
      expect(within(tree).queryByText('a1.md')).toBeNull();
    });

    await user.click(docsRow);
    await waitFor(() => {
      expect(within(tree).getByText('a1.md')).toBeInTheDocument();
    });
  });

  it('点击文档节点打开真实内容', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue({
      workspace_id: WS,
      path: 'README.md',
      content: '# Hello P1-22',
      encoding: 'utf-8',
      binary: false,
      editable: true,
      size_bytes: 13,
      sha256: 'x',
      revision: 1,
      read_only: false,
    });

    const user = userEvent.setup();
    render(<DocumentTree workspaceId={WS} />);
    const tree = await screen.findByTestId('doc-tree');
    await screen.findByText('a4.md');

    await user.click(within(tree).getByText('README.md'));

    const preview = await screen.findByTestId('doc-tree-preview');
    expect(preview).toHaveTextContent('# Hello P1-22');
    expect(workbenchApi.readFile).toHaveBeenCalledWith(WS, 'README.md');
  });

  it('二进制文档打开时显示二进制说明而非文本内容', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue({
      workspace_id: WS,
      path: 'logo.bin',
      content: '',
      encoding: 'binary',
      binary: true,
      editable: false,
      size_bytes: 4096,
      sha256: null,
      revision: 1,
      read_only: false,
    });

    const user = userEvent.setup();
    render(<DocumentTree workspaceId={WS} />);
    const tree = await screen.findByTestId('doc-tree');
    await screen.findByText('a4.md');

    await user.click(within(tree).getByText('logo.bin'));

    const preview = await screen.findByTestId('doc-tree-preview');
    expect(preview).toHaveTextContent(/二进制文件，不渲染文本内容/);
  });
});
