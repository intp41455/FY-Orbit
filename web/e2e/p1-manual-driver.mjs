// Standalone diagnostic driver for P1-09 / P1-22 (run outside the Playwright
// test runner so failure details are fully visible). Real backend + real UI.
import { chromium } from '@playwright/test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const BASE = 'http://127.0.0.1:5189';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const SHOT_DIR = path.join('..', 'evidence', 'p1-file-browser-20261003');

let passed = 0, failed = 0;
function check(name, cond, extra = '') {
  if (cond) { passed++; console.log(`  PASS ${name}`); }
  else { failed++; console.log(`  FAIL ${name} ${extra}`); }
}

async function makeFixtureRoot() {
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
  ];
  for (const [rel, content] of docs) await fs.writeFile(path.join(root, rel), content, 'utf8');
  const bigLine = 'p1-09 large file probe line 0123456789\n';
  await fs.writeFile(path.join(root, 'big.log'), bigLine.repeat(Math.ceil(3 * 1024 * 1024 / bigLine.length)));
  await fs.writeFile(path.join(root, 'logo.bin'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x01, 0x02, 0x00, 0xff]));
  await fs.writeFile(path.join(root, '.hidden-note.txt'), '我是隐藏文件，默认不可见。');
  await fs.writeFile(path.join(root, 'notes/.hidden-in-dir.txt'), '子目录里的隐藏文件。');
  return root;
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const consoleErrors = [];
page.on('pageerror', (e) => consoleErrors.push(e.message));

try {
  // login
  const res = await page.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
  console.log('dev-token status:', res.status());
  const body = await res.json();
  const setCookie = res.headers()['set-cookie'] ?? '';
  const seg = setCookie.split(';').find((s) => s.trim().startsWith('fy_session='));
  if (seg) {
    await ctx.addCookies([{ name: 'fy_session', value: seg.split('=')[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
  }
  // csrf from body

  const root = await makeFixtureRoot();
  const wsName = `p1-diag-${Date.now().toString(36)}`;

  await page.goto(`${BASE}/workbench`);
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill(wsName);
  await page.getByLabel('授权根目录（绝对路径）').fill(root);
  await page.getByRole('button', { name: '注册', exact: true }).click();
  const chip = page.locator('.workspace-chip', { hasText: wsName });
  await chip.waitFor({ timeout: 15000 });
  console.log('workspace chip visible');
  await chip.click();
  await page.locator('.file-tree').getByText('README.md').waitFor({ timeout: 15000 });
  console.log('file tree loaded (README.md visible)');

  // ---------------- P1-09 large file
  console.log('--- P1-09 大文件');
  await page.locator('.file-tree').getByText('big.log').click();
  const notice = page.getByTestId('large-file-notice');
  try {
    await notice.waitFor({ timeout: 8000 });
    check('大文件提示可见', await notice.isVisible());
    const t = await notice.textContent();
    check('提示含截断/懒加载语义', /大文件已停止内联加载/.test(t) && /2 MiB/.test(t), t ?? '');
    check('原始英文报错不外泄', (await page.getByText('inline-edit limit').count()) === 0);
    check('大文件不渲染文本域', (await page.locator('.editor-pane textarea').count()) === 0);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-large-file-notice.png') });
    console.log('  screenshot saved');
  } catch (e) {
    check('大文件提示可见', false, e.message.split('\n')[0]);
  }

  // ---------------- P1-09 binary
  console.log('--- P1-09 二进制');
  await page.locator('.file-tree').getByText('logo.bin').click();
  const ph = page.getByTestId('binary-placeholder');
  try {
    await ph.waitFor({ timeout: 8000 });
    check('二进制占位可见', await ph.isVisible());
    const t = await ph.textContent();
    check('占位说明避免乱码策略', /二进制文件/.test(t) && /为避免乱码/.test(t), t ?? '');
    check('二进制不渲染文本域', (await page.locator('.editor-pane textarea').count()) === 0);
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-binary-placeholder.png') });
    console.log('  screenshot saved');
  } catch (e) {
    check('二进制占位可见', false, e.message.split('\n')[0]);
  }

  // ---------------- P1-09 hidden files
  console.log('--- P1-09 隐藏文件');
  const tree = page.locator('.file-tree');
  check('默认隐藏 .hidden-note.txt', (await tree.getByText('.hidden-note.txt').count()) === 0);
  await tree.getByText('notes').click();
  await tree.getByText('plan.txt').waitFor({ timeout: 8000 });
  check('子目录内 dotfile 亦隐藏', (await tree.getByText('.hidden-in-dir.txt').count()) === 0);
  await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-hidden-before-toggle.png') });
  const toggle = page.getByTestId('show-hidden-toggle');
  check('可见性开关存在', (await toggle.count()) === 1);
  await toggle.check();
  await tree.getByText('.hidden-note.txt').waitFor({ timeout: 8000 });
  check('开关打开后 .hidden-note.txt 可见', await tree.getByText('.hidden-note.txt').isVisible());
  check('开关打开后子目录 dotfile 可见', (await tree.getByText('.hidden-in-dir.txt').count()) === 1);
  await page.screenshot({ path: path.join(SHOT_DIR, 'p1-09-hidden-after-toggle.png') });
  await toggle.uncheck();
  await page.waitForTimeout(500);
  check('关闭开关后再次隐藏', (await tree.getByText('.hidden-note.txt').count()) === 0);

  // ---------------- P1-22 doc tree
  console.log('--- P1-22 文档树图');
  const dt = page.getByTestId('doc-tree');
  try {
    await dt.waitFor({ timeout: 10000 });
    await dt.getByText('architecture.md').waitFor({ timeout: 15000 });
    const docCount = await dt.locator('li.tree-row.file').count();
    check('文档树 ≥10 个真实文档节点', docCount >= 10, `actual=${docCount}`);
    const dirCount = await dt.locator('li.tree-row.dir').count();

    // collapse docs
    await dt.getByText('docs', { exact: true }).first().click();
    await page.waitForTimeout(400);
    const afterCollapse = (await dt.getByText('architecture.md').count()) === 0;
    check('目录可折叠', afterCollapse);
    // expand again
    await dt.getByText('docs', { exact: true }).first().click();
    await dt.getByText('architecture.md').waitFor({ timeout: 8000 });
    check('目录可再展开', await dt.getByText('architecture.md').isVisible());

    // open a doc
    await dt.getByText('README.md').click();
    const preview = page.getByTestId('doc-tree-preview');
    await preview.getByText('README.md').waitFor({ timeout: 8000 });
    await preview.getByText('Find Yourself 工作台').waitFor({ timeout: 8000 });
    const pt = await preview.textContent();
    check('点击文档打开真实内容', /Find Yourself 工作台/.test(pt ?? ''), pt ?? '');
    await page.screenshot({ path: path.join(SHOT_DIR, 'p1-22-doc-tree-open.png') });

    // binary in tree
    await dt.getByText('logo.bin').click();
    await preview.getByText('二进制文件，不渲染文本内容').waitFor({ timeout: 8000 });
    const bt = await preview.textContent();
    check('树图打开二进制显示说明', /二进制文件，不渲染文本内容/.test(bt ?? ''), bt ?? '');
    console.log(`  tree: ${docCount} file nodes, ${dirCount} dir nodes`);
  } catch (e) {
    check('文档树渲染', false, e.message.split('\n')[0]);
  }

  check('无页面运行时错误', consoleErrors.length === 0, consoleErrors.join(' | '));
} finally {
  console.log(`\nRESULT: ${passed} passed, ${failed} failed`);
  await browser.close();
  process.exit(failed === 0 ? 0 : 1);
}
