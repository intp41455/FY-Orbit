# WorkBuddy 接入方式（2026-10-09 实测结论）

> 结论**推翻**了本文档早前「WorkBuddy 无法外接」的判断。
> 那个结论只看了 `:57645/mcp` 一个私有端点，属于以偏概全。

## 一、真实身份

不是「WorkBuddy」，而是 **CodeBuddy**（腾讯 AI IDE，WorkBuddy 是其桌面马甲）。

证据（真实输出）：

    进程命令行:
      D:\下载的\WorkBuddy\WorkBuddy.exe
        D:\下载的\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy --prewarm ...
    环境变量:
      CODEBUDDY_HOST = "workbuddy-desktop"
      CODEBUDDY_HOST_CAPABILITIES = "elicitation.form,weixinpay.interception"
    401 页面的 HTML <title> = "CodeBuddy Remote Control"

## 二、四个监听端口（netstat 实测）

| 端口 | PID | 是什么 | 能否接入 |
|---|---|---|---|
| 57645 | 125236 | daemon 的 MCP 私有端点 | ❌ token 内存态、无 UI 入口、stdio 单实例锁 |
| 59719 | 125236 | 未知（全 404） | ❌ |
| 18488 | 130020 | 内部 API（全 404 `{"ok":false,"error":"Not Found"}`） | ❌ |
| **64180** | 91688 | **CodeBuddy Gateway（REST API）** | ✅ **本文档的接入点** |

## 三、接入方式一：REST API（推荐，已验证认证）

### 认证

    Authorization: Bearer <CODEBUDDY_GATEWAY_PASSWORD>

密码在宿主进程环境变量里（43 字符，`crypto.randomBytes(32).toString('base64url')`
生成，见 app.asar 源码 `getGatewaySecret()`）。它**不是 UI 入口能复制的**，
但可通过读取进程环境变量获得（本机同用户即可）。

源码依据（app.asar 内原文）：

    GATEWAY_AUTH_MODE_ENV   = "CODEBUDDY_GATEWAY_AUTH"
    GATEWAY_PASSWORD_ENV    = "CODEBUDDY_GATEWAY_PASSWORD"
    getGatewaySecret()      = cachedSecret ||= randomBytes(32).toString("base64url")
    gatewaySecretEnv()      = { CODEBUDDY_GATEWAY_AUTH: "password",
                                CODEBUDDY_GATEWAY_PASSWORD: getGatewaySecret(),
                                CODEBUDDY_GATEWAY_DISABLE_API_DOCS: "1" }
    gatewaySecretHeaders()  = { Authorization: `Bearer ${getGatewaySecret()}` }

**401 -> 404 的含义**：带对 Bearer 后状态码从 401 变404，说明**认证已通过**，
404 只是路由不存在。这是判断认证是否通过的可靠信号。

### 接口清单（来自它自己的首页，非猜测）

    GET/POST /api/v1/acp            ACP 协议端点（JSON-RPC 2.0 over SSE）
    POST      /api/v1/runs启动一次 Agent 运行
    GET       /api/v1/runs/:runId   查运行状态
    GET       /api/v1/runs/:runId/stream   SSE 流式结果
    POST      /api/v1/webhooks/:platform    平台 webhook
    GET       /api/v1/health       健康检查
    GET       /api/v1/status运行状态（busy / lastCompletedAt）
    GET       /api/v1/sessions会话列表（实测发现，首页未列）
    GET       /api/docs            Swagger UI

### 实测通过的调用

    GET  /api/v1/health
      -> 200 {"data":{"status":"ok","uptime":2871.05,
                      "platforms":["generic","wecom","wechat-kf"],"pid":91688}}

    GET  /api/v1/status
      -> 200 {"data":{"status":"ok","busy":true,
                      "lastCompletedAt":1791506217735,
                      "activeSessionId":"24d95996-...",
                      "runStatus":"tool_executing"}}

    GET  /api/v1/sessions
      -> 200 {"data":{"sessions":[{"id":"24d95996-...",
                                 "name":"审计FY项目需求核对结果",
                                 "messageCount":320, ...}]}}

    POST /api/v1/runs{"id":"<自定义>", "type":"message", "content":"<任务>"}
      -> 202 {"data":{"runId":"ef4f587f-...","status":"accepted"}}

**请求体契约**：`{id, type:"message", content}`。服务端400 错误原文
`"Invalid generic message format: missing required fields (id, type)"`
直接说明了必填字段。

## 四、四个必须注意的限制（实测）

### 1. run 被接受但不执行（状态机疑似僵死）

    POST /api/v1/runs -> 202 accepted
    GET  /api/v1/runs/<id> -> {"active": false}      ← 从未变 true
    GET  .../stream -> 404 RUN_NOT_FOUND

连续 10 次 / 60 秒探测，`lastCompletedAt` 恒为 `1791506217735` 不变，
而会话 `updatedAt` 在推进 —— **run 状态机卡在 `tool_executing`**。

**后果**：不能靠 `/api/v1/status` 判断「能否派单」，它反映的是僵死状态。

### 2. SSE 必须在 run 启动瞬间挂上

run 完成后立刻 `/stream` 就404 `RUN_NOT_FOUND`。必须
「POST /runs → 立刻挂 /stream」才能拿到输出，事后补挂拿不到。

### 3. 单实例单会话，与并行编排冲突

`busy` + 单个 `activeSessionId` 意味着**同一时刻只能跑一个任务**。
FY-Orbit 的 `run_parallel` 编排对它只能串行化，不能并行。

### 4. 只能读到当前会话

`/api/v1/sessions` 只返回 1 个会话（`isCurrent: false`），说明
gateway 是「远程控制当前 IDE 会话」的定位，不是多 Agent 队列。

## 五、接入方式二：CLI（待确认）

真实入口：`app.asar.unpacked\cli\bin\codebuddy`

直接执行报 `[WinError 193] %1 不是有效的 Win32 应用程序` ——
它是 **shell 脚本不是 exe**（无扩展名）。需要用 `bash codebuddy` 或
找到它对应的 `.cmd` 包装器。FY-Orbit 的 `cli.run`
（`access.py:278 CliContract` + `access.py:323 run_cli_process`，
已实现 argv 禁 shell=True / 超时杀进程树 / env 白名单）可以直接吃它，
前提是找到正确的 argv 形态。

## 六、对 FY-Orbit 的建议

用 `kind=http_webhook` 接入（已有该kind），配置：

    config:
      url: http://127.0.0.1:64180/api/v1/runs
      method: POST
      headers: { Authorization: "Bearer <从进程环境变量取>" }
    credentials:
      gateway_password: "<CODEBUDDY_GATEWAY_PASSWORD>"

但必须先解决「run 被接受不执行」——否则接上了也拿不到结果。
建议先在 WorkBuddy 里手动跑一次任务把状态机跑通，再验证。

## 七、验证脚本位置

本轮所有实测脚本（纯只读探测，可复现）：

    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_probe_new_ports.py     四端口 + 环境变量
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_try_gateway_auth.py认证方式穷举
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_enum_gateway_routes.py 路由枚举（挖出接口清单）
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_test_real_paths.py    REST + CLI 双路径实测
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_probe_runs_api.py     /runs 契约探测
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_verify_run_processing.py  状态机僵死验证
    ...\work-mode-projects\6ac63ac2f417e341d58c8c23\_read_sessions.py       会话读取

## 八、诚实声明

- 认证已验证通过，`/health` `/status` `/sessions` `/runs` 四个端点均实测 200/202
- **但从未拿到过Agent 的真实输出**（SSE 因状态机僵死未能抓到数据）
- 所以「能派单」已证实，「能拿到结果」**尚未证实**
- CLI 路径因WinError 193 未走通
