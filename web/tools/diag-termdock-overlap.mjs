// Probe: at mobile width, .wb-termdock-foot intercepts clicks on 启动终端.
//
// This is a PRE-EXISTING defect (the colour-token commit touched no layout
// property). It was invisible to the previous agent because it only ever ran
// `playwright --project=desktop`; the mobile project was never exercised.
//
// Reports the dock's box, its children boxes, the button's box, and what
// document.elementFromPoint() returns at the button's centre.
//
// Usage: node tools/diag-termdock-overlap.mjs [baseURL] [width] [height]
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const W = Number(process.argv[3] ?? 393);
const H = Number(process.argv[4] ?? 851);
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: W, height: H } });
const page = await ctx.newPage();

const auth = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
const m = /fy_session=([^;]+)/.exec(auth.headers()['set-cookie'] ?? '');
if (m) {
  await ctx.addCookies([
    { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
}

const root = await fs.mkdtemp(path.join(os.tmpdir(), 'fy-dock-'));
await fs.writeFile(path.join(root, 'app.py'), 'value = 1\n');

await page.goto(`${BASE}/workbench`);
await page.getByRole('button', { name: '注册工作区' }).click();
await page.getByLabel('项目名称').fill(`dock-${Date.now().toString(36)}`);
await page.getByLabel('授权根目录（绝对路径）').fill(root);
await page.getByRole('button', { name: '注册', exact: true }).click();
const chip = page.locator('.workspace-chip', { hasText: 'dock-' });
await chip.waitFor({ state: 'visible', timeout: 20_000 });
await chip.click();
await page.waitForTimeout(2000);

const report = await page.evaluate(() => {
  const r = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1), bottom: +b.bottom.toFixed(1) };
  };
  const dock = document.querySelector('.wb-termdock');
  const foot = document.querySelector('.wb-termdock-foot');
  const bar = document.querySelector('.wb-termdock-bar');
  const view = document.querySelector('.wb-termdock-view:not([hidden])');
  const btn = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === '启动终端');

  const out = {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    dock: r(dock),
    bar: r(bar),
    view: r(view),
    foot: r(foot),
    btn: r(btn),
  };
  if (btn) {
    const bb = btn.getBoundingClientRect();
    const cx = bb.x + bb.width / 2;
    const cy = bb.y + bb.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    out.probe = {
      point: [Math.round(cx), Math.round(cy)],
      hitTag: hit ? `${hit.tagName.toLowerCase()}.${(hit.className || '').toString().split(' ').slice(0, 2).join('.')}` : null,
      hitIsButton: hit === btn || btn.contains(hit),
      hitInsideFoot: foot ? foot.contains(hit) : false,
    };
  }
  // Which ancestor scrolls / clips the dock?
  const chain = [];
  for (let el = dock; el && el !== document.body; el = el.parentElement) {
    const cs = getComputedStyle(el);
    chain.push({
      sel: el.tagName.toLowerCase() + (el.className ? `.${String(el.className).split(' ').slice(0, 2).join('.')}` : ''),
      display: cs.display,
      position: cs.position,
      overflow: `${cs.overflow}/${cs.overflowY}`,
      height: cs.height,
      flex: cs.flex,
    });
  }
  out.ancestors = chain;
  return out;
});

console.log(JSON.stringify(report, null, 2));
await browser.close();
