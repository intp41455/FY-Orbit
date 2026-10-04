import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BackupRestorePanel } from './BackupRestorePanel';

/**
 * P1-14 备份回滚 — 单元测试（mock API 层，验证纯前端逻辑）：
 *  - 备份：读文件 → 暂存（title 带文件名、metadata 带路径+sha256）→ 刷新列表；
 *  - 回滚：确认弹窗 → writeFile（现有保存链路，带 expected_revision）→
 *          重读并哈希比对（一致绿标/不一致红标）→ onRolledBack；
 *  - 列表：分页 / 单条删除 / 双击确认清空。
 */

vi.mock('../../api/workbench', () => ({
  workbenchApi: {
    readFile: vi.fn(),
    writeFile: vi.fn(),
  },
}));
vi.mock('../../api/stash', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/stash')>();
  return {
    ...actual,
    stashApi: {
      stage: vi.fn(),
      list: vi.fn(),
      read: vi.fn(),
      remove: vi.fn(),
      clear: vi.fn(),
    },
  };
});

import { workbenchApi } from '../../api/workbench';
import { stashApi } from '../../api/stash';

const WS = 'ws-p114';
const PATH = 'notes/demo.md';

const ORIGINAL = '# 备份基线\n\n第一版内容。\n';
// 预先用 Node crypto 计算的 UTF-8 sha256（避免测试依赖 @types/node）。
const ORIGINAL_HASH = '508d79da66d094cf0a68e4b6826a775ea0104eee882eeecf8823972f225b94a2';
const MODIFIED = '# 被改坏的内容\n';
const MODIFIED_HASH = '0ea35f4bbf4e9befe5ee997f6963ba8309d4b6455f282e66640eee81ea40b137';

function fileContent(content: string, revision: number) {
  return {
    workspace_id: WS,
    path: PATH,
    content,
    encoding: 'utf-8',
    binary: false,
    editable: true,
    size_bytes: content.length,
    sha256: null,
    revision,
    read_only: false,
  };
}

function stashRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ws_rec_1',
    title: 'demo.md · 2026-10-03 10:00:00',
    content: ORIGINAL,
    content_type: 'text/plain',
    metadata: { path: PATH, workspace_id: WS, sha256: ORIGINAL_HASH, size_bytes: ORIGINAL.length, revision: 1 },
    created_at: '2026-10-03T10:00:00.000Z',
    updated_at: '2026-10-03T10:00:00.000Z',
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(stashApi.list).mockResolvedValue({ records: [], count: 0 });
});

describe('备份当前文件', () => {
  it('暂存 title 带文件名+时间戳，metadata 带路径与内容 sha256', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue(fileContent(ORIGINAL, 3));
    vi.mocked(stashApi.stage).mockResolvedValue({ status: 'ok', record: stashRecord() });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);

    await user.click(screen.getByTestId('backup-btn'));

    await waitFor(() => expect(stashApi.stage).toHaveBeenCalledTimes(1));
    const body = vi.mocked(stashApi.stage).mock.calls[0][0];
    expect(body.content).toBe(ORIGINAL);
    expect(body.title).toMatch(/^demo\.md · \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
    expect(body.metadata).toMatchObject({ path: PATH, workspace_id: WS, sha256: ORIGINAL_HASH });
    expect(screen.getByTestId('backup-hash')).toHaveTextContent(ORIGINAL_HASH);
    // 备份成功后刷新列表
    await waitFor(() => expect(stashApi.list).toHaveBeenCalled());
  });

  it('二进制文件拒绝备份', async () => {
    vi.mocked(workbenchApi.readFile).mockResolvedValue({ ...fileContent('', 1), binary: true, editable: false, read_only: true });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);
    await user.click(screen.getByTestId('backup-btn'));
    await screen.findByText('二进制文件不支持文本备份。');
    expect(stashApi.stage).not.toHaveBeenCalled();
  });
});

describe('回滚与哈希一致性', () => {
  it('确认回滚：走保存链路写回 → 哈希一致显示绿标并回调 onRolledBack', async () => {
    vi.mocked(workbenchApi.readFile)
      // 回滚前读 revision
      .mockResolvedValueOnce(fileContent(MODIFIED, 5))
      // 回滚后重读
      .mockResolvedValueOnce(fileContent(ORIGINAL, 6));
    vi.mocked(workbenchApi.writeFile).mockResolvedValue({ rel_path: PATH, revision: 6, sha256: ORIGINAL_HASH, exists: true });
    vi.mocked(stashApi.list).mockResolvedValue({ records: [stashRecord()], count: 1 });
    const onRolledBack = vi.fn();
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} onRolledBack={onRolledBack} />);

    const row = await screen.findByTestId('stash-row');
    expect(row).toBeInTheDocument();
    await user.click(screen.getByTestId('rollback-btn-ws_rec_1'));

    // 确认弹窗展示目标路径
    expect(screen.getByTestId('confirm-path')).toHaveTextContent(PATH);
    await user.click(screen.getByTestId('confirm-rollback-btn'));

    await waitFor(() => expect(screen.getByTestId('hash-match')).toBeInTheDocument());
    // 写回调用与编辑器保存同链路：content + expected_revision
    expect(workbenchApi.writeFile).toHaveBeenCalledWith(WS, PATH, { content: ORIGINAL, expected_revision: 5 });
    // 比对值展示
    expect(screen.getByTestId('verify-backup-hash')).toHaveTextContent(ORIGINAL_HASH);
    expect(screen.getByTestId('verify-current-hash')).toHaveTextContent(ORIGINAL_HASH);
    expect(screen.getByTestId('backup-notice')).toHaveTextContent(/哈希与备份一致/);
    expect(onRolledBack).toHaveBeenCalledTimes(1);
  });

  it('写盘后内容与备份哈希不一致时显示红标', async () => {
    vi.mocked(workbenchApi.readFile)
      .mockResolvedValueOnce(fileContent(MODIFIED, 5))
      .mockResolvedValueOnce(fileContent('磁盘上被并发改掉的', 7));
    vi.mocked(workbenchApi.writeFile).mockResolvedValue({ rel_path: PATH, revision: 7, sha256: MODIFIED_HASH, exists: true });
    vi.mocked(stashApi.list).mockResolvedValue({ records: [stashRecord()], count: 1 });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);

    await user.click(await screen.findByTestId('rollback-btn-ws_rec_1'));
    await user.click(screen.getByTestId('confirm-rollback-btn'));

    await waitFor(() => expect(screen.getByTestId('hash-mismatch')).toBeInTheDocument());
    expect(screen.getByTestId('verify-current-hash')).not.toHaveTextContent(ORIGINAL_HASH);
    expect(screen.getByTestId('backup-notice')).toHaveTextContent(/哈希不一致/);
  });

  it('metadata 缺哈希时退化为与记录内容直接比对', async () => {
    vi.mocked(workbenchApi.readFile)
      .mockResolvedValueOnce(fileContent(MODIFIED, 2))
      .mockResolvedValueOnce(fileContent(ORIGINAL, 3));
    vi.mocked(workbenchApi.writeFile).mockResolvedValue({ rel_path: PATH, revision: 3, sha256: ORIGINAL_HASH, exists: true });
    vi.mocked(stashApi.list).mockResolvedValue({
      records: [stashRecord({ metadata: { path: PATH, workspace_id: WS } })],
      count: 1,
    });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);

    await user.click(await screen.findByTestId('rollback-btn-ws_rec_1'));
    await user.click(screen.getByTestId('confirm-rollback-btn'));

    await waitFor(() => expect(screen.getByTestId('hash-match')).toBeInTheDocument());
    expect(screen.getByTestId('verify-backup-hash')).toHaveTextContent('（未记录）');
    expect(screen.getByText(/比对来源：记录内容/)).toBeInTheDocument();
  });

  it('取消弹窗不写盘', async () => {
    vi.mocked(stashApi.list).mockResolvedValue({ records: [stashRecord()], count: 1 });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);
    await user.click(await screen.findByTestId('rollback-btn-ws_rec_1'));
    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByTestId('rollback-confirm')).toBeNull();
    expect(workbenchApi.writeFile).not.toHaveBeenCalled();
  });
});

describe('列表分页 / 删除 / 清空', () => {
  it('超过每页 8 条时翻页', async () => {
    const many = Array.from({ length: 17 }, (_, i) => stashRecord({ id: `ws_rec_${i}`, title: `r${i}` }));
    vi.mocked(stashApi.list).mockResolvedValue({ records: many, count: many.length });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);

    expect(await screen.findAllByTestId('stash-row')).toHaveLength(8);
    expect(screen.getByText(/第 1\/3 页 · 共 17 条/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getAllByTestId('stash-row')).toHaveLength(8);
    expect(screen.getByText('r8')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getAllByTestId('stash-row')).toHaveLength(1);
  });

  it('只看当前文件开关过滤无关路径记录', async () => {
    const others = [stashRecord({ id: 'r1', metadata: { path: 'other/else.txt' } })];
    vi.mocked(stashApi.list).mockResolvedValue({ records: others, count: 1 });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);
    await screen.findByTestId('stash-list');
    expect(screen.getByText(/暂无暂存记录/)).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox'));
    expect(screen.getByText(/other\/else\.txt/)).toBeInTheDocument();
  });

  it('单条删除调用 delete API', async () => {
    vi.mocked(stashApi.list).mockResolvedValue({ records: [stashRecord()], count: 1 });
    vi.mocked(stashApi.remove).mockResolvedValue({ status: 'ok', deleted: 'ws_rec_1' });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);
    await user.click((await screen.findAllByRole('button', { name: '删除' }))[0]);
    await waitFor(() => expect(stashApi.remove).toHaveBeenCalledWith('ws_rec_1'));
  });

  it('清空需要二次确认', async () => {
    vi.mocked(stashApi.clear).mockResolvedValue({ status: 'ok', deleted: 3 });
    const user = userEvent.setup();
    render(<BackupRestorePanel workspaceId={WS} path={PATH} />);
    const btn = screen.getByTestId('clear-stash-btn');
    await user.click(btn);
    expect(stashApi.clear).not.toHaveBeenCalled(); // 第一次仅布防
    expect(btn).toHaveTextContent('再次点击确认清空');
    await user.click(btn);
    await waitFor(() => expect(stashApi.clear).toHaveBeenCalledTimes(1));
  });
});

describe('占位与目标缺失', () => {
  it('未选工作区/文件时显示占位', () => {
    render(<BackupRestorePanel workspaceId={null} path={null} />);
    expect(screen.getByText(/注册并选择工作区/)).toBeInTheDocument();
    expect(screen.queryByTestId('backup-btn')).toBeNull();
  });
});
