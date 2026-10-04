import { test, expect, type Page } from '@playwright/test';

/**
 * P1-18 受限 DSL 画布 E2E：画布操作生成 DSL → 真实执行 → 逐步日志回显。
 * 运行前提：后端 8098（E2E_LOCAL_TOKEN 匹配）、前端 dev 5196。
 */
const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';

async function loginViaDevToken(page: Page): Promise<void> {
  const res = await page.request.post('/auth/local/dev-token', { data: { token: LOCAL_TOKEN } });
  if (!res.ok()) throw new Error(`dev-token login failed: HTTP ${res.status()}`);
  const body = await res.json();
  const setCookie = res.headers()['set-cookie'] ?? '';
  const m = setCookie.match(/fy_session=([^;]+)/);
  if (m) {
    await page.context().addCookies([
      { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
    ]);
  }
  await page.addInitScript((csrf) => {
    const meta = document.createElement('meta');
    meta.name = 'csrf-token';
    meta.content = csrf as string;
    document.head.appendChild(meta);
  }, body.csrf_token);
}

test('画布搭三节点流 → 生成 DSL → 真实执行输出符合模板预期', async ({ page }) => {
  await loginViaDevToken(page);
  await page.goto('/dsl-canvas');
  await expect(page.getByTestId('dsl-canvas-root')).toBeVisible();

  // 1) 节点面板添加三类节点（拖拽路径由 HTML5 dnd 提供，此处用点击等价入口）
  await page.getByTestId('palette-input').click();
  await page.getByTestId('palette-transform').click();
  await page.getByTestId('palette-output').click();

  // 2) 连线：input → transform → output（点击出/入端口）
  const ids = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll('[data-testid^="dsl-node-"]'));
    return nodes.map((n) => (n as HTMLElement).dataset.testid!.replace('dsl-node-', ''));
  });
  const inputId = ids.find((i) => i.startsWith('input'))!;
  const transformId = ids.find((i) => i.startsWith('transform'))!;
  const outputId = ids.find((i) => i.startsWith('output'))!;
  await page.getByTestId(`port-out-${inputId}`).click();
  await page.getByTestId(`port-in-${transformId}`).click();
  await page.getByTestId(`port-out-${transformId}`).click();
  await page.getByTestId(`port-in-${outputId}`).click();

  // 3) 生成 DSL：实时 JSON 视图，不含布局坐标
  await page.getByTestId('dsl-generate').click();
  const dslText = await page.getByTestId('dsl-json').textContent();
  expect(dslText).toBeTruthy();
  const dsl = JSON.parse(dslText!);
  expect(dsl.version).toBe('1');
  expect(dsl.edges).toEqual([
    { from: inputId, to: transformId },
    { from: transformId, to: outputId },
  ]);
  expect(JSON.stringify(dsl)).not.toContain('"x"'); // layout 与模型分离

  // 4) 执行：调后端真实执行，输出与逐步日志回显
  await page.getByTestId('dsl-run').click();
  await expect(page.getByTestId('dsl-run-output')).toContainText('你好，张三！你今年 34 岁。');
  const logs = page.getByTestId('dsl-run-logs');
  await expect(logs).toContainText(transformId);
  await expect(logs).toContainText('succeeded');
  await expect(page.locator('[data-testid="dsl-run-result"] .badge.accent').first())
    .toContainText('succeeded');

  await page.screenshot({
    path: 'e2e/screenshots-p1-18/dsl-canvas-run.png', fullPage: true,
  });
});
