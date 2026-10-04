import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

/**
 * P1-10 实时预览面板 — E2E（真实后端 + Playwright video 录屏证据）。
 *
 * 验收口径：改代码 → 预览实时刷新（含节流）。本文件以 Playwright 自带
 * video 取代人工录屏，运行后产出 .webm 归档（--output 指向本次专属目录）。
 *
 * 断言：
 *  - Markdown：编辑器输入 → 节流窗口内旧内容仍在 + 状态角标「节流中」；
 *    窗口过后新内容上屏 + 角标「已刷新」；
 *  - XSS：<script>/<img onerror> 在预览中以转义文本出现，不产生可执行节点；
 *  - HTML 沙箱：iframe sandbox=""（不授予 allow-scripts），srcDoc 注入内容；
 *  - 节流参数可配：面板选择器切换 300ms 后依然按窗口刷新。
 */

test.use({ video: 'on' });

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const THROTTLE_MS = 400; // 与面板默认值一致
const SHOT_DIR = path.join('..', 'evidence', 'p1-preview-20261003');

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

async function makeFixtureRoot(): Promise<string> {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-p1-10-'));
  await fs.writeFile(
    path.join(root, 'demo.md'),
    '# 演示文档\n\n这是 P1-10 实时预览的初始内容。\n',
    'utf8',
  );
  await fs.writeFile(
    path.join(root, 'sandbox.html'),
    '<!doctype html><html><body><h1>HTML 沙箱初始页</h1></body></html>',
    'utf8',
  );
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
  await expect(page.locator('.file-tree').getByText('demo.md')).toBeVisible();
}

test.describe('P1-10 实时预览面板（真实后端，video 录屏）', () => {
  let root: string;

  test.beforeAll(async () => {
    root = await makeFixtureRoot();
  });

  test('Markdown 编辑 → 节流窗口内保持旧内容，窗口后实时刷新', async ({ page }) => {
    test.setTimeout(60_000);
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-preview-${Date.now().toString(36)}`);

    // 打开 Markdown 文件：首载内容经节流后进入预览
    await page.locator('.file-tree').getByText('demo.md').click();
    await expect(page.getByTestId('wb-preview-markdown')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('wb-preview-markdown')).toContainText('演示文档');
    await expect(page.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'refreshed');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-markdown-initial.png') });

    // 在编辑器追加一段（未保存的草稿也应实时进入预览）
    const editor = page.locator('.editor-pane textarea');
    await editor.focus();
    await editor.press('End');
    await editor.pressSequentially('\n\n## 节流验证段落 P1-10\n\n- 实时刷新项 A\n- 实时刷新项 B\n', { delay: 20 });

    // 节流窗口内：角标「节流中」，新段落尚未上屏
    await expect(page.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'throttling');
    await expect(page.getByTestId('wb-preview-markdown')).not.toContainText('节流验证段落');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-throttling.png') });

    // 窗口过后：新内容上屏 + 角标「已刷新」
    await expect(page.getByTestId('wb-preview-markdown')).toContainText('节流验证段落', { timeout: THROTTLE_MS + 3000 });
    await expect(page.getByTestId('wb-preview-markdown')).toContainText('实时刷新项 B');
    await expect(page.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'refreshed');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-refreshed.png') });
  });

  test('Markdown XSS 防护：脚本与事件属性以转义文本呈现', async ({ page }) => {
    test.setTimeout(60_000);
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-xss-${Date.now().toString(36)}`);

    await page.locator('.file-tree').getByText('demo.md').click();
    await expect(page.getByTestId('wb-preview-markdown')).toBeVisible({ timeout: 10_000 });

    const editor = page.locator('.editor-pane textarea');
    await editor.fill(
      '# XSS 探针\n\n<script>window.__pwned=1</script>\n\n<img src=x onerror="window.__pwned=2">\n\n[危险链接](javascript:alert(1))\n',
    );
    const md = page.getByTestId('wb-preview-markdown');
    await expect(md).toContainText('XSS 探针', { timeout: THROTTLE_MS + 3000 });
    // 无可执行节点
    await expect(md.locator('script')).toHaveCount(0);
    await expect(md.locator('img[onerror]')).toHaveCount(0);
    await expect(md.locator('a[href^="javascript:"]')).toHaveCount(0);
    // 原文以转义文本可见
    await expect(md).toContainText('<script>window.__pwned=1</script>');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-xss-escaped.png') });
  });

  test('HTML 沙箱预览：sandbox 空串禁脚本，srcDoc 实时注入', async ({ page }) => {
    test.setTimeout(60_000);
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-html-${Date.now().toString(36)}`);

    await page.locator('.file-tree').getByText('sandbox.html').click();
    const frame = page.getByTestId('wb-preview-html-frame');
    await expect(frame).toBeVisible({ timeout: 10_000 });
    // 关键断言：sandbox 属性为空串 — 未授予 allow-scripts/allow-same-origin，
    // 编辑中的 <script> 无法执行、无法逃逸到父文档。
    await expect(frame).toHaveAttribute('sandbox', '');
    await expect(frame).toHaveAttribute('srcdoc', /HTML 沙箱初始页/);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-html-initial.png') });

    // 修改 HTML → 节流后 srcDoc 更新
    const editor = page.locator('.editor-pane textarea');
    await editor.fill('<!doctype html><html><body><h1 id="probe">沙箱修改后标题 P1-10</h1><script>window.__x=1</script></body></html>');
    await expect(frame).toHaveAttribute('srcdoc', /沙箱修改后标题 P1-10/, { timeout: THROTTLE_MS + 3000 });
    await expect(frame).toHaveAttribute('sandbox', '');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-html-updated.png') });
  });

  test('节流参数可配：切换 300ms 后仍按窗口刷新', async ({ page }) => {
    test.setTimeout(60_000);
    await loginViaDevToken(page);
    await openWorkspace(page, root, `p1-throttle-${Date.now().toString(36)}`);

    await page.locator('.file-tree').getByText('demo.md').click();
    await expect(page.getByTestId('wb-preview-markdown')).toBeVisible({ timeout: 10_000 });

    const select = page.getByTestId('wb-preview-throttle');
    await select.selectOption('300');
    await expect(select).toHaveValue('300');

    const editor = page.locator('.editor-pane textarea');
    await editor.fill('# 演示文档\n\n节流参数 300ms 验证。\n');
    // 窗口内旧内容仍在
    await expect(page.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'throttling');
    await expect(page.getByTestId('wb-preview-markdown')).not.toContainText('节流参数 300ms');
    // 窗口后刷新
    await expect(page.getByTestId('wb-preview-markdown')).toContainText('节流参数 300ms', { timeout: 300 + 3000 });
    await expect(page.getByTestId('wb-preview-status')).toHaveAttribute('data-state', 'refreshed');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-10-throttle-300.png') });
  });
});
