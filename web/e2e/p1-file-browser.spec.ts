import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

/**
 * P1-09 文件浏览增强 + P1-22 文档树图 — E2E against the REAL backend.
 *
 * Evidence captured (screenshots + DOM assertions):
 *  - P1-09: a >2 MiB file opens with the 截断/懒加载 notice (no raw error);
 *  - P1-09: a binary file opens with the placeholder — no mojibake text node;
 *  - P1-09: hidden dotfiles follow the visibility toggle;
 *  - P1-22: the document tree renders ≥10 real document nodes from a real
 *    directory, supports expand/collapse, and clicking a doc opens its content.
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const SHOT_DIR = path.join('..', 'evidence', 'p1-file-browser-20261003');

let csrfToken = '';

function extractCookie(setCookie: string | undefined, name: string): string | null {
  if (!setCookie) return null;
  for (const part of setCookie.split(/,(?=[^;]+=)/)) {
    const seg = part.trim();
    if (seg.startsWith(`${name}=`)) return seg.slice(name.length + 1).split(';')[0];
  }
  return null;
}

async function loginViaDevToken(page: Page): Promise<void> {
  const res = await page.request.post('/auth/local/dev-token', { data: { token: LOCAL_TOKEN } });
  if (!res.ok()) throw new Error(`dev-token login failed: HTTP ${res.status()} — set E2E_LOCAL_TOKEN`);
  const session = extractCookie(res.headers()['set-cookie'], 'fy_session');
  if (session) {
    await page.context().addCookies([
      { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
  csrfToken = (await res.json()).csrf_token;
}

async function api(page: Page, method: 'GET' | 'POST', pathName: string, body?: unknown) {
  return page.request.fetch(pathName, {
    method,
    data: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
  });
}

/**
 * Real fixture directory: 12 documents across nested dirs, one 3 MiB text
 * file, one binary file and two hidden dotfiles.
 */
async function makeFixtureRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-p1-'));
  await fs.mkdir(path.join(root, 'docs', 'deep'), { recursive: true });
  await fs.mkdir(path.join(root, 'notes'), { recursive: true });
  const docs = [
    ['docs/architecture.md', '# 架构说明\n\n分层架构与模块边界。'],
    ['docs/api-notes.md', '# API 笔记\n\nREST + SSE 契约。'],
    ['docs/deep/child-doc.md', '# 深层文档\n\n嵌套目录里的文档。'],
    ['notes/plan.txt', 'P1 计划：先文件浏览，再树图。'],
    ['notes/diary.txt', '观察日志：一切正常。'],
    ['README.md', '# Find Yourself 工作台\n\n文档树图示例项目。'],
    ['CHANGELOG.md', '## 2026-10-03\n\n- 文件浏览增强\n- 文档树图'],
    ['guide.md', '# 使用指南\n\n点击文档节点打开内容。'],
    ['overview.md', '# 总览\n\n九个顶层文档之一。'],
    ['summary.md', '# 摘要\n\n验收证据来自真实目录。'],
  ] as const;
  for (const [rel, content] of docs) {
    await fs.writeFile(path.join(root, rel), content, 'utf8');
  }
  // > 2 MiB text file — beyond the backend inline-edit limit (2 MiB).
  const bigLine = 'p1-09 large file probe line 0123456789\n';
  await fs.writeFile(path.join(root, 'big.log'), bigLine.repeat(Math.ceil(3 * 1024 * 1024 / bigLine.length)));
  // Binary bytes with NULs — backend reports encoding=binary.
  await fs.writeFile(path.join(root, 'logo.bin'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x01, 0x02, 0x00, 0xff]));
  // Hidden dotfiles (non-credential so the tree API returns them).
  await fs.writeFile(path.join(root, '.hidden-note.txt'), '我是隐藏文件，默认不可见。');
  await fs.writeFile(path.join(root, 'notes/.hidden-in-dir.txt'), '子目录里的隐藏文件。');
  return root;
}

async function openWorkspace(page: Page, root: string, name: string): Promise<void> {
  await page.goto('/workbench');
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill(name);
  await page.getByLabel('授权根目录（绝对路径）').fill(root);
  await page.getByRole('button', { name: '注册', exact: true }).click();
  const chip = page.locator('.workspace-chip', { hasText: name });
  await expect(chip).toBeVisible();
  await chip.click();
  // The real tree API answered when the root files appear.
  await expect(page.locator('.file-tree').getByText('README.md')).toBeVisible();
}

test.describe('P1-09 文件浏览增强（真实后端）', () => {
  let root: string;
  let runSeq = 0;

  test.beforeAll(async () => {
    root = await makeFixtureRoot();
  });

  test.beforeEach(async ({ page }) => {
    runSeq += 1;
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-fs-${Date.now().toString(36)}-${runSeq}`);
  });

  test('大文件打开显示截断/懒加载提示而非原始报错', async ({ page }) => {
    await page.locator('.file-tree').getByText('big.log').click();
    const notice = page.getByTestId('large-file-notice');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(/大文件已停止内联加载/);
    await expect(notice).toContainText(/2 MiB/);
    // The backend's raw English error never leaks to the DOM.
    await expect(page.getByText(/inline-edit limit/)).toHaveCount(0);
    await expect(page.locator('.editor-pane textarea')).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-large-file-notice.png'), fullPage: false });
  });

  test('二进制文件打开显示占位，不出现乱码文本', async ({ page }) => {
    await page.locator('.file-tree').getByText('logo.bin').click();
    const placeholder = page.getByTestId('binary-placeholder');
    await expect(placeholder).toBeVisible();
    await expect(placeholder).toContainText(/二进制文件/);
    await expect(placeholder).toContainText(/为避免乱码/);
    // No text area for binary content — nothing mojibake-able in the DOM.
    await expect(page.locator('.editor-pane textarea')).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-binary-placeholder.png'), fullPage: false });
  });

  test('隐藏文件默认不可见，开关打开后可见', async ({ page }) => {
    const tree = page.locator('.file-tree');
    await expect(tree.getByText('.hidden-note.txt')).toHaveCount(0);
    await expect(tree.getByText('notes')).toBeVisible();

    // Expand notes and confirm the nested dotfile is hidden too.
    await tree.getByText('notes').click();
    await expect(tree.getByText('plan.txt')).toBeVisible();
    await expect(tree.getByText('.hidden-in-dir.txt')).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-hidden-before-toggle.png'), fullPage: false });

    const toggle = page.getByTestId('show-hidden-toggle');
    await toggle.check();
    await expect(tree.getByText('.hidden-note.txt')).toBeVisible();
    await expect(tree.getByText('.hidden-in-dir.txt')).toBeVisible();
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-hidden-after-toggle.png'), fullPage: false });

    // And off again hides them.
    await toggle.uncheck();
    await expect(tree.getByText('.hidden-note.txt')).toHaveCount(0);
  });
});

test.describe('P1-22 文档树图（真实后端）', () => {
  let root: string;
  let runSeq = 0;

  test.beforeAll(async () => {
    root = await makeFixtureRoot();
  });

  test.beforeEach(async ({ page }) => {
    runSeq += 1;
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-tree-${Date.now().toString(36)}-${runSeq}`);
  });

  test('渲染真实目录文档 ≥10 个节点，支持展开/折叠，点击可打开', async ({ page }) => {
    const tree = page.getByTestId('doc-tree');
    await expect(tree).toBeVisible();

    // First-level dirs auto-expand; wait for the nested docs to stream in.
    await expect(tree.getByText('architecture.md')).toBeVisible();

    // ≥10 real document nodes from the real fixture directory.
    const docCount = await tree.locator('li.tree-row.file').count();
    expect(docCount).toBeGreaterThanOrEqual(10);

    // Expand/collapse: collapse docs, then expand again; and drill into the
    // second-level dir to prove lazy loading works below the auto-expand.
    const docsRow = tree.getByText('docs', { exact: true }).first();
    await docsRow.click();
    await expect(tree.getByText('architecture.md')).toHaveCount(0);
    await docsRow.click();
    await expect(tree.getByText('architecture.md')).toBeVisible();
    await tree.getByText('deep').click();
    await expect(tree.getByText('child-doc.md')).toBeVisible();

    // Click a document node → real content opens inline.
    await tree.getByText('README.md').click();
    const preview = page.getByTestId('doc-tree-preview');
    await expect(preview).toBeVisible();
    await expect(preview).toContainText(/Find Yourself 工作台/);

    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-22-doc-tree-open.png'), fullPage: false });
  });

  test('树图中二进制文档打开显示二进制说明', async ({ page }) => {
    const tree = page.getByTestId('doc-tree');
    await expect(tree.getByText('architecture.md')).toBeVisible();
    await tree.getByText('logo.bin').click();
    const preview = page.getByTestId('doc-tree-preview');
    await expect(preview).toContainText(/二进制文件，不渲染文本内容/);
  });
});
