import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/**
 * P1-11 diff 可视化 — E2E against the REAL backend (port 8093 / preview 5191).
 *
 * 验收口径：对两次 commit 生成 diff 图，与 git diff 输出对照一致。
 * - 通过 API 真实创建 git 工作区并做两次 commit；
 * - UI 上加载提交历史 → 选择两个 commit → 渲染 diff 图；
 * - DOM 渲染行与 shell 侧子进程 `git diff` 归档原文（e2e/fixtures/p1-11/）
 *   做 hunk 头 + hunk 体逐行对照；
 * - 截图归档 evidence/p1-11-diff-20261003/。
 *
 * 注：本机策略禁止 node spawn 子进程，`git diff` 原文由 shell 侧预先执行归档，
 * 其 hunk 头/hunk 体由 commit 内容确定性决定，与本仓库 shas 无关。
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const SHOT_DIR = path.join('..', 'evidence', 'p1-11-diff-20261003');
const FIXTURE = path.resolve('e2e', 'fixtures', 'p1-11', 'two-commit.diff');

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

/** 从归档的 git diff 原文提取 @@ 头与 hunk 体行（kind 前缀 + 内容） */
function extractFromRaw(raw: string): { hunkHeaders: string[]; bodyLines: string[] } {
  const hunkHeaders: string[] = [];
  const bodyLines: string[] = [];
  let inHunk = false;
  for (const line of raw.split('\n')) {
    if (line.startsWith('@@')) {
      hunkHeaders.push(line);
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    const ch = line.charAt(0);
    if (ch === '+' || ch === '-' || ch === ' ' || ch === '\\') {
      bodyLines.push(ch + line.slice(1));
    } else if (line.startsWith('diff --git ')) {
      inHunk = false;
    }
  }
  return { hunkHeaders, bodyLines };
}

const COMMIT1_FILES: Record<string, string> = {
  'notes.txt': 'alpha\nbravo\ncharlie\n',
  'docs/guide.md': '# 指南\n\n第一段。\n',
  'old-module.js': 'function gone() {\n  return 1;\n}\n',
};
const COMMIT2_FILES: Record<string, string> = {
  'notes.txt': 'alpha\nbravo-changed\ncharlie\ndelta\n',
  'docs/guide.md': '# 指南\n\n第一段改写。\n第二段新增。\n',
  'extra.log': '新增文件第一行\n中文内容第二行\n',
};

async function seedTwoCommits(page: Page, name: string): Promise<{ sha1: string; sha2: string }> {
  const created = await api(page, 'POST', '/api/git-repo/workspaces', { name, default_branch: 'main' });
  if (!created.ok() && created.status() !== 409) {
    throw new Error(`create workspace failed: HTTP ${created.status()} ${await created.text()}`);
  }
  const wsDir = path.resolve('..', '.runtime', 'workspaces', name);
  fs.mkdirSync(path.join(wsDir, 'docs'), { recursive: true });
  for (const [rel, content] of Object.entries(COMMIT1_FILES)) {
    fs.writeFileSync(path.join(wsDir, rel), content, 'utf8');
  }
  const paths1 = Object.keys(COMMIT1_FILES);
  // commit 端点自带暂存；若先 stage 删除文件，commit 内部 add 会因
  // pathspec 失配而失败（删除已入 index 后 git add 不再匹配该路径）。
  expect((await api(page, 'POST', `/api/git-repo/workspaces/${name}/commit`, { message: 'commit 1', paths: paths1 })).ok()).toBeTruthy();

  fs.rmSync(path.join(wsDir, 'old-module.js'));
  for (const [rel, content] of Object.entries(COMMIT2_FILES)) {
    fs.mkdirSync(path.dirname(path.join(wsDir, rel)), { recursive: true });
    fs.writeFileSync(path.join(wsDir, rel), content, 'utf8');
  }
  const paths2 = ['notes.txt', 'docs/guide.md', 'old-module.js', 'extra.log'];
  expect((await api(page, 'POST', `/api/git-repo/workspaces/${name}/commit`, { message: 'commit 2', paths: paths2 })).ok()).toBeTruthy();

  const commitsRes = await api(page, 'GET', `/api/git-repo/workspaces/${name}/commits`);
  expect(commitsRes.ok()).toBeTruthy();
  const commits = (await commitsRes.json()).commits as Array<{ sha: string; message: string }>;
  const sha2 = commits.find((c) => c.message === 'commit 2')!.sha;
  const sha1 = commits.find((c) => c.message === 'commit 1')!.sha;
  return { sha1, sha2 };
}

test.describe('P1-11 diff 可视化（真实后端）', () => {
  test('两次 commit 的 diff 图与 git diff 原文逐行一致', async ({ page }) => {
    await loginViaDevToken(page);
    const name = `p1-11-e2e-${Date.now().toString(36)}`;
    const { sha1, sha2 } = await seedTwoCommits(page, name);

    // UI：加载提交历史 → 选择两个 commit → 生成 diff 图
    await page.goto('/workbench');
    const panel = page.getByTestId('diff-view');
    await expect(panel).toBeVisible();
    await page.getByTestId('diff-workspace-input').fill(name);
    await page.getByRole('button', { name: '加载提交历史' }).click();
    await expect(page.getByTestId('diff-from')).toBeVisible();

    await page.getByTestId('diff-from').selectOption(sha1);
    await page.getByTestId('diff-to').selectOption(sha2);
    await page.getByRole('button', { name: '生成 diff 图' }).click();
    await expect(page.getByTestId('diff-summary')).toBeVisible();
    await expect(page.getByTestId('diff-summary')).toContainText(/4 个文件/);

    // 硬验收：DOM 渲染行 vs 子进程 git diff 归档原文，逐行对照
    const raw = fs.readFileSync(FIXTURE, 'utf8');
    const expected = extractFromRaw(raw);
    await expect(page.locator('.diff-hunk-header')).toHaveCount(expected.hunkHeaders.length);
    const actualHeaders = await page.locator('.diff-hunk-header').allTextContents();
    expect(actualHeaders).toEqual(expected.hunkHeaders);

    const rows = await page.getByTestId('diff-row').evaluateAll((nodes) =>
      nodes.map((n) => {
        const sign = n.querySelector('.diff-sign')?.textContent ?? '';
        return (sign || ' ') + (n.querySelector('.diff-text')?.textContent ?? '');
      }),
    );
    expect(rows.length).toBeGreaterThan(10);
    expect(rows).toEqual(expected.bodyLines);

    // 配色抽查：删除红 / 新增绿 / 修改琥珀成对标记
    const delRow = page.locator('.diff-row.diff-del', { hasText: '-第一段。' });
    const addRow = page.locator('.diff-row.diff-add', { hasText: '第一段改写。' });
    await expect(delRow).toBeVisible();
    await expect(addRow).toHaveClass(/diff-mod/);
    await expect(page.locator('.diff-row.diff-add.diff-mod')).toHaveCount(2);
    await expect(page.locator('.diff-row.diff-del.diff-mod')).toHaveCount(2);

    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-11-diff-two-commits.png'), fullPage: false });
  });

  test('commit vs 工作区：未提交改动以 worktree diff 呈现', async ({ page }) => {
    await loginViaDevToken(page);
    const name = `p1-11-wt-${Date.now().toString(36)}`;
    const { sha2 } = await seedTwoCommits(page, name);

    // 工作区新增一处未提交改动
    const wsDir = path.resolve('..', '.runtime', 'workspaces', name);
    fs.writeFileSync(path.join(wsDir, 'notes.txt'), 'alpha\nbravo-changed\ncharlie\ndelta\n工作区未提交行\n', 'utf8');

    await page.goto('/workbench');
    await page.getByTestId('diff-workspace-input').fill(name);
    await page.getByRole('button', { name: '加载提交历史' }).click();
    await expect(page.getByTestId('diff-from')).toBeVisible();
    await page.getByTestId('diff-from').selectOption(sha2);
    await page.getByTestId('diff-to').selectOption('worktree');
    await page.getByRole('button', { name: '生成 diff 图' }).click();
    await expect(page.getByTestId('diff-summary')).toBeVisible();

    await expect(page.locator('.diff-row.diff-add', { hasText: '工作区未提交行' })).toBeVisible();
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-11-diff-worktree.png'), fullPage: false });
  });
});
