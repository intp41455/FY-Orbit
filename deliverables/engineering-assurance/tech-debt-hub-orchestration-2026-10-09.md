# FY-Orbit Hub 多 Agent 协同编排 — 剩余风险治理评估

**日期**：2026-10-09
**工作流**：工作流 5（技术债评估）+ 工作流 1（代码审查）混合
**参与成员**：Archi（架构师）/ Cody（代码审查师）/ Rex（SRE 工程师）/ Tessa（测试专家）/ Docu（技术文档师）
**评估对象**：仓库 `FY-Orbit`，commit `1217fe7`（已核验存在，作者 intp41455，2026-10-09 08:37）
**主理人**：甄宇航（Zhen）· 工程督导

---

## 📌 TL;DR（执行摘要）

- **整体结论**：陛下提出的 4 项剩余风险全部成立，且**修复成本远低于预期**——仓库里已有 4 套成熟基建（`ModelGateway` 重试、`retry_call` 退避、`budget.py` 配额、`hub.py` 端点范式），问题是**编排链路把它们全部绕过了**。这是「接线债」而非「从零建设债」。
- **严重度分布**：🔴 严重 3 项 / 🟠 高 4 项 / 🟡 中 5 项 / 🟢 低 3 项
- **阻塞 / 非阻塞**：**2 项阻塞**（缺陷 A、B）——它们不影响现有脚本调用，但**一旦给编排加 HTTP 端点就必然炸**，因此必须先修再上线。
- **最关键的单点洞察**：321 行编排单测全部注入 `_FakeInvoke`，其签名是按 orchestrator 的**期望**伪造的，与真实 `HubService.invoke` 差一个 `actor` 参数。**测试是自我确认的闭环，无法证伪真实集成**——这正是陛下两次「改了调用点忘加定义」能逃过 3572 条回归的结构性根因。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟡 **有条件通过**（脚本可用，HTTP 化前必须先修 2 项阻塞） |
| 阻塞项数量 | **2**（缺陷 A 签名错位 / 缺陷 B 跨线程共享 Session） |
| 关键行动项 | **7** 条（P0×3 / P1×2 / P2×2） |
| 建议下一步 | ①修 A+B（各 ≤10 行）→ ②把 gateway 重试接到 hub → ③照 `hub.py` 范式补 `/orchestrate` 端点 |
| 陛下 4 项剩余风险的结论 | 全部成立，但**3 项有现成基建可复用**，1 项（OpenCode 凭证）是外部依赖不可代劳 |

---

## 🔍 审查发现（按严重度排序）

| # | 严重度 | 类别 | 文件:行 | 问题描述 | 建议修复 | 来源 |
|---|--------|------|---------|---------|---------|------|
| 1 | 🔴严重 | 正确性 | `orchestrator.py:207-208` vs `connections.py:483-485` | **`invoke` 签名错位**：Orchestrator 按 `invoke(conn_id, *, action, params, timeout)` 调用，真实 `HubService.invoke(actor, conn_id, *, action, params, ...)` 第 1 位是 `actor`。直接注入必然 actor/conn_id 互换。**铁证**：`api/routes/hub.py:246-250` 真实用法为 `hub.invoke(actor, conn_id, action=..., ...)` | 编排层用 `functools.partial(hub.invoke, actor)` 绑定 actor，或在 `Orchestrator.__init__` 显式接收 actor 并适配 | Archi 提出 / Cody 验证 / Zhen 铁证 |
| 2 | 🔴严重 | 正确性/并发 | `orchestrator.py:309-317` + `connections.py:483-505` | **parallel 跨线程共享 SQLAlchemy Session**：`ThreadPoolExecutor` 内并发调用 `HubService.invoke`，其路径会触碰同一个 Session（`:497-498` 写、`_get` 读并可能 lazy-load 发 SQL）。Session 非线程安全，高并发下会串话/脏读 | 每线程独立 Session（`sessionmaker` 工厂），或在并行派单前把所有需要的数据预取（连接配置+凭证）后不再触库 | Archi 提出 / Cody 确认 |
| 3 | 🔴严重 | 正确性 | `adapters.py:131-138`（`InvokeResult.to_public`）+ `orchestrator.py:215-225` | **跨层 `meta` 丢失，429 与永久 400 无法区分**。上游已精确分类（`ProviderRateLimited.retryable=True`、`ProviderAuthError` 永不重试），但 `to_public()` 不返回 `meta`，`_run_step` 只读 `result.get("ok")/get("error")`。编排层因此**无法判断该重试还是该放弃** | `to_public()` 加 `meta` 字段；`_run_step` 读取 `retryable` 决定是否退避重试 | Cody |
| 4 | 🟠高 | 韧性 | `adapters.py:411-426` + `gateway.py:528-558` | **hub 路径零退避重试**。`ChatModelAdapter.invoke` 直接 `build_provider()` → `provider.complete()`，**绕过** `ModelGateway._call_with_retry`（后者有完整退避 + `Retry-After` 尊重）。编排层也无任何退避 | 让 hub 的 chat 调用改走 `ModelGateway`，或在编排层套用现成 `retry_call`（`knowledge/sources/base.py:75`，已含 jitter） | Rex / Tessa 独立证实 |
| 5 | 🟠高 | 可维护性 | `adapters.py:13` vs `:426` | **文档承诺与实现不符**。模块头声称"经 `build_provider`/`probe_provider` **复用其重试与计费安全边界**"，实际直接调 `complete()`，重试边界根本没用上 | 二选一：要么修实现让它真的走 gateway，要么改注释如实描述。**不可两不管** | Zhen |
| 6 | 🟠高 | 性能 | `adapters.py:726-731` | **无连接池**：`_request` 内 `with httpx.Client(**kwargs)` 每次请求新建 client，重试时再建一次。免费档场景下握手开销占比显著 | 提升为长生命周期 client（在 adapter 实例上持有），仅在 transport 变更时重建 | Rex |
| 7 | 🟠高 | 韧性 | `adapters.py:808-831` | **退避默认关闭**：`retries = int(self.config.get("retries") or 0)` → **默认 0，即不重试**。虽有线性退避代码（`0.2 * 2**attempt`），但需用户显式配置才生效，且**无 jitter、不解析 `Retry-After`** | 给免费档连接设默认 `retries>=2`；改用 `retry_call` 统一（自带 jitter）；解析 `Retry-After`（字段已存在于 `ProviderError.retry_after`） | Rex / Zhen |
| 8 | 🟡中 | 可观测性 | 全局 | **编排无任何指标/trace**。步骤级 latency、失败率、429 计数、连接占用率、编排成功率均不可观测 | 按 Rex 的最小指标集埋点（见下方 SRE 章节） | Rex |
| 9 | 🟡中 | 测试 | `tests/unit/hub/test_orchestrator.py:38-50` | **测试自我确认闭环**：`_FakeInvoke.__call__(self, conn_id, *, action, params, timeout_seconds)` 按 orchestrator 期望伪造签名，与真实签名不符却永不报错 → 集成断裂不可见 | 增加**契约测试**：直接对 `HubService.invoke` 做签名断言（`inspect.signature`），或加一条真 hub 的最小集成用例 | Tessa / Zhen |
| 10 | 🟡中 | 测试 | `orchestrator.py:383-419` | **`summarize` 从未真实验证**。146 单测覆盖逻辑，但汇总链路依赖真实上游，因限流从未跑通 | 引入可控 mock 上游网关 + 契约测试，逐级推进验证矩阵（详见 Tessa 章节） | Tessa |
| 11 | 🟡中 | 架构 | `orchestrator.py:104-122` | **actor 未纳入编排构造契约**。编排器设计上假设"注入的 callable 已绑好身份"，但该假设无类型强制，且无文档 | 在构造签名上显式化身份来源，或提供 `from_service(hub, actor)` 工厂方法 | Archi |
| 12 | 🟡中 | 正确性 | `orchestrator.py:193-203` | **`params` 构造顺序脆弱**：`context` 分支**整体替换** `params`，其后 `params_extra.update()` 可能覆盖 `messages`；且 `tool` 在该分支下需重设 | 改为显式构造单一 params dict，各来源优先级写死并加注释 | Cody |
| 13 | 🟢低 | 可维护性 | `orchestrator.py:288-302` | **注释表述易误读**：`used` 排他注释称"不显式排除就会三次全落同一个连接"，但 `_pick` 的 `picked`/`continue` 分支实际已能正确报"候选占用"。Cody 实测**该逻辑无缺陷、`raise` 可达**（3 侧面 2 连接 → 第 3 个如实失败） | 仅优化注释措辞，无需改逻辑 | Cody |
| 14 | 🟢低 | 正确性 | `orchestrator.py:176-181` | `capability` 缺省兜底为 `"invoke"`，对 `mcp_server` 类适配器会失败且错误信息误导（真实需求是工具名走 `params["name"]`） | 按 kind 决定兜底动作名，或强制显式指定 | Cody |
| 15 | 🟢低 | 文档 | 全局 | **编排零文档**，用户无法自助使用 | 采纳 Docu 的 runbook（已产出） | Docu |

> **诚实纠偏声明**：主理人与架构师初始怀疑「`run_parallel` 排他逻辑有缺陷、`raise` 不可达」及「`summarize` 可能被路由到非 chat 连接」。经 Cody 独立审查，**两条均不成立**——排他逻辑正确、`kind="openai_chat"` 过滤有效。此处如实撤销，避免误导修复方向。

---

## 🏗️ 架构影响评估（Archi 产出摘要）

### 关键发现：编排的三层能力里，**只缺最后一层的 HTTP 暴露**

| 层 | 已有 HTTP 端点 | 位置 |
|---|---|---|
| 单连接调用 | ✅ `POST /hub/connections/{conn_id}/invoke` | `api/routes/hub.py:236` |
| 路由试算 | ✅ `POST /hub/route` | `api/routes/hub.py:298` |
| **多 Agent 协同** | ❌ **唯独缺这一层** | 无 |

**这改变了任务性质**：不是"从零建 API"，而是"照 `hub.py` 既有范式补 1–2 个端点"。

### ADR 建议（Archi 提出，主理人整理）

- **ADR-001**：新增 `POST /hub/orchestrate` 统一入口，body 带 `mode`（sequential/parallel/pipeline）+ `summarize: bool`，复用既有 `_owner(actor)` + `csrf_protected` 依赖链。
- **ADR-002**：进度回传用 **SSE**（编排实测单步可达 18s，同步 HTTP 会撞网关超时；轮询对步骤级状态表达力不足）。
- **ADR-003**：`pipeline` **暂不暴露给前端**（需要用户先知道 connection_id，属高级用法，先走 API）。
- **ADR-004**（阻塞）：**编排必须先解决 actor 绑定**（缺陷 A），否则端点一上线即 100% 失败。

### 既有可复用基建（**别重造轮子**）

| 能力 | 现成位置 | 用途 |
|---|---|---|
| 指数退避 + jitter | `knowledge/sources/base.py:75` `retry_call` | 直接拿来套编排调用 |
| Gateway 重试 + `Retry-After` | `runtime/gateway.py:528-558` | chat 通道首选接入点 |
| 滚动月度预算 + 预留 | `services/budget.py:1-40` | 429 的配额感知治理 |
| 并发/租约控制 | `api/routes/canvas.py` `OrchestratorLeaseService` | 编排并发闸门参考 |
| MCP 重连策略 | `adapters/mcp.py:211` `ReconnectPolicy` | 本机 MCP 步骤的韧性 |
| 端点范式 | `api/routes/hub.py` | 照抄即可 |

---

## 🧪 测试覆盖评估（Tessa 产出摘要）

### 验证分级矩阵

| 层级 | summarize 当前状态 | 目标 | 前置条件 |
|---|---|---|---|
| 单测 | ✅ 146 条覆盖 | 保持 | 无 |
| 契约测试（mock 上游） | ❌ 缺 | **本轮补齐** | 需 mock 网关 |
| 集成测试（真连接隔离） | ❌ 缺 | 申请后补 | 需隔离环境 |
| 真实档位端到端 | ❌ **从未跑通** | 需付费 key 或独立配额 | **需陛下决策** |

### 429 限流测试用例设计（关键）

- 指数退避是否生效 / jitter 是否存在（`rng` 可注入，`base.py:82` → **可确定性测试**）
- **retry 上限** → 现有 `retries` 默认 0（缺陷 7），**这是待建设能力而非待测试能力**
- 幂等性：重试是否重复计费 —— 现有 `ProviderAuthError` 注释明确"never bill twice"，但 429 路径需验证
- `Retry-After` 是否被尊重 —— **当前 hub 路径未消费该字段**（缺陷 3）
- **thundering herd**：并行侧面同时撞 429 会放大限流 —— 需全局并发闸门，非线程池调参

### 覆盖率真实盲区（诚实说明）

146 条单测覆盖的是**逻辑分支**，永远覆盖不到的**真实世界性质**：
- 真实延迟分布（18s 尾延迟对超时阈值的压力）
- 真实上游错误码多样性（免费档返回的 429 body 格式）
- 跨线程竞态（缺陷 B，恰恰是单测结构上无法发现的）

---

## 📊 SRE 评估（Rex 产出摘要）

### 限流治理方案（优先级排序）

| 优先级 | 措施 | 依据 |
|---|---|---|
| P0 | **把 `ModelGateway` 重试接到 hub chat 路径** | 基建已有，接线即可，收益最大 |
| P0 | **`retries` 默认从 0 改为 ≥2**（免费档连接） | `adapters.py:808` 默认 0 = 不重试 |
| P1 | 全局并发信号量（替代仅靠 `DEFAULT_MAX_WORKERS=3`） | 免费档配额才是真上限 |
| P1 | 解析 `Retry-After`（`ProviderError.retry_after` 已有） | 尊重上游意图 |
| P2 | 连接池化（`httpx.Client` 复用） | 缺陷 6 |
| P2 | 配额感知调度（接 `budget.py`） | 滚动月度预算已实现 |

### 最小可用指标集

`orchestration_total{mode,ok}`、`orchestration_step_latency_ms{mode,step}`（p50/p95）、`hub_invoke_total{conn,kind,retryable}`、`hub_429_total{conn}`、`orchestration_connection_occupancy`。

### 并发容量推导

单步实测最长 18068ms、免费档配额**未知（需实测探测，不可假设）**。保守取值：**并发 2–3**，并在连续 429 时**自动降为 1**。探测方法：阶梯加压至首次 429，记录阈值。

### 失败模式分诊表

| 失败模式 | SEV | 影响 | 处置 |
|---|---|---|---|
| 路由零候选（`没有 Agent 能处理`） | SEV-4 | 单次编排失败 | 补充能力别名（别名表扩容） |
| 候选全占用 | SEV-4 | 部分侧面失败 | 属**预期行为**（诚实报失败），非 bug |
| 429 限流 | SEV-3 | 编排成功率下降 | 退避 + 降并发 |
| 单步超时 | SEV-4 | 该步失败，`sequential` 会中断 | 按步设 timeout |
| 上游 5xx | SEV-3 | 同上 | 退避重试 |
| 凭证失效 | SEV-2 | 该连接不可用 | 不重试（`ProviderAuthError` 已正确分类） |
| **actor/conn_id 错位** | **SEV-1** | **编排 100% 失败** | **上线前必修（缺陷 A）** |

### 风险登记册

| 风险 | 可能性 | 影响 | 缓解 | 负责角色 |
|---|---|---|---|---|
| WorkBuddy 无法外接 | 已确认 | 该数据源不可用 | 接受，改用其它连接 | 陛下决策 |
| OpenCode 凭证未就绪 | 已确认 | 该 Agent 不可接 | **需陛下提供，无法代劳** | 陛下 |
| 免费档配额 | 高（实测已撞） | 编排成功率下降 | 退避 + 降并发 + 配额探测 | Rex |
| summarize 未真实验证 | 高 | 汇总步骤生产不可信 | 验证矩阵逐级推进 | Tessa |
| **缺陷 A/B 未修先上线** | **中** | **编排全挂 + Session 串话** | **列 P0 上线门禁** | Zhen |

---

## ✅ 行动清单（按优先级排序）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | 修**缺陷 A**：编排层用 `partial(hub.invoke, actor)` 绑定 actor，消除签名错位 | 开发 / Cody 复验 | **P0** | HTTP 化前（≤10 行） |
| 2 | 修**缺陷 B**：并行派单改每线程独立 Session，或预取全部数据后不再触库 | 开发 / Archi 复核 | **P0** | HTTP 化前 |
| 3 | 补**契约测试**：`inspect.signature` 断言 `HubService.invoke` 与编排调用一致，打破自我确认闭环 | Tessa | **P0** | 与 A/B 同批 |
| 4 | 把 `ModelGateway` 重试（或现成 `retry_call`）接到 hub chat 路径；`retries` 默认改 ≥2；消费 `Retry-After` | Rex 设计 / 开发实现 | P1 | 本轮 |
| 5 | `InvokeResult.to_public()` 补 `meta`，让编排层能区分 429（可重试）与永久 400（放弃） | 开发 / Cody 复验 | P1 | 本轮 |
| 6 | 照 `api/routes/hub.py` 范式加 `POST /hub/orchestrate`（含 SSE 进度），并接 `/hub/route` 同款鉴权 | Archi 设计 / 开发实现 | P2 | 下轮 |
| 7 | 按 Tessa 验证矩阵把 `summarize` 从单测推进到契约测试；如需真实档位端到端**请陛下决策是否投入付费 key** | Tessa | P2 | 待陛下决策 |

---

## ⚠️ 待完善 / 已知局限

- **免费档配额具体数值未知**，所有容量建议均为推导值，需实测探测校准。
- **`summarize` 的真实上游行为不可得**——在拿到可用配额前，只能做到契约测试级别，不能声称"已验证"。
- **`WebhookAdapter` 与 chat 通道的重试逻辑是两套**（前者线性退避、后者无退避）。统一到 `retry_call` 是更彻底的方案，但会改动面较大，本轮建议先接线后统一。
- 本次评估为**静态代码审查 + 交叉验证**，未实际运行测试（未在隔离环境执行 pytest，避免污染陛下工作树）。所有结论均有 `文件:行号` 依据。
- **未验证**：`_MCP_CLIENTS` 全局字典（`connections.py:190-193` 附近）在多线程下的竞争细节，建议缺陷 B 修复时一并检查。

---

## 📚 数据来源 & 成员产出索引

| 成员 | 角色 | 核心产出 |
|---|---|---|
| **Archi** | 架构师 | HTTP 契约设计、ADR-001~004、`actor` 绑定阻塞项、可复用基建清单、`hub.py` 端点缺口分析 |
| **Cody** | 代码审查师 | 15 项缺陷清单（🔴3/🟠4/🟡5/🟢3）、**meta 跨层丢失根因定位**、并**推翻**排他逻辑与 kind 过滤两项误判 |
| **Rex** | SRE 工程师 | 限流治理优先级、最小指标集、容量推导、失败模式分诊表、风险登记册、**WebhookAdapter 无连接池** |
| **Tessa** | 测试专家 | 四层验证分级矩阵、429 测试用例设计、thundering herd 分析、**自我确认闭环**根因、真实世界盲区 |
| **Docu** | 技术文档师 | 三形态用法指南（含 `partial` 绑定 actor 的正确示例）、排障手册、如实风险陈述 |
| **Zhen** | 主理人 | 事实核验（commit/别名数/端点存在性）、**签名错位铁证**（`hub.py:246-250`）、`_FakeInvoke` 闭环发现、报告汇编 |

### 关键证据锚点（可复核）

- 签名错位铁证：`api/routes/hub.py:246-250` 真实调 `hub.invoke(actor, conn_id, ...)`
- 测试闭环铁证：`tests/unit/hub/test_orchestrator.py:38-50` `_FakeInvoke` 签名不含 actor
- meta 丢失：`adapters.py:131-138` `to_public()` 无 meta 字段
- 零退避：`adapters.py:411/426` 直调 `provider.complete()`；`services/hub/` 内 gateway 零引用（grep 实证）
- 退避默认关：`adapters.py:808` `retries or 0`
- 无连接池：`adapters.py:726-731` 每请求新建 `httpx.Client`

---

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
> 免费档配额、付费 key 投入、OpenCode 凭证等外部依赖项需陛下本人决策。
