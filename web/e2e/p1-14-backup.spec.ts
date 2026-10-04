import { test, expect } from '@playwright/test';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { createHash } from 'crypto';
import { fileURLToPath } from 'url';

/**
 * P1-14 备份回滚 UI — E2E（真实后端 + 真实文件系统）。
 *
 * 验收口径：备份 → 改动 → 回滚 → 内容哈希一致，比对证据归档。
 * 流程：造临时工作区文件 → UI 登录 → 注册工作区 → 打开文件 → 备份（进 /api/stash，
 * metadata 带路径+sha256）→ 编辑器改动并保存 → 从面板回滚（确认弹窗 → 写回）→
 * 断言哈希一致绿标 + 磁盘文件内容复原 → 截图与比对值归档 evidence/p1-14-backup/。
 */

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const EVIDENCE_DIR = path.resolve(__dirname, '../../evidence/p1-14-backup');

const LOCAL_TOKEN = process.env.P1_14_LOCAL_TOKEN ?? 'p1-14-dev-token';

const ORIGINAL = '# P1-14 备份基线\n\n这一行是备份时保存的原始内容。\n';
const MODIFIED = '# P1-14 内容被改动\n\n回滚必须能恢复上一行。\n';

test.beforeAll(() => {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
});

test('备份→改动→回滚→哈希一致', async ({ page }) => {
  test.setTimeout(180_000);

  // ---- 0) 造真实工作区文件 -------------------------------------------------
  const wsDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fy-p1-14-ws-'));
  const relPath = 'p1-14-demo.md';
  fs.writeFileSync(path.join(wsDir, relPath), ORIGINAL, 'utf8');
  const originalHash = createHash('sha256').update(ORIGINAL, 'utf8').digest('hex');
  const projectName = `p1-14-backup-e2e-${Date.now().toString(36)}`;

  // ---- 1) 登录 -------------------------------------------------------------
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/login');
  await page.locator('input[type="password"]').fill(LOCAL_TOKEN);
  await page.getByRole('button', { name: /本地口令直接登录/ }).click();
  await expect(page).toHaveURL(/\/chat/);

  // ---- 2) 进入工作台，注册工作区 --------------------------------------------
  await page.goto('/workbench');
  await expect(page.getByRole('heading', { name: '工程代码工作台' })).toBeVisible();
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill(projectName);
  await page.getByLabel(/授权根目录/).fill(wsDir);
  await page.getByRole('button', { name: '注册', exact: true }).click();
  // 显式选中我的工作区（共享 DB 中可能存在他人工作区）
  const myChip = page.getByRole('button', { name: projectName });
  await expect(myChip).toBeVisible();
  await myChip.click();

  // ---- 3) 打开文件 → 备份 ---------------------------------------------------
  await page.getByText(relPath).first().click();
  const editor = page.getByTestId('code-area');
  await expect(editor).toHaveValue(ORIGINAL);

  await page.getByTestId('backup-btn').click();
  await expect(page.getByTestId('backup-notice')).toContainText('已备份');
  const backupHash = (await page.getByTestId('backup-hash').textContent()) ?? '';
  expect(backupHash).toBe(originalHash); // metadata.sha256 === 内容 sha256
  const recordId = ((await page.getByTestId('last-backup').locator('code').first().textContent()) ?? '').trim();
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '01-backup-done.png'), fullPage: true });

  // ---- 4) 改动并保存（真实写盘） ---------------------------------------------
  await editor.fill(MODIFIED);
  await page.getByRole('button', { name: '保存' }).click();
  await expect(page.getByText(/已保存（新版本/).first()).toBeVisible();
  expect(fs.readFileSync(path.join(wsDir, relPath), 'utf8')).toBe(MODIFIED);
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '02-modified-saved.png'), fullPage: true });

  // ---- 5) 回滚（确认弹窗 → 写回 → 哈希比对） ---------------------------------
  await page.getByRole('button', { name: '回滚', exact: true }).first().click();
  await expect(page.getByTestId('rollback-confirm')).toBeVisible();
  await expect(page.getByTestId('confirm-path')).toHaveText(relPath);
  await page.getByTestId('confirm-rollback-btn').click();

  await expect(page.getByTestId('hash-match')).toBeVisible(); // 绿标
  const verifyBackup = (await page.getByTestId('verify-backup-hash').textContent()) ?? '';
  const verifyCurrent = (await page.getByTestId('verify-current-hash').textContent()) ?? '';
  expect(verifyBackup).toBe(originalHash);
  expect(verifyCurrent).toBe(originalHash);

  // 磁盘文件内容真实复原
  const restored = fs.readFileSync(path.join(wsDir, relPath), 'utf8');
  expect(restored).toBe(ORIGINAL);
  expect(createHash('sha256').update(restored, 'utf8').digest('hex')).toBe(originalHash);
  // 编辑器缓冲区同步刷新（onRolledBack → remount）
  await expect(page.getByTestId('code-area')).toHaveValue(ORIGINAL);
  await page.screenshot({ path: path.join(EVIDENCE_DIR, '03-rollback-hash-match.png'), fullPage: true });

  // ---- 6) 比对证据归档 -------------------------------------------------------
  const evidence = {
    ticket: 'P1-14',
    acceptance: '备份→改动→回滚→内容哈希一致',
    timestamp: new Date().toISOString(),
    workspace_dir: wsDir,
    file: relPath,
    stash_record_id: recordId,
    backup_hash_metadata: backupHash,
    rollback_hash_current: verifyCurrent,
    rollback_hash_backup: verifyBackup,
    hash_match: verifyBackup === verifyCurrent && verifyCurrent === originalHash,
    node_disk_sha256: createHash('sha256').update(restored, 'utf8').digest('hex'),
    original_sha256: originalHash,
    screenshots: ['01-backup-done.png', '02-modified-saved.png', '03-rollback-hash-match.png'],
  };
  fs.writeFileSync(path.join(EVIDENCE_DIR, 'hash-comparison.json'), JSON.stringify(evidence, null, 2));
  expect(evidence.hash_match).toBe(true);
});
