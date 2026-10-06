// Flake reproducer for e2e/ui-team.spec.ts:370 (terminal reattach).
//
// The handover recorded 1 failure in 6 runs ("click dispatched, POST /terminals
// never sent") without a stable trigger. Guessing is not acceptable, so this
// replays the exact spec sequence N times and records hard evidence per run:
//   - every /terminals request the page issued (method + timing)
//   - whether the click handler actually ran (console marker)
//   - the pane's data-state and the 终端输入 presence, polled over time
//
// Usage: node tools/diag-term-repro.mjs <runs> [baseURL]
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const RUNS = Number(process.argv[2] ?? 6);
const BASE = process.argv[3] ?? 'http://127.0.0.1:4173';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

const browser = await chromium.launch();
const results = [];

for (let run = 1; run <= RUNS; run++) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const termReqs = [];
  const consoleLines = [];
  const t0 = Date.now();
  const ms = () => Date.now() - t0;

  page.on('request', (r) => {
    const u = r.url();
    if (/\/terminals/.test(u)) termReqs.push({ t: ms(), method: r.method(), url: u.replace(BASE, '') });
  });
  page.on('console', (m) => consoleLines.push(`[${ms()}ms] ${m.type()}: ${m.text()}`.slice(0, 200)));

  const auth = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
  const m = /fy_session=([^;]+)/.exec(auth.headers()['set-cookie'] ?? '');
  if (m) {
    await ctx.addCookies([
      { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }

  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-repro-'));
  await fs.writeFile(path.join(root, 'app.py'), 'value = 1\n');

  const wsName = `repro-term-${Date.now().toString(36)}`;
  let outcome = 'unknown';
  let detail = {};

  try {
    await page.goto(`${BASE}/workbench`);
    await page.getByRole('button', { name: '注册工作区' }).click();
    await page.getByLabel('项目名称').fill(wsName);
    await page.getByLabel('授权根目录（绝对路径）').fill(root);
    await page.getByRole('button', { name: '注册', exact: true }).click();
    const chip = page.locator('.workspace-chip', { hasText: wsName });
    await chip.waitFor({ state: 'visible', timeout: 20_000 });
    const tChip = ms();
    await chip.click();

    // Record what the pane looks like right after the workspace switch settles.
    await page.waitForTimeout(1500);
    const afterSwitch = await page.evaluate(() => {
      const pane = document.querySelector('.terminal-pane');
      return {
        panePresent: !!pane,
        dataState: pane?.getAttribute('data-state') ?? null,
        startBtn: [...document.querySelectorAll('button')].some((b) => b.textContent?.includes('启动终端')),
        startDisabled: (() => {
          const b = [...document.querySelectorAll('button')].find((x) => x.textContent?.includes('启动终端'));
          return b ? b.disabled : null;
        })(),
        status: document.querySelector('.terminal-pane')?.textContent?.slice(0, 120) ?? null,
      };
    });

    const t = ms();
    await page.getByRole('button', { name: '启动终端' }).click({ timeout: 20_000 });
    const tClick = ms();

    let inputVisible = false;
    try {
      await page.getByLabel('终端输入').waitFor({ state: 'visible', timeout: 30_000 });
      inputVisible = true;
    } catch { /* recorded as failure below */ }

    outcome = inputVisible ? 'PASS' : 'FAIL';
    detail = { afterSwitch, chipAt: tChip, clickStart: t, clickEnd: tClick };
  } catch (e) {
    outcome = 'ERROR';
    detail = { message: String(e).split('\n')[0].slice(0, 220) };
  }

  const postCount = termReqs.filter((r) => r.method === 'POST').length;
  results.push({
    run,
    outcome,
    postTerminals: postCount,
    termReqs,
    detail,
    consoleTail: consoleLines.slice(-6),
  });

  console.log(
    `run ${run}: ${outcome}  POST /terminals x${postCount}  click ${detail.clickStart ?? '-'}->${detail.clickEnd ?? '-'}ms` +
      `  state=${detail.afterSwitch?.dataState ?? '-'}`,
  );
  await ctx.close();
}

await browser.close();

console.log('\n===== SUMMARY =====');
const pass = results.filter((r) => r.outcome === 'PASS').length;
console.log(`PASS ${pass} / ${results.length}`);
for (const r of results.filter((x) => x.outcome !== 'PASS')) {
  console.log(`\n--- run ${r.run} ${r.outcome} (POST x${r.postTerminals}) ---`);
  console.log('  requests:', JSON.stringify(r.termReqs));
  console.log('  detail  :', JSON.stringify(r.detail));
  console.log('  console :', r.consoleTail.join('\n            '));
}
