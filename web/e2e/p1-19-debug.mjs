// 临时调试脚本：抓取浏览器发出的 /api/streaming/chat 请求头
import { chromium } from '@playwright/test';

const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5199';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();

const res = await page.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
const body = await res.json();
console.log('csrf:', body.csrf_token);

page.on('request', (req) => {
  if (req.url().includes('/api/streaming')) {
    console.log('REQUEST', req.method(), req.url());
    console.log('HEADERS', JSON.stringify(req.headers(), null, 2));
  }
});
page.on('response', (resp) => {
  if (resp.url().includes('/api/streaming')) {
    console.log('RESPONSE', resp.status(), resp.url());
  }
});

await page.addInitScript((csrf) => {
  const meta = document.createElement('meta');
  meta.name = 'csrf-token';
  meta.content = csrf;
  document.head.appendChild(meta);
}, body.csrf_token);

await page.goto(`${BASE}/chat-debug`);
await page.waitForSelector('[data-testid="chat-send"]', { timeout: 15000 });
await page.fill('[data-testid="chat-input"]', '调用 add 计算 3 和 4 的和');
await page.click('[data-testid="chat-send"]');
await page.waitForTimeout(6000);
const alert = await page.locator('[role="alert"]').allTextContents();
const metaNow = await page.evaluate(() => document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ?? 'NO_META');
console.log('ALERT:', alert);
console.log('META_NOW:', metaNow);
await browser.close();
