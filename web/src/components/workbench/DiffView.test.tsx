import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DiffView } from './DiffView';

vi.mock('../../api/gitRepo', () => ({
  gitRepoApi: {
    listCommits: vi.fn(),
    getDiff: vi.fn(),
  },
}));

import { gitRepoApi } from '../../api/gitRepo';

const COMMITS = {
  workspace: 'diff-demo',
  count: 2,
  commits: [
    { sha: 'aaaa2222bbbb3333cccc4444dddd5555eeee6666', parents: ['a'], author: 'u', date: '2026-10-03T00:00:00Z', message: 'commit 2' },
    { sha: 'aaaa1111bbbb2222cccc3333dddd4444eeee5555', parents: [], author: 'u', date: '2026-10-02T00:00:00Z', message: 'commit 1' },
  ],
};

const DIFF_TEXT = [
  'diff --git a/notes.txt b/notes.txt',
  'index 1111111..2222222 100644',
  '--- a/notes.txt',
  '+++ b/notes.txt',
  '@@ -1,3 +1,4 @@',
  ' alpha',
  '-bravo',
  '+bravo-changed',
  '+delta',
  ' charlie',
].join('\n');

describe('DiffView 组件渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(gitRepoApi.listCommits).mockResolvedValue(COMMITS);
    vi.mocked(gitRepoApi.getDiff).mockResolvedValue({
      workspace: 'diff-demo', from: 'aaaa1111', to: 'aaaa2222',
      context: 3, text: DIFF_TEXT, truncated: false,
    });
  });

  it('加载提交历史 → 选择两次 commit → 渲染文件分组与配色行', async () => {
    const user = userEvent.setup();
    render(<DiffView />);

    await user.type(screen.getByTestId('diff-workspace-input'), 'diff-demo');
    await user.click(screen.getByRole('button', { name: '加载提交历史' }));
    await waitFor(() => expect(screen.getByTestId('diff-from')).toBeVisible());

    // 选择 commit1 → commit2
    await user.selectOptions(screen.getByTestId('diff-from'), 'aaaa1111bbbb2222cccc3333dddd4444eeee5555');
    await user.selectOptions(screen.getByTestId('diff-to'), 'aaaa2222bbbb3333cccc4444dddd5555eeee6666');
    await user.click(screen.getByRole('button', { name: '生成 diff 图' }));

    await waitFor(() => expect(screen.getByTestId('diff-summary')).toBeVisible());
    expect(screen.getByTestId('diff-summary')).toHaveTextContent(/1 个文件 · \+2 \/ -1/);

    // 文件分组头
    expect(screen.getByTestId('diff-file')).toHaveTextContent('notes.txt');
    // 行级配色：新增绿 / 删除红 / 成对修改琥珀标记
    const addRow = screen.getByText('bravo-changed').closest('.diff-row');
    expect(addRow).toHaveClass('diff-add');
    expect(addRow).toHaveClass('diff-mod');
    const delRow = screen.getByText('bravo').closest('.diff-row');
    expect(delRow).toHaveClass('diff-del');
    expect(delRow).toHaveClass('diff-mod');
    const extraRow = screen.getByText('delta').closest('.diff-row');
    expect(extraRow).toHaveClass('diff-add');
    expect(extraRow).not.toHaveClass('diff-mod');
    // hunk 头
    expect(screen.getByText('@@ -1,3 +1,4 @@')).toBeVisible();
    // 渲染行与 git diff 体逐行对应（sign 前缀 + 内容）
    const rows = screen.getAllByTestId('diff-row').map((r) => {
      const sign = r.querySelector('.diff-sign')?.textContent ?? '';
      return (sign || ' ') + (r.querySelector('.diff-text')?.textContent ?? '');
    });
    expect(rows).toEqual([' alpha', '-bravo', '+bravo-changed', '+delta', ' charlie']);
  });

  it('接口报错时展示错误提示', async () => {
    const user = userEvent.setup();
    vi.mocked(gitRepoApi.listCommits).mockRejectedValue(new Error('工作区不存在'));
    render(<DiffView />);
    await user.type(screen.getByTestId('diff-workspace-input'), 'nope');
    await user.click(screen.getByRole('button', { name: '加载提交历史' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('工作区不存在'));
  });
});
