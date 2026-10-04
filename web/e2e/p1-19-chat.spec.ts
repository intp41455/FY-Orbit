import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * P1-19 Chat 调试预览 E2E：选模板 + 绑工具发起 Chat →
 * SSE 流式回复中真实触发工具调用 → trace 面板留痕。
 * 运行前提：后端 8101（E2E_LOCAL_TOKEN 匹配）、vite dev 5199。
 * 归档：evidence/p1-19-chat/（截图 + trace JSON；video 由配置 outputDir 输出）。
 */
const LOCAL_TOKEN = process.env.E2E_LOCAL_TOKEN ?? '';
const EVIDENCE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../evidence/p1-19-chat');

const TEMPLATE_NAME = 'chat.debug.p1x19';
const TOOL_NAME = 'add';

async function loginViaDevToken(page: Page): Promise<string> {
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
    (document.head ?? document.documentElement).appendChild(meta);
  }, body.csrf_token);
  return body.csrf_token as string;
}

test('选模板+勾工具发起 Chat：流式回复 + 工具真实执行 + trace 面板', async ({ page }) => {
  const csrf = await loginViaDevToken(page);
  const postHeaders = { 'X-CSRF-Token': csrf };

  // 1) 种子数据：注册工具（幂等）+ 创建/审批/启用模板（governance 流程）
  const reg = await page.request.post('/api/tools/register', {
    data: {
      name: TOOL_NAME,
      description: '两数相加',
      parameters: {
        type: 'object',
        properties: { a: { type: 'number' }, b: { type: 'number' } },
        required: ['a', 'b'],
      },
      entry: { type: 'builtin', executor: 'add' },
    },
    headers: postHeaders,
  });
  if (!reg.ok()) throw new Error(`tool register failed: HTTP ${reg.status()} ${await reg.text()}`);

  const discover = await page.request.get('/api/tools/discover').then((r) => r.json());
  expect(discover.tools.map((t: { name: string }) => t.name)).toContain(TOOL_NAME);

  // 模板已存在且启用时跳过治理流程（幂等重跑）
  const existing = await page.request.get(`/api/prompts/${TEMPLATE_NAME}`);
  const isActive = existing.ok() ? (await existing.json()).is_active : false;

  if (!isActive) {
    const created = await page.request.post('/api/prompts', {
      data: {
        name: TEMPLATE_NAME,
        content: '你是 {{topic}} 领域的调试助手，请调用所需工具完成任务。',
        variables_schema: { topic: { type: 'str', required: true } },
        scope: 'platform',
        description: 'P1-19 E2E 联调模板',
      },
      headers: postHeaders,
    });
    if (!created.ok() && created.status() !== 409) {
      throw new Error(`prompt create failed: HTTP ${created.status()} ${await created.text()}`);
    }

    const stage = await page.request.post(`/api/prompts/${TEMPLATE_NAME}/stage`, {
      data: {
        content: '你是 {{topic}} 领域的调试助手，请调用所需工具完成任务。',
        variables_schema: { topic: { type: 'str', required: true } },
        reason: 'P1-19 E2E 种子模板',
      },
      headers: postHeaders,
    }).then((r) => r.json());
    await page.request.post(`/api/proposals/${stage.proposal_id}/decision`, {
      data: { digest: stage.digest, decision: 'approve' },
      headers: postHeaders,
    });
    const activate = await page.request.put(`/api/prompts/${TEMPLATE_NAME}/activate`, {
      data: { version: 1, reason: 'P1-19 E2E 启用' },
      headers: postHeaders,
    }).then((r) => r.json());
    await page.request.post(`/api/proposals/${activate.proposal_id}/decision`, {
      data: { digest: activate.digest, decision: 'approve' },
      headers: postHeaders,
    });
    const applied = await page.request.post(`/api/prompts/${TEMPLATE_NAME}/apply`, {
      data: { proposal_id: activate.proposal_id, digest: activate.digest },
      headers: postHeaders,
    });
    if (!applied.ok()) throw new Error(`prompt apply failed: HTTP ${applied.status()} ${await applied.text()}`);
  }

  // 2) P1-01 §4 衔接：详情页「用此模板发起试跑」深链进入 Chat 调试页并自动填充
  const variables = encodeURIComponent(JSON.stringify({ topic: '心理画像' }));
  await page.goto(`/chat-debug?template=${TEMPLATE_NAME}&version=1&variables=${variables}`);
  await expect(page.getByTestId('chat-debug-root')).toBeVisible();
  await expect(page.getByTestId('template-select')).toHaveValue(TEMPLATE_NAME);
  await expect(page.getByTestId('var-input-topic')).toHaveValue('心理画像');

  // 3) 勾选工具 + 发送触发消息
  await page.getByTestId(`tool-check-${TOOL_NAME}`).check();
  await page.getByTestId('chat-input').fill('调用 add 计算 3 和 4 的和');
  await page.getByTestId('chat-send').click();

  // 4) SSE 流式回复渲染（stub 文本 + 工具续流段）
  const assistant = page.getByTestId('chat-msg-assistant');
  await expect(assistant).toContainText('Deterministic streaming stub', { timeout: 30_000 });
  await expect(assistant).toContainText('已真实执行', { timeout: 30_000 });

  // 5) trace 面板：本轮触发了工具 add（参数/结果）
  const entry = page.getByTestId('chat-trace-entry');
  await expect(entry).toContainText(TOOL_NAME);
  await expect(entry).toContainText('{"a":3,"b":4}');
  await expect(entry).toContainText('{"sum":7}');
  await expect(entry).toContainText('已真实执行');

  // 6) 模板徽标（审计链：name/version/variables_hash）
  await expect(page.getByTestId('badge-template')).toContainText(`${TEMPLATE_NAME} v1`);
  await expect(page.locator('[data-testid="chat-debug-badges"]').getByText('variables_hash')).toBeVisible();

  // 7) 归档：截图 + trace JSON（UI trace + 工具调用 receipts + 渲染日志）
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({ path: path.join(EVIDENCE_DIR, 'chat-debug-run.png'), fullPage: true });

  const uiTrace = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="chat-trace-panel"]');
    return panel ? panel.textContent : '';
  });
  const calls = await page.request.get('/api/tools/calls?limit=50').then((r) => r.json());
  const addReceipts = calls.calls.filter(
    (c: { tool: string; arguments: { a?: number } }) =>
      c.tool === TOOL_NAME && c.arguments?.a === 3,
  );
  expect(addReceipts.length).toBeGreaterThan(0);

  const logs = await page.request.get(`/api/prompts/${TEMPLATE_NAME}/logs`).then((r) => r.json());

  fs.writeFileSync(path.join(EVIDENCE_DIR, 'trace.json'), JSON.stringify({
    captured_at: new Date().toISOString(),
    template: TEMPLATE_NAME,
    tools: [TOOL_NAME],
    user_message: '调用 add 计算 3 和 4 的和',
    ui_trace_text: uiTrace,
    tool_receipts: addReceipts,
    prompt_render_logs: logs,
  }, null, 2), 'utf-8');
});
