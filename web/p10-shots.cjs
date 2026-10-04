/**
 * P10 · 三档截图脚本（1280 / 1024 / 768）。
 * 依赖本地真实服务：后端 8030（FY_LOCAL_TOKEN=p10-local-token）+ vite dev 5173。
 * 产物：evidence/P10/<页面>-<宽度>.png（人工 review diff 用，非自动断言）。
 */
const { chromium } = require('@playwright/test');

const BASE = 'http://127.0.0.1:5188';
const TOKEN = 'p10-local-token';
const WIDTHS = [1280, 1024, 768];
const PAGES = [
  { path: '/workbench', name: 'workbench' },
  { path: '/plugins', name: 'plugins' },
  { path: '/knowledge', name: 'knowledge' },
  { path: '/dsl-canvas', name: 'dsl-canvas' },
];

(async () => {
  const browser = await chromium.launch();
  for (const width of WIDTHS) {
    const context = await browser.newContext({
      viewport: { width, height: 800 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    // 与 e2e/app.spec.ts 相同的登录方式：dev-token + HttpOnly cookie 上提。
    const res = await page.request.post(`${BASE}/auth/local/dev-token`, {
      data: { token: TOKEN },
    });
    if (!res.ok()) {
      throw new Error(`dev-token login failed: HTTP ${res.status()}`);
    }
    const setCookie = res.headersArray().find((h) => h.name.toLowerCase() === 'set-cookie');
    const raw = setCookie ? setCookie.value : '';
    const pair = raw.split(';')[0];
    const [cookieName] = pair.split('=');
    await context.addCookies([
      { name: cookieName, value: pair.slice(cookieName.length + 1), domain: '127.0.0.1', path: '/' },
    ]);
    for (const target of PAGES) {
      await page.goto(`${BASE}${target.path}`, { waitUntil: 'networkidle', timeout: 60000 })
        .catch(() => page.goto(`${BASE}${target.path}`, { waitUntil: 'load', timeout: 60000 }));
      await page.waitForTimeout(1200); // 让路由/懒加载稳定
      const out = `../evidence/P10/${target.name}-${width}.png`;
      await page.screenshot({ path: out, fullPage: false });
      console.log('SHOT', out);
    }
    await context.close();
  }
  await browser.close();
  console.log('P10 SCREENSHOTS DONE');
})();
