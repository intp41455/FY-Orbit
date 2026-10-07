// Desktop workbench acceptance against the REAL Edge App-Mode window.
//
// The window is launched by desktop/app/run_desktop.py with
// FY_EDGE_EXTRA_ARGS="--remote-debugging-port=9333"; this script attaches to
// that window over CDP (it is the actual desktop window, not a surrogate) and
// performs the same core operations as the browser acceptance:
// login -> workspace registration -> file tree -> edit/save -> version conflict
// -> interactive terminal (run + stop) -> git diff/stage/commit -> refresh.
//
// Usage (from web/):  node scripts/desktop-window-acceptance.mjs
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CDP = process.env.FY_CDP_URL ?? 'http://127.0.0.1:9333';
const BASE = process.env.FY_DESKTOP_URL ?? 'http://127.0.0.1:8088';
const TOKEN = process.env.FY_LOCAL_TOKEN ?? 'desktop-token-secret';
const WS_ROOT =
  process.env.FY_ACCEPTANCE_WS_ROOT ??
  path.join(__dirname, '../../.runtime/acceptance-ws');
const OUT = path.resolve(__dirname, '../../evidence/acceptance-2026-10-02/desktop');

fs.mkdirSync(OUT, { recursive: true });
const results = [];
let shotIdx = 0;

function record(step, ok, detail) {
  results.push({ step, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${step}${detail ? ` — ${detail}` : ''}`);
}
async function shot(name) {
  shotIdx += 1;
  const file = path.join(OUT, `${String(shotIdx).padStart(2, '0')}-${name}.png`);
  // App-mode windows can be minimised/occluded: Chromium then produces no frame
  // and Playwright's default 15s screenshot timeout trips. Give it room.
  await page.screenshot({ path: file, timeout: 60000, animations: 'disabled' });
  return file;
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const browser = await chromium.connectOverCDP(CDP);
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? (await ctx.newPage());
page.setDefaultTimeout(15000);

try {
  // ---------------------------------------------------------------- login ---
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  const pw = page.locator('input[type="password"]');
  if (await pw.count()) {
    await pw.first().fill(TOKEN);
    await page.getByRole('button', { name: /本地口令直接登录/ }).click();
    await page.waitForURL(/\/chat/, { timeout: 15000 });
    record('desktop login (loopback dev token)', true, page.url());
  } else {
    record('desktop login (loopback dev token)', true, 'already authenticated');
  }
  await shot('login');

  // ------------------------------------------------------------ workbench ---
  await page.goto(`${BASE}/workbench`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.workbench-grid, .workspace-chip, .card', { timeout: 15000 });

  if ((await page.locator('.workspace-chip').count()) === 0) {
    await page.getByRole('button', { name: '注册工作区' }).click();
    await page.getByLabel('项目名称').fill('acceptance-ws');
    await page.getByLabel('授权根目录（绝对路径）').fill(WS_ROOT);
    await page.getByRole('button', { name: '注册', exact: true }).click();
    await page.waitForSelector('.workspace-chip', { timeout: 15000 });
    record('register authorized workspace', true, WS_ROOT);
  } else {
    record('register authorized workspace', true, 'already registered');
  }
  await shot('workbench');

  // Failure state first: a relative root must be rejected with a visible error.
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill('bad-root-test');
  await page.getByLabel('授权根目录（绝对路径）').fill('relative/path/does-not-exist');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.waitForSelector('[role="alert"]', { timeout: 15000 });
  const badRootMsg = (await page.locator('[role="alert"]').first().textContent()) ?? '';
  record('invalid workspace root rejected', /absolute path|绝对路径/i.test(badRootMsg), badRootMsg.trim());
  await shot('failure-invalid-root');
  await page.getByRole('button', { name: '取消' }).click();

  // ----------------------------------------------------------- file tree ---
  await page.locator('li.tree-row', { hasText: 'notes' }).first().click();
  await page.locator('li.tree-row', { hasText: 'todo.txt' }).first().click();
  await page.waitForSelector('.code-area', { timeout: 15000 });
  // The textarea renders before the revision meta arrives; wait for it.
  await page.waitForFunction(
    () => /r\d+/.test(document.querySelector('.editor-pane .row .muted')?.textContent ?? ''),
    undefined,
    { timeout: 15000 },
  );
  const revBefore = (await page.locator('.editor-pane .row .muted').first().textContent()) ?? '';
  record('file tree -> editor load', /r\d+/.test(revBefore), revBefore.trim());
  await shot('editor-loaded');

  // ---------------------------------------------------------- edit + save ---
  const marker = `desktop-window-edit-${Date.now()}`;
  await page.locator('.code-area').fill(`line one\n${marker}\n`);
  await page.locator('.editor-pane button', { hasText: '保存' }).click();
  await page.waitForSelector('.editor-pane .notice.ok', { timeout: 15000 });
  const savedNote = (await page.locator('.editor-pane .notice.ok').first().textContent()) ?? '';
  record('edit + save (revision bump)', /新版本 r\d+/.test(savedNote), savedNote.trim());
  await shot('file-saved');

  // ---------------------------------------------------- version conflict ----
  const revText = (await page.locator('.editor-pane .row .muted').first().textContent()) ?? 'r0';
  const curRev = Number((revText.match(/r(\d+)/) ?? [0, 0])[1]);
  const external = await page.evaluate(async ({ curRev }) => {
    const me = await (await fetch('/auth/me')).json();
    const ws = (await (await fetch('/api/workbench/workspaces')).json()).items[0];
    const r = await fetch(
      `/api/workbench/workspaces/${ws.id}/file?path=${encodeURIComponent('notes/todo.txt')}`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf_token, Origin: location.origin },
        body: JSON.stringify({ content: 'line one\nexternal concurrent writer\n', expected_revision: curRev }),
      },
    );
    return { status: r.status, body: await r.json() };
  }, { curRev });
  record('external writer bumps revision', external.status === 200, `rev ${curRev} -> ${external.body?.revision}`);

  await page.locator('.code-area').fill('line one\nconflicting local edit\n');
  await page.locator('.editor-pane button', { hasText: '保存' }).click();
  await page.waitForSelector('.editor-pane .error-text', { timeout: 15000 });
  const conflictMsg = (await page.locator('.editor-pane .error-text').first().textContent()) ?? '';
  record('stale save intercepted (409, no silent overwrite)', conflictMsg.includes('保存冲突'), conflictMsg.trim());
  await shot('version-conflict');

  // ------------------------------------------------------------- terminal ---
  await page.locator('.terminal-pane button', { hasText: '启动终端' }).click();
  await page.waitForSelector('.terminal-input-row input', { timeout: 20000 });
  const termInput = page.locator('.terminal-input-row input');
  await termInput.fill('echo desktop-window-terminal-ok');
  await termInput.press('Enter');
  await page.waitForFunction(
    () => (document.querySelector('.terminal-output')?.textContent ?? '').includes('desktop-window-terminal-ok'),
    undefined,
    { timeout: 15000 },
  );
  record('interactive terminal executes command', true, 'echo output observed');
  await shot('terminal-echo');

  await termInput.fill('ping -t 127.0.0.1');
  await termInput.press('Enter');
  await page.waitForTimeout(2500);
  await page.locator('.terminal-pane button.danger', { hasText: '停止' }).click();
  await page.waitForFunction(
    () => (document.querySelector('.terminal-pane .row .muted')?.textContent ?? '').includes('stopped'),
    undefined,
    { timeout: 15000 },
  );
  record('terminal stop (process tree kill)', true, 'state=stopped');
  await shot('terminal-stopped');

  // ----------------------------------------------------------------- git ---
  // Make sure there IS an unstaged change to review (the tree may be clean
  // because a previous run already committed it). The editor still holds the
  // pre-conflict revision, so reload first to pick up the current one.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.workspace-chip', { timeout: 15000 });
  await page.locator('li.tree-row', { hasText: 'notes' }).first().click();
  await page.locator('li.tree-row', { hasText: 'todo.txt' }).first().click();
  await page.waitForFunction(
    () => /r\d+/.test(document.querySelector('.editor-pane .row .muted')?.textContent ?? ''),
    undefined,
    { timeout: 15000 },
  );
  await page.locator('.code-area').fill(`line one\ngit-review-${Date.now()}\n`);
  await page.locator('.editor-pane button', { hasText: '保存' }).click();
  await page.waitForSelector('.editor-pane .notice.ok', { timeout: 15000 });
  record('prepare working-tree change for git steps', true, 'editor save');

  // The panel re-reads status on refresh (which also clears the selection), so
  // always (re-)select the target row right before each git action.
  const selectTarget = async () => {
    const box = page.locator('li.git-row', { hasText: 'notes/todo.txt' }).locator('input[type="checkbox"]');
    await box.waitFor({ state: 'visible', timeout: 15000 });
    if (!(await box.isChecked())) await box.check();
    await page.waitForFunction(
      () => {
        const btn = [...document.querySelectorAll('.git-pane button')].find((b) => b.textContent.includes('暂存选中'));
        return btn && !btn.disabled;
      },
      undefined,
      { timeout: 15000 },
    );
  };

  await page.locator('.git-pane button', { hasText: '刷新' }).click();
  await page.waitForSelector('li.git-row', { timeout: 15000 });
  await selectTarget();
  await page.locator('.git-pane button', { hasText: '查看工作区差异' }).click();
  await page.waitForSelector('.diff-content', { timeout: 15000 });
  const diffText = (await page.locator('.diff-content').first().textContent()) ?? '';
  record('git diff of selected file', diffText.includes('notes/todo.txt'), `+${diffText.split('\n').filter((l) => l.startsWith('+')).length} lines`);
  await shot('git-diff');

  await selectTarget();
  await page.locator('.git-pane button', { hasText: '暂存选中' }).click();
  await page.waitForFunction(
    () => [...document.querySelectorAll('li.git-row')].some((li) => li.textContent.includes('staged(')),
    undefined,
    { timeout: 15000 },
  );
  record('git stage of selected file', true, 'row moved to staged');
  await selectTarget();
  await page.locator('input[placeholder="描述本次改动…"]').fill('chore: desktop window acceptance commit');
  await page.locator('.git-pane button', { hasText: '提交选中文件' }).click();
  await page.waitForFunction(
    () => (document.querySelector('.git-pane .notice.ok')?.textContent ?? '').includes('已提交'),
    undefined,
    { timeout: 15000 },
  );
  const commitNote = (await page.locator('.git-pane .notice.ok').first().textContent()) ?? '';
  record('git commit of selected files', true, commitNote.trim());
  await shot('git-committed');

  // ------------------------------------------------- refresh (恢复) check ---
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.workspace-chip', { timeout: 15000 });
  const chip = (await page.locator('.workspace-chip').first().textContent()) ?? '';
  const treeCount = await page.locator('.file-tree li').count();
  record('refresh restores workspace + tree', treeCount > 0, `${chip.trim()} · ${treeCount} tree rows`);
  await shot('after-refresh');

  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(
    path.join(OUT, 'desktop-acceptance-result.json'),
    JSON.stringify({ base: BASE, cdp: CDP, workspaceRoot: WS_ROOT, at: new Date().toISOString(), results }, null, 2),
  );
  console.log(`\n${results.length - failed.length}/${results.length} desktop window steps passed`);
  process.exitCode = failed.length ? 1 : 0;
} catch (err) {
  record('exception', false, String(err?.message ?? err));
  try {
    await shot('error');
  } catch {
    /* screenshot may fail if the window is gone */
  }
  fs.writeFileSync(
    path.join(OUT, 'desktop-acceptance-result.json'),
    JSON.stringify({ base: BASE, cdp: CDP, at: new Date().toISOString(), results }, null, 2),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}
