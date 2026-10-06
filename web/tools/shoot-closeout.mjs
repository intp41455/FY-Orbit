// Re-shoots the stale package-B canvas screenshots (verify item V-6) and adds
// navigation evidence for the T-5 consolidation.
//
// V-6: the three 包B-*.png were captured at 02:42, BEFORE the contract fixes
// and before the inspector became persistent on narrow screens, so they no
// longer show the shipped UI. These replace them.
//
// Usage: node tools/shoot-closeout.mjs [baseURL] [outDir]

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const OUT = process.argv[3] ?? '../evidence/ui-2026-10-05';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const shots = [];

async function session(width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const auth = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
  const m = /fy_session=([^;]+)/.exec(auth.headers()['set-cookie'] ?? '');
  if (m) {
    await ctx.addCookies([
      { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
  return { ctx, page };
}

async function shoot(page, name, label) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file });
  const kb = Math.round(fs.statSync(file).size / 1024);
  shots.push({ name, label, kb });
  console.log(`  ${name}  (${kb} KB)`);
}

// Seed one real team so the canvas graph and the inspector have content.
async function seedTeam(page) {
  await page.goto(`${BASE}/canvas`);
  await page.getByLabel('团队名称').fill(`closeout-${Date.now().toString(36)}`);
  await page.getByRole('button', { name: '新建团队草稿' }).click();
  await page.getByText(/草稿已创建/).waitFor({ timeout: 20_000 });
  await page.getByRole('button', { name: '检查并启动团队' }).click();
  await page.getByText(/团队已启动/).waitFor({ timeout: 30_000 });
  await page.waitForTimeout(1200);
}

console.log('== 包 B 画布（重拍，替换 02:42 的过期版本） ==');
{
  const { ctx, page } = await session(1440, 900);
  await seedTeam(page);
  await shoot(page, '包B-canvas-宽屏默认.png', '1440x900 默认态');

  // Inspector open on a real member node — proves the role-key accessible name.
  const node = page.locator('.team-map [role=button]').first();
  await node.click();
  await page.waitForTimeout(700);
  await shoot(page, '包B-canvas-抽屉展开.png', '1440x900 节点检查器展开');

  await ctx.close();
}

{
  // 820px: below the 860px stacking breakpoint, inspector must persist.
  const { ctx, page } = await session(820, 1180);
  await seedTeam(page);
  await shoot(page, '包B-canvas-窄屏820.png', '820px 检查器常驻堆叠');
  await ctx.close();
}

console.log('== T-5 导航证据 ==');
{
  const { ctx, page } = await session(1440, 900);
  await page.goto(`${BASE}/workbench`);
  await page.waitForTimeout(1200);
  await shoot(page, '收口-导航-宽屏.png', '1440x900 LineIcon 导航 + 选中态 + 数字角标');
  await ctx.close();
}
{
  const { ctx, page } = await session(390, 844);
  await page.goto(`${BASE}/workbench`);
  await page.waitForTimeout(1200);
  await shoot(page, '收口-导航-窄屏390.png', '390px 顶部抽屉 + 只留图标（a11y 名称保留）');
  await ctx.close();
}
{
  const { ctx, page } = await session(700, 1000);
  await page.goto(`${BASE}/workbench`);
  await page.waitForTimeout(1200);
  await shoot(page, '收口-导航-中屏700.png', '700px 单列塌陷（本次修复的断点）');
  await ctx.close();
}

await browser.close();
console.log(`\n${shots.length} 张已写入 ${OUT}`);
