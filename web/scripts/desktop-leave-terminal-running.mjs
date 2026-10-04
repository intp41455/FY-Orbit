// Start an interactive terminal in the real desktop window and leave a
// long-running child process (ping -t) alive, so the shutdown test can prove
// that closing the app window kills the backend AND the terminal process tree.
import { chromium } from '@playwright/test';

const CDP = process.env.FY_CDP_URL ?? 'http://127.0.0.1:9333';
const browser = await chromium.connectOverCDP(CDP);
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? (await ctx.newPage());
page.setDefaultTimeout(20000);

await page.goto('http://127.0.0.1:8088/workbench', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('.workspace-chip', { timeout: 20000 });
await page.locator('.terminal-pane button', { hasText: '启动终端' }).click();
await page.waitForSelector('.terminal-input-row input', { timeout: 20000 });
const input = page.locator('.terminal-input-row input');
await input.fill('ping -t 127.0.0.1');
await input.press('Enter');
await page.waitForFunction(
  () => (document.querySelector('.terminal-output')?.textContent ?? '').includes('TTL='),
  undefined,
  { timeout: 20000 },
);
const state = await page.locator('.terminal-pane .row .muted').first().textContent();
console.log('terminal state:', state?.trim());
await page.screenshot({ path: '../evidence/acceptance-2026-10-02/desktop/12-terminal-left-running.png', timeout: 60000 });
await browser.close();