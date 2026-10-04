# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: p1-19-chat.spec.ts >> 选模板+勾工具发起 Chat：流式回复 + 工具真实执行 + trace 面板
- Location: e2e\p1-19-chat.spec.ts:38:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: expect(locator).toContainText(expected) failed

Locator: getByTestId('chat-msg-assistant')
Expected substring: "Deterministic streaming stub"
Error: element(s) not found

Call log:
  - Expect "toContainText" getByTestId('chat-msg-assistant') with timeout 30000ms
  - waiting for getByTestId('chat-msg-assistant')
  - Test timeout of 30000ms exceeded.

```

```yaml
- complementary:
  - strong: AI 协作工作台
  - text: 让 AI 团队，把事情做完
  - group "空间切换":
    - button "工作台空间" [pressed]
    - button "个人空间"
  - text: 工作台功能导航
  - navigation "主导航":
    - link "任务工作台 指挥 · 终端 · 验收":
      - /url: /workbench
    - link "协作画布 内部团队 · 逐成员选模型":
      - /url: /canvas
    - link "DSL 画布 受限动词 · 数据流执行":
      - /url: /dsl-canvas
    - link "Chat 调试 模板 · 工具 · 流式联动":
      - /url: /chat-debug
    - link "子 Agent 派发 Task 协议 · 独立验收":
      - /url: /agent-dispatch
    - link "Agent 与技能 MCP · 经验沉淀":
      - /url: /skills
    - link "审批中心 提案与授权":
      - /url: /approvals
    - link "设置与数据 同步 · 权限":
      - /url: /settings
  - link "权限与沙箱隔离 受控":
    - /url: /settings
  - link "预算与用量 查看":
    - /url: /settings
  - text: owner
  - button "登出"
- main:
  - heading "Chat 调试预览" [level=2]
  - text: 工具 × 1
  - strong: 配置
  - text: 提示词模板
  - combobox "提示词模板":
    - option "（不使用模板）"
    - option "chat.debug.p1x19 v1" [selected]
  - group "变量填值":
    - text: 变量填值 topic * (str)
    - textbox "topic * (str)": 心理画像
  - group "绑定工具（2）":
    - text: 绑定工具（2）
    - checkbox "add" [checked]
    - text: add
    - checkbox "echo"
    - text: echo
  - text: 模型
  - textbox "模型": default
  - text: 调用 add 计算 3 和 4 的和
  - textbox "调试消息":
    - /placeholder: 例如：调用 add 计算 3 和 4 的和
  - button "发送" [disabled]
  - strong: 工具调用 trace
  - text: 本轮未触发工具调用。
  - alert: Missing or invalid CSRF token
```

# Test source

```ts
  21  |   const body = await res.json();
  22  |   const setCookie = res.headers()['set-cookie'] ?? '';
  23  |   const m = setCookie.match(/fy_session=([^;]+)/);
  24  |   if (m) {
  25  |     await page.context().addCookies([
  26  |       { name: 'fy_session', value: m[1], domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  27  |     ]);
  28  |   }
  29  |   await page.addInitScript((csrf) => {
  30  |     const meta = document.createElement('meta');
  31  |     meta.name = 'csrf-token';
  32  |     meta.content = csrf as string;
  33  |     document.head.appendChild(meta);
  34  |   }, body.csrf_token);
  35  |   return body.csrf_token as string;
  36  | }
  37  | 
  38  | test('选模板+勾工具发起 Chat：流式回复 + 工具真实执行 + trace 面板', async ({ page }) => {
  39  |   const csrf = await loginViaDevToken(page);
  40  |   const postHeaders = { 'X-CSRF-Token': csrf };
  41  | 
  42  |   // 1) 种子数据：注册工具（幂等）+ 创建/审批/启用模板（governance 流程）
  43  |   const reg = await page.request.post('/api/tools/register', {
  44  |     data: {
  45  |       name: TOOL_NAME,
  46  |       description: '两数相加',
  47  |       parameters: {
  48  |         type: 'object',
  49  |         properties: { a: { type: 'number' }, b: { type: 'number' } },
  50  |         required: ['a', 'b'],
  51  |       },
  52  |       entry: { type: 'builtin', executor: 'add' },
  53  |     },
  54  |     headers: postHeaders,
  55  |   });
  56  |   if (!reg.ok()) throw new Error(`tool register failed: HTTP ${reg.status()} ${await reg.text()}`);
  57  | 
  58  |   const discover = await page.request.get('/api/tools/discover').then((r) => r.json());
  59  |   expect(discover.tools.map((t: { name: string }) => t.name)).toContain(TOOL_NAME);
  60  | 
  61  |   // 模板已存在且启用时跳过治理流程（幂等重跑）
  62  |   const existing = await page.request.get(`/api/prompts/${TEMPLATE_NAME}`);
  63  |   const isActive = existing.ok() ? (await existing.json()).is_active : false;
  64  | 
  65  |   if (!isActive) {
  66  |     const created = await page.request.post('/api/prompts', {
  67  |       data: {
  68  |         name: TEMPLATE_NAME,
  69  |         content: '你是 {{topic}} 领域的调试助手，请调用所需工具完成任务。',
  70  |         variables_schema: { topic: { type: 'str', required: true } },
  71  |         scope: 'platform',
  72  |         description: 'P1-19 E2E 联调模板',
  73  |       },
  74  |       headers: postHeaders,
  75  |     });
  76  |     if (!created.ok() && created.status() !== 409) {
  77  |       throw new Error(`prompt create failed: HTTP ${created.status()} ${await created.text()}`);
  78  |     }
  79  | 
  80  |     const stage = await page.request.post(`/api/prompts/${TEMPLATE_NAME}/stage`, {
  81  |       data: {
  82  |         content: '你是 {{topic}} 领域的调试助手，请调用所需工具完成任务。',
  83  |         variables_schema: { topic: { type: 'str', required: true } },
  84  |         reason: 'P1-19 E2E 种子模板',
  85  |       },
  86  |       headers: postHeaders,
  87  |     }).then((r) => r.json());
  88  |     await page.request.post(`/api/proposals/${stage.proposal_id}/decision`, {
  89  |       data: { digest: stage.digest, decision: 'approve' },
  90  |       headers: postHeaders,
  91  |     });
  92  |     const activate = await page.request.put(`/api/prompts/${TEMPLATE_NAME}/activate`, {
  93  |       data: { version: 1, reason: 'P1-19 E2E 启用' },
  94  |       headers: postHeaders,
  95  |     }).then((r) => r.json());
  96  |     await page.request.post(`/api/proposals/${activate.proposal_id}/decision`, {
  97  |       data: { digest: activate.digest, decision: 'approve' },
  98  |       headers: postHeaders,
  99  |     });
  100 |     const applied = await page.request.post(`/api/prompts/${TEMPLATE_NAME}/apply`, {
  101 |       data: { proposal_id: activate.proposal_id, digest: activate.digest },
  102 |       headers: postHeaders,
  103 |     });
  104 |     if (!applied.ok()) throw new Error(`prompt apply failed: HTTP ${applied.status()} ${await applied.text()}`);
  105 |   }
  106 | 
  107 |   // 2) P1-01 §4 衔接：详情页「用此模板发起试跑」深链进入 Chat 调试页并自动填充
  108 |   const variables = encodeURIComponent(JSON.stringify({ topic: '心理画像' }));
  109 |   await page.goto(`/chat-debug?template=${TEMPLATE_NAME}&version=1&variables=${variables}`);
  110 |   await expect(page.getByTestId('chat-debug-root')).toBeVisible();
  111 |   await expect(page.getByTestId('template-select')).toHaveValue(TEMPLATE_NAME);
  112 |   await expect(page.getByTestId('var-input-topic')).toHaveValue('心理画像');
  113 | 
  114 |   // 3) 勾选工具 + 发送触发消息
  115 |   await page.getByTestId(`tool-check-${TOOL_NAME}`).check();
  116 |   await page.getByTestId('chat-input').fill('调用 add 计算 3 和 4 的和');
  117 |   await page.getByTestId('chat-send').click();
  118 | 
  119 |   // 4) SSE 流式回复渲染（stub 文本 + 工具续流段）
  120 |   const assistant = page.getByTestId('chat-msg-assistant');
> 121 |   await expect(assistant).toContainText('Deterministic streaming stub', { timeout: 30_000 });
      |                           ^ Error: expect(locator).toContainText(expected) failed
  122 |   await expect(assistant).toContainText('已真实执行', { timeout: 30_000 });
  123 | 
  124 |   // 5) trace 面板：本轮触发了工具 add（参数/结果）
  125 |   const entry = page.getByTestId('chat-trace-entry');
  126 |   await expect(entry).toContainText(TOOL_NAME);
  127 |   await expect(entry).toContainText('{"a":3,"b":4}');
  128 |   await expect(entry).toContainText('{"sum":7}');
  129 |   await expect(entry).toContainText('已真实执行');
  130 | 
  131 |   // 6) 模板徽标（审计链：name/version/variables_hash）
  132 |   await expect(page.getByTestId('badge-template')).toContainText(`${TEMPLATE_NAME} v1`);
  133 |   await expect(page.locator('[data-testid="chat-debug-badges"]').getByText('variables_hash')).toBeVisible();
  134 | 
  135 |   // 7) 归档：截图 + trace JSON（UI trace + 工具调用 receipts + 渲染日志）
  136 |   fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  137 |   await page.screenshot({ path: path.join(EVIDENCE_DIR, 'chat-debug-run.png'), fullPage: true });
  138 | 
  139 |   const uiTrace = await page.evaluate(() => {
  140 |     const panel = document.querySelector('[data-testid="chat-trace-panel"]');
  141 |     return panel ? panel.textContent : '';
  142 |   });
  143 |   const calls = await page.request.get('/api/tools/calls?limit=50').then((r) => r.json());
  144 |   const addReceipts = calls.calls.filter(
  145 |     (c: { tool: string; arguments: { a?: number } }) =>
  146 |       c.tool === TOOL_NAME && c.arguments?.a === 3,
  147 |   );
  148 |   expect(addReceipts.length).toBeGreaterThan(0);
  149 | 
  150 |   const logs = await page.request.get(`/api/prompts/${TEMPLATE_NAME}/logs`).then((r) => r.json());
  151 | 
  152 |   fs.writeFileSync(path.join(EVIDENCE_DIR, 'trace.json'), JSON.stringify({
  153 |     captured_at: new Date().toISOString(),
  154 |     template: TEMPLATE_NAME,
  155 |     tools: [TOOL_NAME],
  156 |     user_message: '调用 add 计算 3 和 4 的和',
  157 |     ui_trace_text: uiTrace,
  158 |     tool_receipts: addReceipts,
  159 |     prompt_render_logs: logs,
  160 |   }, null, 2), 'utf-8');
  161 | });
  162 | 
```