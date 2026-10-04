import { test, expect, type Page } from '@playwright/test';

/**
 * P1-02「A 布局骨架」走查：终端停靠底部、左右分区可拖拽生效、终端可折叠、
 * 布局状态会话内保持。三张截图（默认布局 / 拖拽后 / 终端折叠）落盘 e2e/screenshots-p1-02/。
 *
 * 认证与 app.spec.ts 相同：loopback dev-token（E2E_LOCAL_TOKEN）。
 * 布局骨架在未选择工作区时也渲染（占位内容），因此不依赖种子工作区。
 */

const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const SHOT_DIR = 'e2e/screenshots-p1-02';

function extractCookie(setCookie: string | undefined, name: string): string | null {
  if (!setCookie) return null;
  for (const part of setCookie.split(/,(?=[^;]+=)/)) {
    const seg = part.trim();
    if (seg.startsWith(`${name}=`)) {
      return seg.slice(name.length + 1).split(';')[0];
    }
  }
  return null;
}

async function loginViaDevToken(page: Page): Promise<void> {
  const res = await page.request.post('/auth/local/dev-token', {
    data: { token: LOCAL_TOKEN },
  });
  if (!res.ok()) {
    throw new Error(`dev-token login failed: HTTP ${res.status()}`);
  }
  const session = extractCookie(res.headers()['set-cookie'], 'fy_session');
  if (session) {
    await page.context().addCookies([
      { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
}

test.describe('P1-02 A 布局骨架', () => {
  test.skip(!LOCAL_TOKEN, 'E2E_LOCAL_TOKEN not set; skipping layout walkthrough');

  test('terminal docked bottom, split draggable, terminal collapsible', async ({ page }) => {
    await loginViaDevToken(page);
    await page.goto('/workbench');

    const layout = page.getByTestId('wb-layout-a');
    await expect(layout).toBeVisible();
    const dock = page.getByTestId('wb-terminal-dock');
    await expect(dock).toBeVisible();

    // 1) 终端停靠底部：dock 底边与布局容器底边重合（容差 8px）。
    const layoutBox = (await layout.boundingBox())!;
    const dockBox = (await dock.boundingBox())!;
    const bottomGap = Math.abs(
      layoutBox.y + layoutBox.height - (dockBox.y + dockBox.height),
    );
    expect(bottomGap).toBeLessThan(8);

    await page.screenshot({ path: `${SHOT_DIR}/01-default-layout.png`, fullPage: true });

    // 2) 左右分区拖拽生效：把分隔手柄向左拖 160px，左区宽度相应变小。
    const zoneLeft = page.getByTestId('wb-zone-left');
    const before = (await zoneLeft.boundingBox())!;
    const handle = page.getByTestId('wb-split-handle');
    await expect(handle).toBeVisible();
    const handleBox = (await handle.boundingBox())!;
    await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
    await page.mouse.down();
    await page.mouse.move(handleBox.x - 160, handleBox.y + 40, { steps: 12 });
    await page.mouse.up();
    const after = (await zoneLeft.boundingBox())!;
    expect(after.width).toBeLessThan(before.width - 80);

    await page.screenshot({ path: `${SHOT_DIR}/02-after-drag.png`, fullPage: true });

    // 3) 终端折叠展开：折叠后 dock 只剩标题条，高度显著变小。
    const toggle = page.getByTestId('wb-terminal-toggle');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByTestId('wb-terminal-resize')).toHaveCount(0);
    const collapsedBox = (await dock.boundingBox())!;
    expect(collapsedBox.height).toBeLessThan(dockBox.height / 2);

    // 4) 布局状态会话内保持：路由往返后折叠态与分区宽度不丢。
    await page.getByRole('link', { name: '设置与数据' }).click();
    await expect(page).toHaveURL(/\/settings/);
    await page.getByRole('link', { name: '任务工作台' }).click();
    await expect(page).toHaveURL(/\/workbench/);
    await expect(page.getByTestId('wb-terminal-toggle')).toHaveAttribute('aria-expanded', 'false');
    const zoneLeftAfterNav = (await page.getByTestId('wb-zone-left').boundingBox())!;
    expect(Math.abs(zoneLeftAfterNav.width - after.width)).toBeLessThan(2);

    await page.screenshot({ path: `${SHOT_DIR}/03-terminal-collapsed.png`, fullPage: true });
  });
});
