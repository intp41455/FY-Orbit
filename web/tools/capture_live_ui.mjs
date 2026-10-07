import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const BASE = 'http://127.0.0.1:8000';
const OUT_DIR = path.resolve('..', 'assets', 'screenshots');
fs.mkdirSync(OUT_DIR, { recursive: true });

console.log('==> Starting Live UI Capture from', BASE);
const browser = await chromium.launch({ headless: true });

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1.5,
  });

  // Login via dev-token
  const page = await context.newPage();
  try {
    const authResp = await context.request.post(`${BASE}/auth/local/dev-token`, {
      data: { token: 'dev-token-secret' },
    });
    console.log('Auth status:', authResp.status());
  } catch (err) {
    console.log('Dev-token note:', err.message);
  }

  const targets = [
    { url: `${BASE}/landing.html`, file: '00-official-landing-hero.png', wait: 2000 },
    { url: `${BASE}/workbench`, file: '01-workbench-ide-monaco.png', wait: 2500 },
    { url: `${BASE}/dsl-canvas`, file: '02-workflow-dsl-canvas.png', wait: 2500 },
    { url: `${BASE}/kanban`, file: '03-agile-kanban-gantt.png', wait: 2000 },
    { url: `${BASE}/canvas`, file: '04-multi-agent-team-canvas.png', wait: 2000 },
    { url: `${BASE}/observability`, file: '05-observability-cost-dashboard.png', wait: 2000 },
    { url: `${BASE}/knowledge`, file: '06-knowledge-3d-galaxy.png', wait: 2500 },
    { url: `${BASE}/cabin`, file: '07-personal-cabin-digital-space.png', wait: 2500 },
  ];

  for (const t of targets) {
    try {
      console.log(`Capturing: ${t.url} -> ${t.file}`);
      await page.goto(t.url, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(t.wait);
      const outPath = path.join(OUT_DIR, t.file);
      await page.screenshot({ path: outPath, fullPage: false });
      const sz = fs.statSync(outPath).size;
      console.log(` -> Saved ${t.file} (${Math.round(sz / 1024)} KB)`);
    } catch (e) {
      console.error(`Failed on ${t.url}:`, e.message);
    }
  }

  await context.close();
} finally {
  await browser.close();
}

console.log('==> Live UI capture complete!');
