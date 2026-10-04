import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

/**
 * P1-12 git 提交树图 — E2E against the REAL backend (isolated: port 8094).
 *
 * 验收口径（真实运行证据）：
 *  1. 树图渲染 ≥5 节点真实历史（git_repo 服务 API 造历史，绝不 mock）；
 *  2. 点击节点联动 diff（P1-11 的 GET /api/git-repo/.../diff 真实端点）。
 *
 * 环境约定（impl-p1-12 专属，隔离纪律）：
 *  - 后端 8094，FY_WORKSPACE_ROOT 指向 P12_WS_ROOT（独立目录）；
 *  - preview 4192（E2E_PREVIEW_PORT），API 代理到 8094；
 *  - 截图归档 evidence/p1-12-commit-graph-20261003/。
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const WS_ROOT = process.env.P12_WS_ROOT ?? '';
const SHOT_DIR = path.join('..', 'evidence', 'p1-12-commit-graph-20261003');

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

async function api(page: Page | APIRequestContext, method: 'GET' | 'POST', pathName: string, body?: unknown) {
  const req = 'request' in page ? page.request : page;
  const res = await req.fetch(pathName, {
    method,
    data: body === undefined ? undefined : JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
  });
  if (!res.ok()) throw new Error(`${method} ${pathName} failed: HTTP ${res.status()} ${await res.text()}`);
  return res.json();
}

const COMMITS: Array<{ files: Record<string, string>; message: string }> = [
  { files: { 'README.md': '# P1-12 树图示例仓库\n\n用于提交树图验收的真实仓库。\n' }, message: 'init: 项目骨架' },
  { files: { 'docs/architecture.md': '# 架构说明\n\n树图 + diff 联动。\n' }, message: 'docs: 添加架构说明' },
  { files: { 'src/core.py': 'def main():\n    pass\n' }, message: 'feat: 核心模块初版' },
  { files: { 'src/core.py': 'def main():\n    return 42\n' }, message: 'fix: 修复初始化边界' },
  { files: { 'README.md': '# P1-12 树图示例仓库\n\n使用指南：点击节点查看 diff。\n' }, message: 'docs: 更新 README 使用指南' },
  { files: { 'src/utils.py': 'def helper():\n    return True\n' }, message: 'feat: 新增工具函数' },
];

test.describe('P1-12 git 提交树图（真实后端）', () => {
  let workspaceName = '';
  let repoDir = '';

  test.beforeAll(async ({ request }) => {
    if (!WS_ROOT) throw new Error('P12_WS_ROOT 未设置（后端 FY_WORKSPACE_ROOT 指向的独立目录）');
    await fs.mkdir(SHOT_DIR, { recursive: true });
    workspaceName = `p12-${Date.now().toString(36)}`;
    repoDir = path.join(WS_ROOT, workspaceName);

    // 登录：request fixture 自动保存 Set-Cookie（fy_session）到其 cookie jar，
    // 后续 setup 调用自动携带；CSRF 取自响应体。
    const res = await request.post('/auth/local/dev-token', { data: { token: LOCAL_TOKEN } });
    if (!res.ok()) throw new Error(`dev-token login failed: HTTP ${res.status()}`);
    csrfToken = (await res.json()).csrf_token;

    // 1) git_repo 服务：初始化真实仓库
    await api(request, 'POST', '/api/git-repo/workspaces', { name: workspaceName, default_branch: 'main' });

    // 2) git_repo 服务：造 ≥5 个真实提交（显式 stage + commit，绝不 mock）
    for (const step of COMMITS) {
      for (const [rel, content] of Object.entries(step.files)) {
        await fs.mkdir(path.dirname(path.join(repoDir, rel)), { recursive: true });
        await fs.writeFile(path.join(repoDir, rel), content, 'utf8');
      }
      await api(request, 'POST', `/api/git-repo/workspaces/${workspaceName}/stage`, {
        paths: Object.keys(step.files),
      });
      await api(request, 'POST', `/api/git-repo/workspaces/${workspaceName}/commit`, {
        message: step.message,
        paths: Object.keys(step.files),
      });
    }
    const history = await api(request, 'GET', `/api/git-repo/workspaces/${workspaceName}/commits?limit=100`);
    if (history.count < 5) throw new Error(`真实提交历史不足 5 条: ${history.count}`);

    // 3) 注册工程工作台工作区（authorized_root 即该仓库目录），供页面选中
    await api(request, 'POST', '/api/workbench/workspaces', {
      project_name: workspaceName,
      authorized_root: repoDir.replace(/\\/g, '/'),
    });
  });

  test('树图渲染 ≥5 节点真实历史（sha+message+作者+相对时间）', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/workbench');
    const chip = page.locator('.workspace-chip', { hasText: workspaceName });
    await expect(chip).toBeVisible({ timeout: 20_000 });
    await chip.click();

    const graph = page.getByTestId('commit-graph');
    await expect(graph).toBeVisible();
    await page.getByTestId('commit-graph-ws-input').fill(workspaceName);
    await page.getByTestId('commit-graph-load').click();

    const nodes = page.getByTestId('commit-node');
    await expect(nodes.first()).toBeVisible();
    const count = await nodes.count();
    expect(count).toBeGreaterThanOrEqual(5);

    // 最新提交内容：sha 前 7 位 + message + 作者 + 相对时间
    const newest = COMMITS[COMMITS.length - 1].message;
    await expect(nodes.first()).toContainText(newest);
    await expect(nodes.first()).toContainText('Find Yourself');
    await expect(nodes.first()).toContainText(/秒前|分钟前/);

    // parents 连线 SVG 与 ≥5 个节点圆点共存
    await expect(graph.locator('svg.commit-graph-lanes circle').first()).toBeVisible();

    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-12-commit-graph-nodes.png'), fullPage: false });
  });

  test('点击节点联动 diff：节点 vs 前驱展示真实差异', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/workbench');
    await page.locator('.workspace-chip', { hasText: workspaceName }).click();
    await page.getByTestId('commit-graph-ws-input').fill(workspaceName);
    await page.getByTestId('commit-graph-load').click();
    const nodes = page.getByTestId('commit-node');
    await expect(nodes.first()).toBeVisible();

    // 点击第 2 个节点（docs: 更新 README 使用指南）→ 与其前驱的 diff
    await nodes.nth(1).click();
    const detail = page.getByTestId('commit-detail');
    await expect(detail).toBeVisible();
    await expect(detail).toContainText('docs: 更新 README 使用指南');
    await expect(detail).toContainText('↔ 前驱');

    const diffPane = page.getByTestId('commit-diff');
    await expect(diffPane).toBeVisible({ timeout: 15_000 });
    // 真实 git diff：README.md 被修改，存在 + 与 - 行
    await expect(diffPane).toContainText('README.md');
    const addRows = diffPane.locator('.diff-row.diff-add');
    const delRows = diffPane.locator('.diff-row.diff-del');
    expect(await addRows.count()).toBeGreaterThanOrEqual(1);
    expect(await delRows.count()).toBeGreaterThanOrEqual(1);
    await expect(diffPane).toContainText('使用指南：点击节点查看 diff');
    // 无错误泄漏
    await expect(page.getByTestId('commit-diff-error')).toHaveCount(0);

    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-12-commit-graph-click-diff.png'), fullPage: false });
  });

  test('选中两个节点互比 diff', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/workbench');
    await page.locator('.workspace-chip', { hasText: workspaceName }).click();
    await page.getByTestId('commit-graph-ws-input').fill(workspaceName);
    await page.getByTestId('commit-graph-load').click();
    const nodes = page.getByTestId('commit-node');
    await expect(nodes.first()).toBeVisible();

    await nodes.nth(0).click();
    await nodes.nth(3).click();
    await expect(page.getByTestId('commit-detail-range')).toContainText('↔');
    const diffPane = page.getByTestId('commit-diff');
    await expect(diffPane).toBeVisible({ timeout: 15_000 });
    // 第 4 ↔ 第 1 个提交：横跨 init→docs→feat 的差异，README.md 与 src 均可能出现
    await expect(diffPane.locator('[data-testid="commit-diff-file"]').first()).toBeVisible();
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-12-commit-graph-two-nodes.png'), fullPage: false });
  });
});
