import { chromium } from 'playwright';
import path from 'path';

const outDir = 'C:/Users/intpj/.gemini/antigravity/brain/edc780c5-b43d-43e2-bcb9-e41881477a83';

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });
  console.log('Obtaining session from http://127.0.0.1:8030/auth/guest...');
  const guestRes = await fetch('http://127.0.0.1:8030/auth/guest', { method: 'POST' });
  const cookieHeader = guestRes.headers.get('set-cookie');
  console.log('Guest auth status:', guestRes.status, 'Cookie:', cookieHeader);

  if (cookieHeader) {
    const match = cookieHeader.match(/fy_session=([^;]+)/);
    if (match) {
      await context.addCookies([{
        name: 'fy_session',
        value: match[1],
        domain: '127.0.0.1',
        path: '/',
        httpOnly: true,
        sameSite: 'Lax',
      }]);
      console.log('Session cookie injected successfully!');
    }
  }

  const page = await context.newPage();
  page.on('response', (r) => {
    if (r.status() >= 400) console.log('[HTTP FAIL]', r.status(), r.url());
  });
  page.on('console', (msg) => console.log('[BROWSER LOG]', msg.type(), msg.text()));
  page.on('pageerror', (err) => console.log('[BROWSER ERR]', err));

  // 模拟已登录本地会话与画像（严格限定 /auth/ 与 /api/ 接口，避免误拦 /src/api/*.ts 源码）
  await page.route(/^http:\/\/127\.0\.0\.1:5178\/auth\//, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        subject_type: 'owner',
        owner_id: 'owner_live_demo',
        is_guest: true,
        plan: 'free',
        csrf_token: 'mock_csrf_token_123',
      }),
    });
  });

  await page.route(/^http:\/\/127\.0\.0\.1:5178\/api\//, async (route) => {
    const url = route.request().url();
    if (url.includes('/api/conversations')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    } else if (url.includes('/api/avatar/house')) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":{"code":"not_found"}}' });
    } else if (url.includes('/api/house')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"layout":null}' });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
  });

  console.log('Navigating to http://127.0.0.1:5178/chat...');
  await page.goto('http://127.0.0.1:5178/chat', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  console.log('Current URL after /chat:', page.url());

  const personalBtn = page.locator('button:has-text("个人空间")');
  if (await personalBtn.isVisible()) {
    console.log('Clicking personal space tab...');
    await personalBtn.click();
    await page.waitForTimeout(500);
  }

  const cabinLink = page.locator('a[href="/cabin"]');
  console.log('Cabin link count:', await cabinLink.count());
  if (await cabinLink.first().isVisible()) {
    console.log('Clicking cabin nav link in sidebar...');
    await cabinLink.first().click();
    await page.waitForTimeout(3000);
  }
  console.log('Current URL after clicking /cabin:', page.url());
  const errorText = await page.locator('.notice, [role="alert"]').allInnerTexts();
  console.log('Any notices or alerts on page:', errorText);

  // 1. 默认室外（老林子 + 新版抽屉折叠UI）
  await page.screenshot({ path: path.join(outDir, 'cabin_live_outdoor_forest.png') });
  console.log('Captured cabin_live_outdoor_forest.png');

  // 2. 检查后花园（全屏瓦片与地表向上延伸，无半屏截断）
  const gardenBtn = page.locator('[data-testid="cabin-ni-map-garden"]');
  if (await gardenBtn.isVisible()) {
    await gardenBtn.click();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(outDir, 'cabin_live_garden_fullscreen.png') });
    console.log('Captured cabin_live_garden_fullscreen.png');
  }

  // 3. 检查黄金田野
  const fieldBtn = page.locator('[data-testid="cabin-ni-map-golden_field"], [data-testid="cabin-ni-map-field"]');
  if (await fieldBtn.first().isVisible()) {
    await fieldBtn.first().click();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(outDir, 'cabin_live_golden_field.png') });
    console.log('Captured cabin_live_golden_field.png');
  }

  // 4. 打开 NPC 面板，展示专属村民名册与好感
  const npcTab = page.locator('[data-testid="cabin-tab-npc"]');
  if (await npcTab.isVisible()) {
    await npcTab.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(outDir, 'cabin_live_exclusive_npc.png') });
    console.log('Captured cabin_live_exclusive_npc.png');
  }

  // 5. 进屋布置（室内温馨木屋，小人与小羊同步换装）
  const decorateTab = page.locator('[data-testid="cabin-tab-decorate"]');
  if (await decorateTab.isVisible()) {
    await decorateTab.click();
    await page.waitForTimeout(300);
  }
  const enterIndoorBtn = page.locator('[data-testid="cabin-ni-enter-indoor"]');
  if (await enterIndoorBtn.isVisible()) {
    await enterIndoorBtn.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, 'cabin_live_indoor_warm.png') });
    console.log('Captured cabin_live_indoor_warm.png');

    // 退出室内，回到大世界
    try {
      await page.evaluate(() => {
        const btn = document.querySelector('[data-testid="cabin-exit-indoor"]') ||
                    Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.includes('出门回院子'));
        if (btn) btn.click();
      });
      await page.waitForTimeout(1200);
    } catch (e) {
      console.log('Exit indoor failed non-blocking:', e);
    }
  }

  // 6. 验证键盘 WASD 纵深探索与跳跃
  const canvasEl = page.locator('canvas').first();
  if (await canvasEl.isVisible()) {
    await canvasEl.click({ position: { x: 300, y: 300 } });
    await page.waitForTimeout(300);
  }
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('KeyW');
    await page.waitForTimeout(100);
  }
  await page.keyboard.press('Space');
  await page.waitForTimeout(80);
  await page.screenshot({ path: path.join(outDir, 'cabin_live_depth_keyboard.png') });
  console.log('Captured cabin_live_depth_keyboard.png');

  // 7. 离开小屋，进入工作台验证【桌面宠物伴侣 Desk Pet Companion】
  console.log('Navigating to http://127.0.0.1:5178/chat ...');
  await page.goto('http://127.0.0.1:5178/chat', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  // 检查桌宠组件并点击触发气泡
  const deskPetActor = page.locator('[data-testid="desk-pet-actor"], [data-testid="desk-pet-companion"]');
  if (await deskPetActor.first().isVisible()) {
    console.log('Clicking desk-pet-actor to trigger speech bubble and interaction menu...');
    await deskPetActor.first().click();
    await page.waitForTimeout(1000);
  }
  await page.screenshot({ path: path.join(outDir, 'cabin_live_deskpet_workbench.png') });
  console.log('Captured cabin_live_deskpet_workbench.png');

  await browser.close();
  console.log('All screenshots captured successfully!');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
