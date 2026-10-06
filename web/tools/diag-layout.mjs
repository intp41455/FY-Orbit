import { chromium } from 'playwright';
const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 393, height: 851 } });
const page = await ctx.newPage();
const a = await ctx.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
const m = /fy_session=([^;]+)/.exec(a.headers()['set-cookie'] ?? '');
if (m) await ctx.addCookies([{ name:'fy_session', value:m[1], domain:'127.0.0.1', path:'/', httpOnly:true, sameSite:'Lax' }]);
await page.goto(`${BASE}/workbench`);
await page.waitForTimeout(1500);
console.log(JSON.stringify(await page.evaluate(() => {
  const pick = (sel) => { const el = document.querySelector(sel); if (!el) return { sel, missing: true };
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    return { sel, w: Math.round(r.width), h: Math.round(r.height), display: cs.display, flexDirection: cs.flexDirection, position: cs.position, width: cs.width, minWidth: cs.minWidth, overflowX: cs.overflowX }; };
  return { mq860: window.matchMedia('(max-width: 860px)').matches, mq560: window.matchMedia('(max-width: 560px)').matches,
    nodes: ['.app','.sidebar','.main','.nav','.wb-terminal-body','.wb-termdock','.wb-termdock-foot'].map(pick) };
}), null, 2));
await b.close();
