// Probe: why does .cv-topbar intercept clicks on a .cv-team-tab?
//
// Reads the live DOM instead of guessing from CSS. Reports, for the target tab:
//   - bounding boxes of the tab, the scrolling .cv-team-tabs, and .cv-topbar
//   - the tab's scroll offset inside .cv-team-tabs
//   - what document.elementFromPoint() actually returns at the tab's centre
//
// Usage: node tools/diag-teamtab.mjs http://127.0.0.1:4173
import { chromium } from 'playwright';

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173';
const TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const res = await page.request.post(`${BASE}/auth/local/dev-token`, { data: { token: TOKEN } });
const setCookie = res.headers()['set-cookie'];
const m = /fy_session=([^;]+)/.exec(setCookie ?? '');
if (m) {
  await page.context().addCookies([
    { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
}

await page.goto(`${BASE}/canvas`);
await page.waitForSelector('.tabs-container.cv-team-tabs', { timeout: 20_000 }).catch(() => {});
await page.waitForTimeout(1200);

const report = await page.evaluate(() => {
  const out = {};
  const tabs = [...document.querySelectorAll('.tabs-container.cv-team-tabs')];
  out.tabContainers = tabs.length;

  const teamTabs = document.querySelector('.tabs-container.cv-team-tabs');
  if (!teamTabs) return { ...out, error: 'no .cv-team-tabs' };

  const btns = [...teamTabs.querySelectorAll('button')];
  out.teamCount = btns.length;
  out.tabNames = btns.slice(0, 8).map((b) => b.getAttribute('aria-label'));

  const r = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { x: +b.x.toFixed(1), y: +b.y.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1) };
  };

  const topbar = document.querySelector('.cv-topbar');
  out.topbar = r(topbar);
  out.container = r(teamTabs);
  out.containerScroll = { scrollLeft: teamTabs.scrollLeft, scrollWidth: teamTabs.scrollWidth, clientWidth: teamTabs.clientWidth };

  const cs = getComputedStyle(teamTabs);
  out.containerStyle = { overflowX: cs.overflowX, position: cs.position, zIndex: cs.zIndex };
  const csTop = topbar ? getComputedStyle(topbar) : null;
  out.topbarStyle = csTop
    ? { position: csTop.position, zIndex: csTop.zIndex, overflow: csTop.overflow, height: csTop.height }
    : null;

  // Probe EVERY tab: is its centre actually hittable?
  out.perTab = btns.map((b, i) => {
    const bb = b.getBoundingClientRect();
    const cx = bb.x + bb.width / 2;
    const cy = bb.y + bb.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    return {
      i,
      label: (b.getAttribute('aria-label') ?? '').slice(0, 28),
      box: r(b),
      inViewport: bb.top >= 0 && bb.bottom <= window.innerHeight && bb.left >= 0 && bb.right <= window.innerWidth,
      hitIsTab: hit === b || b.contains(hit),
      hitTag: hit ? `${hit.tagName.toLowerCase()}.${(hit.className || '').toString().split(' ').slice(0, 2).join('.')}` : null,
    };
  });
  return out;
});

console.log(JSON.stringify(report, null, 2));
await browser.close();
