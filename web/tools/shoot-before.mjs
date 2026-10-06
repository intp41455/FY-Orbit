// Captures the current visual state of pages being reworked, so the redesign
// is driven by what is actually on screen rather than by reading CSS.
//
// Usage: node tools/shoot-before.mjs [baseURL] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const OUT = process.argv[3] ?? '../evidence/ui-2026-10-05/before';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();

/** Seed one workspace so /workbench has real content and the side panels overflow. */
async function withWorkbench(page, fn) {
  const fs2 = await import('node:fs/promises');
  const os = await import('node:os');
  const p = await import('node:path');
  const root = await fs2.mkdtemp(p.join(os.tmpdir(), 'fy-wb-'));
  await fs2.writeFile(p.join(root, 'app.py'), 'value = 1\n');
  await page.goto(`${BASE}/workbench`);
  await page.getByRole('button', { name: '注册工作区' }).click();
  await page.getByLabel('项目名称').fill(`shot-${Date.now().toString(36)}`);
  await page.getByLabel('授权根目录（绝对路径）').fill(root);
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await page.locator('.workspace-chip', { hasText: 'shot-' }).first().waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('.workspace-chip', { hasText: 'shot-' }).first().click();
  await page.waitForTimeout(1500);
  await fn(page);
}

const targets = [
  { name: 'before-工作台-宽屏.png', w: 1440, h: 900, route: '/workbench', seed: true },
  { name: 'before-chat调试-宽屏.png', w: 1440, h: 900, route: '/chat-debug' },
  { name: 'before-设置-宽屏.png', w: 1440, h: 900, route: '/settings' },
  { name: 'before-chat调试-窄屏.png', w: 820, h: 1180, route: '/chat-debug' },
  { name: 'before-设置-窄屏.png', w: 820, h: 1180, route: '/settings' },
];

for (const t of targets) {
  const ctx = await browser.newContext({ viewport: { width: t.w, height: t.h } });
  const page = await ctx.newPage();
  const auth = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
  const m = /fy_session=([^;]+)/.exec(auth.headers()['set-cookie'] ?? '');
  if (m) await ctx.addCookies([{ name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
  await page.goto(`${BASE}${t.route}`);
  await page.waitForTimeout(1200);
  if (t.seed) await withWorkbench(page, async () => {});
  await page.waitForTimeout(800);
  const file = path.join(OUT, t.name);
  await page.screenshot({ path: file, fullPage: !t.seed });
  console.log(`  ${t.name}  ${Math.round(fs.statSync(file).size / 1024)} KB`);
  await ctx.close();
}

await browser.close();
