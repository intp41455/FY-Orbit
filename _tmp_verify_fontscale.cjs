
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const out = {};
  try {
    await page.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1500);

    out.indicatorCount = await page.locator('[data-testid="font-scale-indicator"]').count();
    out.valueText = await page.locator('[data-testid="font-scale-value"]').first().textContent().catch(() => null);

    const before = await page.locator('[data-testid="font-scale-value"]').first().textContent();
    await page.locator('[data-testid="font-scale-plus"]').first().click();
    await page.waitForTimeout(500);
    const after = await page.locator('[data-testid="font-scale-value"]').first().textContent();
    out.plusWorks = { before, after, changed: before !== after };
    out.zoomAfterPlus = await page.evaluate(() => document.getElementById('root').style.zoom);

    await page.keyboard.press('Control+0');
    await page.waitForTimeout(500);
    out.afterReset = await page.locator('[data-testid="font-scale-value"]').first().textContent();
    out.zoomAfterReset = await page.evaluate(() => document.getElementById('root').style.zoom);

    out.identityOverflow = await page.evaluate(() => {
      const el = document.querySelector('.sidebar-foot .identity');
      if (!el) return 'no-element';
      const span = el.querySelector('span');
      if (!span) return 'no-span';
      return { scrollW: span.scrollWidth, clientW: span.clientWidth, overflows: span.scrollWidth > span.clientWidth + 1 };
    });

    await page.goto('http://127.0.0.1:8000/avatar-workshop', { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(1800);
    out.workshopStage = await page.locator('[data-testid="avatar-ni-stage"]').count();
    out.previewEndpoint = await page.evaluate(async () => {
      try {
        const r = await fetch('/api/avatar/preview', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({overrides:{}}) });
        return { status: r.status };
      } catch (e) { return { error: String(e) }; }
    });
  } catch (e) {
    out.error = String(e).slice(0, 400);
  }
  await browser.close();
  console.log(JSON.stringify(out, null, 2));
})();

