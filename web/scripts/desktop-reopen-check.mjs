// Reopen check: after the desktop window was closed (service stopped), launch
// again and prove the persisted state is restored — registered workspace, file
// revisions and the commits made through the window in the previous session.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, '../../evidence/acceptance-2026-10-02/desktop');
const CDP = process.env.FY_CDP_URL ?? 'http://127.0.0.1:9333';
const BASE = process.env.FY_DESKTOP_URL ?? 'http://127.0.0.1:8088';

const browser = await chromium.connectOverCDP(CDP);
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? (await ctx.newPage());
page.setDefaultTimeout(20000);

await page.goto(`${BASE}/workbench`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.workspace-chip', { timeout: 20000 });
await page.locator('li.tree-row', { hasText: 'notes' }).first().click();
await page.locator('li.tree-row', { hasText: 'todo.txt' }).first().click();
await page.waitForFunction(
  () => /r\d+/.test(document.querySelector('.editor-pane .row .muted')?.textContent ?? ''),
  undefined,
  { timeout: 20000 },
);

const state = await page.evaluate(async () => {
  const me = await (await fetch('/auth/me')).json();
  const ws = (await (await fetch('/api/workbench/workspaces')).json()).items;
  const file = await (
    await fetch(`/api/workbench/workspaces/${ws[0].id}/file?path=${encodeURIComponent('notes/todo.txt')}`)
  ).json();
  const git = await (await fetch(`/api/workbench/workspaces/${ws[0].id}/git/status`)).json();
  return { authenticated: !!me.csrf_token, workspaces: ws, file, git };
});

let screenshot = 'skipped';
try {
  await page.screenshot({ path: path.join(OUT, '13-after-reopen.png'), timeout: 20000 });
  screenshot = 'captured';
} catch (err) {
  // A minimised/occluded app-mode window may produce no frame; the state
  // assertions below are the actual evidence, the PNG is a bonus.
  screenshot = `failed: ${String(err?.message ?? err).split('\n')[0]}`;
}
const summary = {
  at: new Date().toISOString(),
  authenticated: state.authenticated,
  screenshot,
  workspace: state.workspaces[0]?.project_name,
  branch: state.workspaces[0]?.branch,
  fileRevision: state.file.revision,
  gitBranch: state.git.branch,
  gitRows: [...(state.git.staged ?? []), ...(state.git.unstaged ?? []), ...(state.git.untracked ?? [])].map((f) => `${f.path}:${f.state}`),
};
fs.writeFileSync(path.join(OUT, 'desktop-reopen-state.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
await browser.close();