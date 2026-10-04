# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: p1-15-dispatch.spec.ts >> P1-15 点击弹窗派改（19 号 ModelBinding 链路） >> 弹窗走 19 号 ModelBinding 链路并回填结果
- Location: e2e\p1-15-dispatch.spec.ts:70:3

# Error details

```
Error: POST /api/teams（创建派改团队）

expect(received).toBeGreaterThanOrEqual(expected)

Expected: >= 1
Received:    0
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary [ref=e4]:
    - generic [ref=e5]:
      - generic [aria-hidden] [ref=e6]: FY
      - generic [ref=e7]:
        - strong [ref=e8]: AI 协作工作台
        - generic [ref=e9]: 让 AI 团队，把事情做完
    - group "空间切换" [ref=e10]:
      - button "工作台空间" [pressed] [ref=e11] [cursor=pointer]: 💼 工作台空间
      - button "个人空间" [ref=e12] [cursor=pointer]: 🌌 个人空间
    - generic [ref=e13]:
      - generic [ref=e14]: 工作台功能导航
      - navigation "主导航" [ref=e15]:
        - link "任务工作台 指挥 · 终端 · 验收" [ref=e16] [cursor=pointer]:
          - /url: /workbench
          - generic [aria-hidden] [ref=e17]: 🎯
          - generic [ref=e18]:
            - text: 任务工作台
            - generic [ref=e19]: 指挥 · 终端 · 验收
        - link "协作画布 内部团队 · 逐成员选模型" [ref=e20] [cursor=pointer]:
          - /url: /canvas
          - generic [aria-hidden] [ref=e21]: 🧭
          - generic [ref=e22]:
            - text: 协作画布
            - generic [ref=e23]: 内部团队 · 逐成员选模型
        - link "DSL 画布 受限动词 · 数据流执行" [ref=e24] [cursor=pointer]:
          - /url: /dsl-canvas
          - generic [aria-hidden] [ref=e25]: 🧱
          - generic [ref=e26]:
            - text: DSL 画布
            - generic [ref=e27]: 受限动词 · 数据流执行
        - link "Agent 与技能 MCP · 经验沉淀" [ref=e28] [cursor=pointer]:
          - /url: /skills
          - generic [aria-hidden] [ref=e29]: 🧩
          - generic [ref=e30]:
            - text: Agent 与技能
            - generic [ref=e31]: MCP · 经验沉淀
        - link "审批中心 提案与授权" [ref=e32] [cursor=pointer]:
          - /url: /approvals
          - generic [aria-hidden] [ref=e33]: ✅
          - generic [ref=e34]:
            - text: 审批中心
            - generic [ref=e35]: 提案与授权
        - link "设置与数据 同步 · 权限" [ref=e36] [cursor=pointer]:
          - /url: /settings
          - generic [aria-hidden] [ref=e37]: ⚙️
          - generic [ref=e38]:
            - text: 设置与数据
            - generic [ref=e39]: 同步 · 权限
    - generic [ref=e40]:
      - link "权限与沙箱隔离 受控" [ref=e41] [cursor=pointer]:
        - /url: /settings
        - generic [ref=e42]: 权限与沙箱隔离
        - generic [ref=e44]: 受控
      - link "预算与用量 查看" [ref=e45] [cursor=pointer]:
        - /url: /settings
        - generic [ref=e46]: 预算与用量
        - generic [ref=e48]: 查看
      - generic [ref=e49]: owner
      - button "登出" [ref=e50] [cursor=pointer]
  - main [ref=e51]:
    - heading "任务工作台" [level=2] [ref=e53]
    - generic [ref=e54]:
      - generic [ref=e55]:
        - generic [ref=e56]: 任务目标
        - textbox "任务目标" [ref=e57]:
          - /placeholder: 描述要完成的工程/调研任务
      - button "创建任务（幂等）" [disabled] [ref=e58]
    - heading "工程代码工作台" [level=2] [ref=e60]
    - generic [ref=e61]:
      - generic [ref=e62]:
        - strong [ref=e63]: 工作区
        - button "注册工作区" [ref=e64] [cursor=pointer]
      - generic [ref=e65]: 尚无已注册工作区。
    - generic [ref=e66]:
      - generic [ref=e67]:
        - generic [ref=e68]: 代码/文件区：注册并选择一个工作区后，文件树、编辑器与差异审查即可操作。
        - separator "调整左右分区宽度" [ref=e71]
        - generic [ref=e73]:
          - generic [ref=e74]:
            - strong [ref=e75]: 预览窗
            - generic [ref=e76]:
              - combobox "节流窗口" [ref=e77]:
                - option "300ms"
                - option "400ms" [selected]
                - option "500ms"
              - generic "节流窗口 400ms（300-500ms 可配）" [ref=e78]: 待预览
          - generic [ref=e79]: 预览内容区：从左侧文件树选择一个 Markdown / HTML 文件即可实时预览。
          - generic [ref=e81]:
            - generic [ref=e82]: 派改：点击下方元素可发起改写（统一走 ModelBinding 链路）
            - button "产品简介段落 Find Yourself 是一款单主人自我探索陪伴应用：它记录你的言语与经历，在画像与协作画布上沉淀为可复核的记忆结构，并在隐私边界内提供陪伴式洞察。" [ref=e83] [cursor=pointer]:
              - generic [ref=e84]: 产品简介段落
              - generic [ref=e85]: Find Yourself 是一款单主人自我探索陪伴应用：它记录你的言语与经历，在画像与协作画布上沉淀为可复核的记忆结构，并在隐私边界内提供陪伴式洞察。
            - button "工作台说明段落 工程代码工作台提供文件树、编辑器、终端与 git 面板；预览窗实时渲染Markdown 与沙箱化 HTML，选中内容可一键发起派改（统一走 ModelBinding 链路）。" [ref=e86] [cursor=pointer]:
              - generic [ref=e87]: 工作台说明段落
              - generic [ref=e88]: 工程代码工作台提供文件树、编辑器、终端与 git 面板；预览窗实时渲染Markdown 与沙箱化 HTML，选中内容可一键发起派改（统一走 ModelBinding 链路）。
          - generic [ref=e89]:
            - generic [ref=e90]: 派改结果回填
            - generic [ref=e91]: 【派改结果·合成】已按改写指令处理选中内容。 指令摘要：改写指令：把这段话改写得更简洁，保留关键事实 改写后文本：本段为经 19 号 ModelBinding 链路（冻结绑定 → ModelGateway → 本地确定性提供方）返回的确定性改写结果，仅用于 P1-15 验收演示。
      - separator "调整终端面板高度" [ref=e92]
      - generic [ref=e93]:
        - generic [ref=e94]:
          - strong [ref=e95]: 终端
          - generic [ref=e96]: 停靠底部
          - button "折叠终端 ▼" [expanded] [ref=e97] [cursor=pointer]
        - generic [ref=e98]: 终端：注册并选择一个工作区后可启动真实 PTY 会话。
    - generic [ref=e101]:
      - generic [ref=e102]:
        - strong [ref=e103]: Diff 可视化（两次 commit 对照）
        - generic [ref=e104]: 新增绿 · 删除红 · 修改琥珀标记
      - generic [ref=e105]:
        - generic [ref=e106]:
          - text: git 工作区名称
          - textbox "git 工作区名称" [ref=e107]:
            - /placeholder: 如 diff-consistency
        - button "加载提交历史" [ref=e109] [cursor=pointer]
    - generic [ref=e110]:
      - strong [ref=e112]: 备份与回滚（P1-14）
      - generic [ref=e113]: 注册并选择工作区、选中一个文件后，可一键备份当前内容并随时回滚。
    - generic [ref=e114]:
      - generic [ref=e115]:
        - strong [ref=e116]: 提交树图
        - generic [ref=e117]: 按 parents 关系布局 · 点击节点联动 diff，选中两个节点可互比
      - generic [ref=e118]:
        - generic [ref=e119]: git 工作区名称
        - textbox "git 工作区名称" [ref=e120]:
          - /placeholder: 如 p12-demo（/api/git-repo 下的工作区名）
        - button "加载历史" [disabled] [ref=e121]
    - dialog "派改" [ref=e122]:
      - generic [ref=e123]:
        - generic [ref=e124]:
          - strong [ref=e125]: 派改（Rewrite）
          - button "关闭派改弹窗" [ref=e126] [cursor=pointer]: ✕
        - generic [ref=e127]:
          - generic [ref=e128]: 选中内容（预填上下文）
          - generic [ref=e129]:
            - generic [ref=e130]: 预览窗 · 产品简介段落
            - generic [ref=e131]: Find Yourself 是一款单主人自我探索陪伴应用：它记录你的言语与经历，在画像与协作画布上沉淀为可复核的记忆结构，并在隐私边界内提供陪伴式洞察。
        - generic [ref=e132]:
          - generic [ref=e133]: 改写指令
          - textbox "改写指令" [ref=e134]:
            - /placeholder: 例如：把这段话改写得更简洁，并保留关键事实
            - text: 把这段话改写得更简洁，保留关键事实
        - generic [ref=e135]:
          - generic [ref=e136]: 模型绑定（ModelBinding 可选项）
          - combobox "模型绑定（ModelBinding 可选项）" [ref=e137]:
            - option "openai-compatible / gemini-1.5-flash"
            - option "openai-compatible / gpt-4o-mini"
            - option "local-synthetic / mock-deterministic（本地确定性提供方）" [selected]
          - generic [ref=e138]: 绑定端点：inprocess://mock-deterministic · 单价已知 · 凭据引用 env:FY_MODEL_API_KEY
        - button "提交派改" [ref=e140] [cursor=pointer]
        - generic [ref=e141]:
          - generic [ref=e142]: 派改结果（已回填预览窗）
          - generic [ref=e143]: 【派改结果·合成】已按改写指令处理选中内容。 指令摘要：改写指令：把这段话改写得更简洁，保留关键事实 改写后文本：本段为经 19 号 ModelBinding 链路（冻结绑定 → ModelGateway → 本地确定性提供方）返回的确定性改写结果，仅用于 P1-15 验收演示。
          - generic [ref=e145]: 团队 team-cbbdccd44a2d · 请求模型 mock-deterministic · 实际模型 unknown
```

# Test source

```ts
  41  | test.describe('P1-15 点击弹窗派改（19 号 ModelBinding 链路）', () => {
  42  |   test.skip(!LOCAL_TOKEN, 'E2E_LOCAL_TOKEN not set; skipping P1-15 dispatch E2E');
  43  | 
  44  |   function extractCookie(setCookie: string | undefined, name: string): string | null {
  45  |     if (!setCookie) return null;
  46  |     for (const part of setCookie.split(/,(?=[^;]+=)/)) {
  47  |       const seg = part.trim();
  48  |       if (seg.startsWith(`${name}=`)) {
  49  |         return seg.slice(name.length + 1).split(';')[0];
  50  |       }
  51  |     }
  52  |     return null;
  53  |   }
  54  | 
  55  |   async function loginViaDevToken(page: Page): Promise<void> {
  56  |     const res = await page.request.post('/auth/local/dev-token', {
  57  |       data: { token: LOCAL_TOKEN },
  58  |     });
  59  |     if (!res.ok()) {
  60  |       throw new Error(`dev-token login failed: HTTP ${res.status()}`);
  61  |     }
  62  |     const session = extractCookie(res.headers()['set-cookie'], 'fy_session');
  63  |     if (session) {
  64  |       await page.context().addCookies([
  65  |         { name: 'fy_session', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  66  |       ]);
  67  |     }
  68  |   }
  69  | 
  70  |   test('弹窗走 19 号 ModelBinding 链路并回填结果', async ({ page }) => {
  71  |     fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  72  | 
  73  |     // ---- 网络日志采集：记录全部 /api/ 请求（方法/URL/状态码） ----
  74  |     const log: LogEntry[] = [];
  75  |     const record = (method: string, url: string, status: number | null) => {
  76  |       if (!url.includes('/api/')) return;
  77  |       log.push({ ts: new Date().toISOString(), method, url, status });
  78  |     };
  79  |     page.on('response', (r: Response) => record(r.request().method(), r.url(), r.status()));
  80  |     page.on('requestfailed', (r) => record(r.method(), r.url(), -1));
  81  |     const pageErrors: string[] = [];
  82  |     page.on('pageerror', (e) => pageErrors.push(`pageerror: ${e.message}`));
  83  |     page.on('console', (m) => {
  84  |       if (m.type() === 'error') pageErrors.push(`console.error: ${m.text()}`);
  85  |     });
  86  | 
  87  |     await loginViaDevToken(page);
  88  |     try {
  89  |       await runDispatchFlow(page, log);
  90  |     } finally {
  91  |       // 失败也归档现场：网络日志 + 页面错误（video 由 Playwright 归档于
  92  |       // test-results/，成功路径在本测试内复制到 evidence/）
  93  |       fs.writeFileSync(
  94  |         path.join(EVIDENCE_DIR, 'network-log.json'),
  95  |         JSON.stringify({ captured_at: new Date().toISOString(), page_errors: pageErrors, entries: log }, null, 2),
  96  |         'utf-8',
  97  |       );
  98  |     }
  99  |   });
  100 | 
  101 |   async function runDispatchFlow(page: Page, log: LogEntry[]): Promise<void> {
  102 |     await page.goto('/workbench');
  103 |     await expect(page.getByTestId('wb-preview-pane')).toBeVisible();
  104 | 
  105 |     // ---- 1. 点击预览窗派改元素 → 弹窗打开 ----
  106 |     await page.getByTestId('preview-element').first().click();
  107 |     const dialog = page.getByTestId('dispatch-dialog');
  108 |     await expect(dialog).toBeVisible();
  109 |     const ctxText = await page.getByTestId('dispatch-context').innerText();
  110 |     expect(ctxText.length).toBeGreaterThan(10); // 选中内容已预填
  111 |     await page.screenshot({ path: path.join(EVIDENCE_DIR, '01-dialog-open.png'), fullPage: true });
  112 | 
  113 |     // 弹窗打开即请求 ModelBinding 可选项（19 号链路：GET /api/teams/catalog）
  114 |     await expect(page.getByTestId('dispatch-model-select')).toBeVisible();
  115 |     const catalogCalls = log.filter((e) => e.url.includes('/api/teams/catalog'));
  116 |     expect(catalogCalls.length).toBeGreaterThanOrEqual(1);
  117 | 
  118 |     // ---- 2. 表单校验：空指令拦截 ----
  119 |     await page.getByTestId('dispatch-submit').click();
  120 |     await expect(page.getByTestId('dispatch-validation')).toBeVisible();
  121 | 
  122 |     // ---- 3. 填写指令并提交 ----
  123 |     await page.getByLabel('改写指令').fill('把这段话改写得更简洁，保留关键事实');
  124 |     await page.screenshot({ path: path.join(EVIDENCE_DIR, '02-dialog-filled.png'), fullPage: true });
  125 |     await page.getByTestId('dispatch-submit').click();
  126 | 
  127 |     // 提交中状态（按钮禁用）由组件实现；此处直接等待结果面板（stub 链路毫秒级完成）
  128 | 
  129 |     // execute 返回成功（200），结果面板出现
  130 |     const result = page.getByTestId('dispatch-result');
  131 |     await expect(result).toBeVisible({ timeout: 30_000 });
  132 |     await expect(page.getByTestId('dispatch-submit')).toBeEnabled();
  133 |     await page.screenshot({ path: path.join(EVIDENCE_DIR, '03-result-backfilled.png'), fullPage: true });
  134 | 
  135 |     // ---- 4. 网络层断言：请求真实经过 19 号 ModelBinding 链路 ----
  136 |     const teamsRequests = log.filter((e) => e.url.includes('/api/teams'));
  137 |     const postTeams = teamsRequests.filter((e) => e.method === 'POST' && /\/api\/teams$/.test(e.url));
  138 |     const startCalls = teamsRequests.filter((e) => e.method === 'POST' && e.url.includes('/start'));
  139 |     const executeCalls = teamsRequests.filter((e) => e.method === 'POST' && e.url.includes('/members/execute'));
  140 | 
> 141 |     expect(postTeams.length, 'POST /api/teams（创建派改团队）').toBeGreaterThanOrEqual(1);
      |                                                         ^ Error: POST /api/teams（创建派改团队）
  142 |     expect(startCalls.length, 'POST /api/teams/{id}/start（冻结 ModelBinding）').toBeGreaterThanOrEqual(1);
  143 |     expect(executeCalls.length, 'POST /api/teams/{id}/members/execute（经绑定推理）').toBe(1);
  144 |     expect(postTeams.every((e) => e.status === 201), '创建团队 201').toBeTruthy();
  145 |     expect(startCalls.every((e) => e.status === 200), 'start 200').toBeTruthy();
  146 |     expect(executeCalls[0].status, 'execute 200（绑定链路真实执行）').toBe(200);
  147 | 
  148 |     // execute 响应体核对：requested_model 与弹窗选择一致（绑定真正生效）
  149 |     const execResp = await page.request.get('/health/live'); // 占位保持上下文
  150 |     expect(execResp.ok()).toBeTruthy();
  151 |     const executeUrl = executeCalls[0].url;
  152 |     const teamId = executeUrl.match(/\/api\/teams\/([^/]+)\/members\/execute/)![1];
  153 |     const detail = await page.request.get(`/api/teams/${teamId}`);
  154 |     expect(detail.ok()).toBeTruthy();
  155 |     const snap = await detail.json();
  156 |     const rewriter = snap.members.find((m: { role: string }) => m.role === 'rewriter');
  157 |     expect(rewriter).toBeTruthy();
  158 |     expect(rewriter.requested_model, '成员绑定模型非空').toBeTruthy();
  159 |     expect(String(rewriter.requested_model)).toContain('mock-deterministic');
  160 |     expect(rewriter.effective_confidence).toBeTruthy();
  161 | 
  162 |     // ---- 5. 结果回填预览窗 ----
  163 |     await expect(page.getByTestId('preview-dispatch-result')).toBeVisible();
  164 |     const backfill = await page.getByTestId('preview-dispatch-result').innerText();
  165 |     expect(backfill.length).toBeGreaterThan(10);
  166 |     expect(backfill).toContain('派改结果');
  167 | 
  168 |     // ---- 6. 证据归档：请求日志已在 finally 落盘；video 由运行脚本从
  169 |     // test-results/ 归档；此处补人工核对摘要 ----
  170 |     const summary = {
  171 |       team_id: teamId,
  172 |       requested_model: rewriter.requested_model,
  173 |       effective_confidence: rewriter.effective_confidence,
  174 |       api_teams_requests: teamsRequests.length,
  175 |       catalog_calls: catalogCalls.length,
  176 |       create_calls: postTeams.length,
  177 |       start_calls: startCalls.length,
  178 |       execute_calls: executeCalls.length,
  179 |       execute_status: executeCalls[0].status,
  180 |     };
  181 |     fs.writeFileSync(
  182 |       path.join(EVIDENCE_DIR, 'assertion-summary.json'),
  183 |       JSON.stringify(summary, null, 2),
  184 |       'utf-8',
  185 |     );
  186 |   }
  187 | });
  188 | 
```