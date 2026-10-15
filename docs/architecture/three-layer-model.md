# FY-Orbit · 星轨 —— 三层分层统一底座（规范定义）

| 字段 | 值 |
|---|---|
| 文档类型 | 规范性定义（normative spec） |
| 状态 | **Accepted** |
| 版本 | `1.0.2`（2026-10-13 数据更正，见文末「修订记录」） |
| 层标识 | `L1` / `L2` / `L3`（稳定 ID，供 lint / CI 引用） |
| 适用仓库 | FY-Orbit（产品名「FY-Orbit · 星轨」；后端包 `find_yourself`；前端 `web/`） |
| 相关决策 | [`docs/adr/0001-three-layer-architecture.md`](../adr/0001-three-layer-architecture.md) |
| 需求来源 | N7「三层分层统一底座」；工程闸门出处 `.github/workflows/ci.yml:189-199` |
| 日期 | 2026-10-13 |

> **本文档是「三层」的唯一定义源。** 在此之前，全仓对「三层」的唯一提及是
> `.github/workflows/ci.yml:192` 的一句注释；无架构文档、无分层判据。本文件把「三层」
> 从一句注释升级为可被机器引用的规范：每层有稳定标识（`L1`/`L2`/`L3`）、每层有目录归属、
> 依赖方向有明确规则陈述（可直接转写为 import lint 规则）。
>
> 文中所有 `路径:行号` 均经过只读核实；与本仓侦察数字不一致处，以本文件「§8 事实复验」为准。

---

## 1. 目的与作用域

本规范定义 FY-Orbit 单体仓库的**三层分层模型**，用于：

1. 给「三层」一个**正式、可复读、可机检**的定义（术语锚点）。
2. 为后续工程闸门提供**判据**：后端分层门禁（G7-b）、`runtime ⇄ services` 解环（G7-c）、
   跨语言契约 parity（G7-e）等均以本文件为判据输入。
3. 定义**统一底座**（L1）的收口范围：契约、错误信封、配置读取、事件注册表、
   前端 localStorage 持久化。

**作用域**：本仓库的后端 Python 包 `src/find_yourself/`、顶层包 `find_yourself_dsl/`、
sdk 契约表面，以及前端 `web/src/`。不含 `tests/`、`scripts/`、`desktop/`、`deploy/`、
`infra/`、`migrations/`（这些为工具/交付/基础设施脚本，不受三层依赖规则约束）。

**非目标**：本文档不规定具体某种语言的实现细节，也不要求把 L2 内部的子模块重排成
单一目录；它只规定**依赖方向**与**各层的所有权边界**。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| **L1 · 契约与基础设施（底座）** | 被各层共享、自身不承载业务编排的最小内核：领域契约、配置、统一错误信封、事件注册表、前端基座能力。**L1 不得依赖 L2 / L3。** |
| **L2 · 领域能力与执行引擎** | 承载业务能力的服务层 + 执行引擎（运行时内核、图、网关、总线、适配器、工作流）。**L2 可依赖 L1；不得依赖 L3。** |
| **L3 · 接口与界面** | 对外接口（HTTP 路由、应用装配）与用户界面（页面、业务组件）。**L3 可依赖 L2、L1。** |
| **依赖方向** | 「A 依赖 B」指 A 的源码出现对 B 的 import；本模型中书写为 `A → B`。 |
| **越层直连** | L3 绕过 L2 直接使用 L2 的**具体实现**（而非经由其稳定接口）。技术上仍是 `L3→L2`，属**方向合法但破坏封装约定**，需按 §6 判定。 |
| **环（cycle）** | 同层内两个模块互相 import（`A → B` 且 `B → A`）。**同层依赖不违反方向规则，但形成的环违反「可单测 / 可替换」纪律，须在解环切片中消解。** |

---

## 3. 层定义

### 3.1 `L1` —— 契约与基础设施（底座）

**职责**：定义全仓共享的**契约**与**基础设施原语**，作为唯一真源；不承载业务编排，不
发起对 L2 / L3 的调用。L1 是「统一底座」的物理落点。

**后端归属**：

| 路径 | 作用 | 证据 |
|---|---|---|
| `src/find_yourself/contracts.py` | 领域契约单源：`Domain`、`TaskStatus` 等 `StrEnum`；`Strict(BaseModel)`（`extra="forbid"`） | `contracts.py:17`（`class Strict`）、`:21`（`class Domain`）、`:27`（`class TaskStatus`） |
| `src/find_yourself/config.py` | 配置读取单点：`Settings(BaseSettings)`，`env_prefix="FY_"` | `config.py:8-9` |
| `src/find_yourself/api/errors.py` | **统一错误信封**：`_envelope()`（FROZEN_CONTRACT §1） | `errors.py:25-26` |
| `src/find_yourself/db/` | 持久化基础设施（27 个 `.py`） | 目录实测 27 |
| `src/find_yourself/runtime/hooks.py` | **事件注册表**：`HOOK_EVENTS`（25 个生命周期事件，权威真源）、`WIRED_EVENTS` | `hooks.py:28`、`:53` |

**前端归属**：

| 路径 | 作用 |
|---|---|
| `web/src/api/client.ts` | 统一 fetch 封装：`NetworkError` / `ApiError` / CSRF（`X-CSRF-Token`）；**从不自行读取 token** |
| `web/src/api/types.ts` | 后端契约的前端镜像（非权威，须与 `contracts.py` / OpenAPI 对齐） |
| `web/src/hooks/useAutosave.ts` | 前端基座能力：`useBase()`、`BASE_CAPABILITIES`、`receiptFromError` / `receiptFromPreflight`（localStorage 持久化的统一入口） |
| `web/src/components/ui/SaveStatusIndicator.tsx` | 基座原语 `BaseBound` 的定义处（配合 `useBase` 声明「已接入」） |

> **诚实标注（L1 要素散落在 L2 目录内）**：上表中的 `runtime/hooks.py` 物理位于 `runtime/`
> 目录（整目录在 L2）。**分层判据是「职责」，不是「目录」**：`hooks.py` 的 `HOOK_EVENTS` 是
> 底座级共享契约，故逻辑上归 L1。同类情形还有 `api/errors.py`——它逻辑上归 L1，却反向
> import 了 `services/errors.py` 的 `DomainError`（L1→L2 反向依赖共 **3 处**，见 §6 已知债务
> V2）：说明 `DomainError`
> 事实上也是底座契约的一部分，却物理落在 `services/` 内。这两例是解环工作需一并处理的
> 「基座要素未归位」问题。

### 3.2 `L2` —— 领域能力与执行引擎

**职责**：承载全部业务能力（领域服务）与执行机制（运行时引擎、图、模型网关、事件总线、
适配器、工作流编排）。**允许** L2↔L2 同层依赖；**禁止** L2→L3。

**后端归属**：

| 路径 | 作用 | 实测计数 |
|---|---|---|
| `src/find_yourself/services/` | 领域服务（actor / audit / budget / …） | **171** |
| `src/find_yourself/runtime/` | 执行引擎（`graph` / `kernel` / `gateway` / `sse` / `temporal` / …；`hooks.py` 除外，见 §3.1） | **23** |
| `src/find_yourself/adapters/` | 外部适配器（a2a / mcp / s3 / media / …） | **14** |
| `src/find_yourself/workflows/` | 工作流定义与 worker | **7**（侦察记 8，见 §8） |
| `src/find_yourself/charts/` | 图表引擎（engine / interpreter / retrieval） | — |
| `src/find_yourself/skills/` | 技能发现与 harness | — |
| `find_yourself_dsl/`（顶层包） | DSL 引擎（`_engine` / `_graph` / `_schema`） | — |

**前端归属**：

| 路径 | 作用 | 实测计数 |
|---|---|---|
| `web/src/api/*.ts`（除 `client.ts` / `types.ts`） | 领域 API 客户端（`tasks.ts` / `hub.ts` / `kanban.ts` / …） | 非测试 `.ts` 合计 **39**，其中 L1 占 2，L2 领域客户端 **37**（侦察记 38，见 §8） |

### 3.3 `L3` —— 接口与界面

**职责**：对外接口（HTTP 路由、应用装配、请求/响应 DTO）与用户界面（页面、业务组件）。
**允许** L3→L2、L3→L1；**禁止** 被 L1/L2 反向依赖。

**后端归属**：

| 路径 | 作用 | 实测计数 |
|---|---|---|
| `src/find_yourself/api/routes/` | HTTP 路由（49 个路由模块 + `__init__`） | **63** |
| `src/find_yourself/api/app.py` / `main.py` / `deps.py` / `oidc.py` | 应用装配、依赖注入、OIDC 装配 | — |
| `src/find_yourself/api/schemas.py` | HTTP 请求/响应 DTO | — |

**前端归属**：

| 路径 | 作用 | 实测计数 |
|---|---|---|
| `web/src/pages/` | 页面 | 非测试 **28**（全部含测试 51，与侦察一致） |
| `web/src/components/` | 业务组件（除 `components/ui/` 原语） | 非测试 `.tsx` **113**（侦察记 114，见 §8） |
| `web/src/App.tsx` | 路由装配 | — |

---

## 4. 依赖方向规则（规范性 · 可机检）

### 4.1 规则陈述

设层序 `L3 > L2 > L1`（数值越大越靠上）。**令 `dep(src, dst)` 表示源码 `src` 出现对 `dst`
的 import。** 则：

- **R1（允许，向下）**：`dep(L3, L2)` ✅
- **R2（允许，向下越级）**：`dep(L3, L1)` ✅
- **R3（允许，向下）**：`dep(L2, L1)` ✅
- **R4（允许，同层）**：`dep(L2, L2)` ✅ —— 同层依赖合法；但**须保持无环**（见 R7）。
- **R5（禁止，向上）**：`dep(L1, L2)` ❌
- **R6（禁止，向上）**：`dep(L1, L3)` ❌、`dep(L2, L3)` ❌
- **R7（纪律，同层无环）**：L2 内部子模块之间**不得成环**（`A → B` 且 `B → A`）。环不违反
  R1–R6 的方向规则，但违反可单测 / 可替换纪律，须由解环切片消解。

### 4.2 判定表（允许 / 禁止 import）

| 源层 `src` | 目标层 `dst` | 判定 | 后端 import 样式 | 前端 import 样式 |
|---|---|---|---|---|
| L3 | L2 | ✅ 允许 | `api/routes/x.py` → `from ...services.*` | `pages/*` → `../api/tasks` |
| L3 | L1 | ✅ 允许 | `from ...contracts` / `from ...config` / `from ..errors` | `pages/*` → `../api/client` / `../hooks/useAutosave` |
| L2 | L1 | ✅ 允许 | `services/x.py` → `from ..contracts` / `from ..config` | `api/tasks.ts` → `./client` |
| L2 | L2 | ✅ 允许（须无环） | `services/x.py` → `runtime/y.py`（合法方向）；`services/adapters` 侧另有 **4 个同层真环**，见 §6 | `api/a.ts` → `./b` |
| L1 | L1 | ✅ 允许（须无环） | `db/*` 内部互引 | `client.ts` ⇄ `types.ts` |
| L1 | L2 | ❌ 禁止 | **2 处**：`db/models.py:34`、`db/workbench_models.py:45`（已知债务 V2） | `client.ts` → `./tasks` |
| L2 | L3 | ❌ 禁止 | `from ..api.routes` | `api/*.ts` → `../pages/*` |
| L1 | L3 | ❌ 禁止 | `from ...api.routes` | `client.ts` → `../pages/*` |

### 4.3 越层直连的判定约定

「越层直连」不是方向违规，而是**封装约定违规**：L3 应经由 L2 的**稳定接口**（service）访问
能力，而非直接绑死 L2 的具体实现模块。因此对以下形态，按 **W 级警告**（不是 R 级错误）处理，
但**禁止新增**：

- **W1**：`api/routes/*.py` 直接 import `runtime.*`（绕过 service）。**当前 11 处 / 9 文件**
  （`tasks.py:20-21`、`runtime.py:17-18`、`bus.py:25`、`collaboration.py:30`、`inference.py:14`、
  `streaming.py:13`、`catalog.py:164`、`a2a.py:44`、`dsl_lifecycle.py:114`）。
  其中 `catalog.py:164`、`a2a.py:44`、`dsl_lifecycle.py:114` 为**函数内惰性 import**。

> **方向说明（重要）**：在 L1/L2/L3 模型中，`services` 与 `runtime` **同属 L2**，故
> route→runtime 技术上仍是 `L3→L2`（方向合法）。W1 的「越层」框定，**假设 `runtime` 是
> 比 `services` 更底层的执行引擎**——该子层顺序由 ADR-0001 的 §Decision 确定。因此 W1 的
> 严重度取决于子层口径：若采纳「runtime 为引擎底座、services 为其上领域服务」，则
> route→runtime 是**跨越 services 直连引擎**，属封装违规（W 级）。

---

## 5. 统一底座（L1）五要素收口

「统一底座」指下列五类共享能力**各自只有一个权威落点（L1）**，其余位置只能引用、不得另起
一套。这是 N7「统一」含义的可机检落点。

| # | 要素 | 权威落点（L1） | 证据 | 当前偏离 |
|---|---|---|---|---|
| 1 | **契约单源** | `find_yourself/contracts.py`（后端）；`web/src/api/types.ts`（前端镜像，非权威） | `contracts.py:17,21,27` | `api/types.ts` 手工镜像；OpenAPI/SDK 生成不在 Makefile、不在 CI（G7-d）。**`slice(3)` 派生扩展节点集** 全仓仅命中 `web/src/api/dslCanvas.ts:54` 一处，Python 侧 `services/dsl_canvas.py` **未发现**同写法（侦察称 `dsl_canvas.py:87` 亦有一处，未能复验，见 §8） |
| 2 | **错误信封** | `api/errors.py::_envelope`（+ `services/errors.py::DomainError` 作为领域异常基类） | `errors.py:25-26` | 前端 189 处自建错误处理未收口（G7-f）；10 处裸 `fetch` 绕过 `client.ts`（G7-j） |
| 3 | **配置读取** | `config.py::Settings` | `config.py:8-9` | 约 30 处配置读取旁路（G7-k） |
| 4 | **事件注册表** | `runtime/hooks.py::HOOK_EVENTS`（25 个生命周期事件） | `hooks.py:28`、`:53`（`WIRED_EVENTS`） | 存在第二套领域注册表 `services/dsl_sdk.py`（`VERB_REGISTRY` / `REDUCER_REGISTRY` / `CUSTOM_NODE_REGISTRY`）（G7-i 两套事件注册表） |
| 5 | **localStorage 持久化** | `web/src/hooks/useAutosave.ts`（`useBase` / `BASE_CAPABILITIES` / `receiptFromError`） | `useAutosave.ts`（`useBase`、`BASE_CAPABILITIES`、`receiptFromError`） | 30 处私建 localStorage 键绕过 `local_first`（G7-g） |

**基座契约（前端侧强制门禁）**：`services/quality/base_contract.py`
（`BASE_CONTRACT_VERSION="1.0.0"`；`BASE_CAPABILITIES` 四项 = `realtime_save` / `audit_trail` /
`local_first` / `error_receipt`；扫描面 = `web/src/pages` + `web/src/components`；硬规则
「可编辑文件不得豁免」；豁免落 `.runtime/quality/base_exemptions.json`）。该模块是 L1 底座
**已落地的一条强制收口线**，也是本模型「五要素收口」的先例与其工程范式。

---

## 6. 已知偏离与债务登记（供门禁 baseline）

下列为**当前基线**中与本规范不符之处。它们**不是**本文件的缺陷，而是「三层尚未落地」的真
实状态；后端分层门禁（G7-b）已落地，本节计数即其 baseline，随后逐项收敛。

**门禁权威入口**：`scripts/p0/run_layering_gate.py`（`ast` 精确解析 import + Tarjan SCC 判环），
baseline 为 `scripts/p0/layering_baseline.json`。二者是本节所有数字的**唯一权威来源**；有人质疑
本节数据时，应以该门禁的实测输出为准。门禁带 **stale 检测**：已不再复现的 baseline 条目会被
列为「可裁剪」（**baseline 只能变短不能变长**），避免它慢慢变成一张永不过期的免死金牌。

| 编号 | 偏离 | 实测 | 归类 | 证据 |
|---|---|---|---|---|
| **V1** | L2 内部成环（**同层真环**，不含 `runtime ⇄ services`） | **4 个环**（1 个 6 模块大环 + 3 个 2 模块环），逐边核实见下表 | R7 | 见下方「V1 环清单」 |
| **V2** | L1 反向依赖 L2 | **2 处** | R5 | `db/models.py:34`、`db/workbench_models.py:45` |
| **W1** | 路由层越层直连 runtime（封装违规，**warn 级**） | **11 处 / 9 文件** | §4.3 | `tasks.py:20-21`、`runtime.py:17-18`、`bus.py:25`、`collaboration.py:30`、`inference.py:14`、`streaming.py:13`、`catalog.py:164`、`a2a.py:44`、`dsl_lifecycle.py:114` |

**V1 环清单（L2 同层真环，4 个；逐边 Grep 核实为真环）**

| # | 环路径 | 关键边证据 |
|---|---|---|
| 1 | `adapters/mcp.py` → `services/tool_registry.py` → `services/knowledge/__init__.py` → `services/knowledge/hub_bridge.py` → `services/hub/connections.py` → `services/hub/adapters.py` → `adapters/mcp.py` | `mcp.py:1220` → `tool_registry`；`tool_registry.py:66` → `.knowledge`；`knowledge/__init__.py:30` → `.hub_bridge`；`hub_bridge.py:107` → `..hub.connections`；`connections.py:31` → `.adapters`；`hub/adapters.py:499` → `...adapters.mcp` |
| 2 | `adapters/community_harness_adapter.py` ⇄ `services/hub/access.py` | `access.py:340` → `...adapters.community_harness_adapter`；`community_harness_adapter.py:862` → `find_yourself.services.hub.access`（**绝对导入**） |
| 3 | `services/dsl_canvas.py` ⇄ `services/dsl_ir.py` | `dsl_canvas.py:1933` → `.dsl_ir`；`dsl_ir.py:56` → `.dsl_canvas` |
| 4 | `services/knowledge/sources/__init__.py` ⇄ `services/knowledge/sources/baidu_pan.py` | `sources/__init__.py:28` → `.baidu_pan`；`baidu_pan.py:121` → `.`（回指包 `__init__`） |

> **复验结论（更正）**：`services→runtime` = **21 处 / 15 文件**、`runtime→services` =
> **23 处 / 12 文件**（两者相加为 **44**，非 43；差额来自 G7-b 同期新增的
> `services/dsl_run_events.py:23` `from ..runtime.sse import bus`）、路由 `...runtime` =
> **11 处 / 9 文件** —— 路由一项与侦察数字一致。
>
> **「43 处 `runtime ⇄ services` 互锁」是错的，本节据此更正。** 43 = 20 + 23 是**两个方向各自
> import 计数相加**，**不等于「成环」**：成环要求存在**同一对**模块 A∈`runtime`、B∈`services`
> 同时满足 A→B 且 B→A。门禁的 `collect_d3_cross_pairs` 判据（专查 ADR-0001 D3 子层约定）
> 实测结果为 **`ADR-0001 D3 交叉环对（runtime→services 且 services→runtime 的同一对模块）: 0`** ——
> **零个交叉环对**。故 D3 子层约定在真实代码里**未被违反**，`runtime → services` 的 23 处是
> **单向**依赖（违反 D3 的方向约定，但**不构成 R7 意义上的环**）。
>
> **门禁当前状态**：`exit 0`，baseline **19 条** = **2 R5 + 4 V1 + 11 W1 + 2 D3R**
> （11 条 W1 严重度为 `warn`，不阻塞；2 条 D3R 严重度为 `hard`）。
>
> **D3R 是本规范落地过程中新增的规则族**（门禁原先对 `runtime → services` 是**双向盲区**，
> 既不查它也不查 `services → runtime` 的反向，因而无法区分「单向反向依赖」与「真环」）。
> 补上该规则后 baseline 一度涨到 40 条，随后经 G7-c 阶段 1/2/3a/3b 四轮下沉
> （errors → Actor → scheduler 内核 → hasher/anchors/audit/budget/offline 五模块整簇）
> **从 40 收敛到 19，净消解 21 条**。当前仅剩的 2 条 D3R 已判定为
> 「代价大于收益」与「改动即制造真环」，见 §6 末尾的处置方针。
>
> **R5 从 3 降到 2**：原 3 条中的 `api/errors.py:20` 已在阶段 1 随 errors 下沉 L1 一并消解。

**剩余 2 条 D3R 的处置方针（终态判断，2026-10-13）**

| 边 | 性质 | 方针 |
|---|---|---|
| `runtime/sandbox.py:41` → `services/terminal`（`kill_process_tree`） | 形态上是**纯进程工具函数、本属 L2.0 执行原语**，只是被归类错位放在 L2.1，不是真实行为耦合 | **暂不动**。下沉它要先解开 `terminal`(484 行) → `workspace`(846 行) → `snapshot`(244 行) 的长依赖链，为 1 条债付此代价不划算；若日后 terminal 簇本身需要治理，再单独立项一并处理 |
| `runtime/graph.py:152` → `services/agent_teams`（函数内惰性导入） | **真实行为耦合**（图节点内联组建团队），且改到模块级会**立刻制造真环**（`agent_teams` 本身依赖 `runtime`） | **绝对不动**。唯一正解是把组队逻辑**上移到 L2.1**，让 `runtime` 只接收已建好的团队——属独立立项，不在本轮范围 |

> ⚠️ **两条不可混为一谈**：前者是「位置错了」，后者是「职责错了」。前者可用搬迁解决，
> 后者只能靠职责重划。若把后者也当作搬迁来做，会直接把一条单向依赖升级成 R7 真环。

**V1 / R5 终态处置（2026-10-15，N7 收官）**

> 方法：6 条逐边 AST 实测（模块级 vs 函数内）+ 打破代价评估。修了 4 条（门禁逐条复核消解、
> baseline 19→15）；2 条永久接受（理由如下，门禁 baseline 保留）。

| # | 项 | 处置 | 证据 |
|---|---|---|---|
| V1-4 | `sources/__init__` ⇄ `baidu_pan` | **已修**：`secret_store` 实例下沉 `sources/store.py`（只依赖 `hub.secrets`）；包 `__init__` 重导出（`__all__` 不变）；`baidu_pan` 两处改从 `.store` 取 | 门禁「1 条已不再复现」；sources/hub 相关 174 passed |
| V1-2 | `community_harness_adapter` ⇄ `hub/access` | **已修**：`process_alive` + `kill_process_tree` 下沉 `hub/access.py`（CLI 超时杀整树本就是该模块职责）；adapter 顶部重导出（既有导入位零改动）；`access` 侧惰性导入删除 | 门禁消解；`adapter.kill is access.kill is True`；community/terminal/sandbox 相关 213 passed |
| R5-1 | `db/models` → `workflows.models`（Stage） | **已修**：`Stage` 本体下沉 `db/stages.py`（L1）；`workflows/models.py` 重导出（全网 `import Stage` 零改动）；`db/models` 改 L1 内部边 | 门禁消解；stage 约束/工作流相关测试绿 |
| R5-2 | `db/workbench_models` → `services.hub`（常量） | **已修**：`HUB_KINDS` 等整块常量下沉 `db/hub_constants.py`；`services/hub/__init__` 重导出（`__all__` 不变）；workbench 改 L1 内部边 | 门禁消解；hub 相关测试绿 |
| V1-1 | 6 模块网（mcp/tools/knowledge/hub_bridge/hub/connections/hub/adapters） | **永久接受**：12 条边中 11 条函数内惰性、仅 1 条模块级（`knowledge/__init__:30`）；**零导入时成环**（全量单测即证明）；网状源于 hub↔knowledge↔tools↔mcp 真实双向能力需求，重构 = 中台边界重划，属独立立项 | AST 逐边核实表见本轮 board §3.35 |
| V1-3 | `dsl_canvas` ⇄ `dsl_ir` | **永久接受**：`dsl_ir` 要的是 `VERB_REGISTRY` 等**行为闭包**（`execute` 字段绑定本模块 20+ 执行函数），搬迁 = 执行器裂变（10+ 调用方改注入签名）；反向边已是惰性，**零导入时成环**；打破 = 独立立项 | 同上 |

> ⚠️ **接受 ≠ 放任**：两条接受项保留在门禁 baseline 内（永久已知债务），新增边仍会被门禁拦截；
> 接受的是「现状」，不是「增量」。

---

## 7. 解环第一刀（规范指引 · 详见 ADR-0001）

后端「两层半」的根因是 `runtime` 与 `services` 的**双向依赖**：领域服务调用引擎原语
（`services → runtime`），引擎又反向绑定具体领域实现（`runtime → services`）。**N7 要落三层，
第一刀必须切在这条耦合上，而不是先补前端。**

**本规范的子层约定（由 ADR-0001 决策）**：

- 将 L2 内部再分两级次序：**`L2.0 执行引擎（runtime）` < `L2.1 领域服务（services）`**。
- 合法方向：`services → runtime`（领域服务调用引擎原语）；**禁止** `runtime → services`。
- 消解方向违规（`runtime → services`）与 V1 同层环的手段：把 `runtime` 对具体领域服务的依赖，
  **下沉为 L1 契约**（Protocol / 值对象 / 错误类型）或**改为依赖注入**，使引擎不再绑定业务实现。

**示例**：`runtime/gateway.py:45-48` 直接 import `Actor` / `BudgetService` / `DomainError` /
`OfflineUnavailable`。归位方向：`Actor` / `BudgetService` 应通过传入的协议接口访问（依赖倒置）；
`DomainError` 系错误类型本就是底座契约，应迁入 L1（→ 同时消解 V2）。

> **更正（2026-10-13，见 §6 复验结论）**：本节此前把 V1 描述为「`runtime ⇄ services`
> **互锁成环**」，该表述**不成立**——门禁实测 D3 交叉环对为 **0**，即不存在任何一对
> A∈`runtime`、B∈`services` 同时满足 A→B 且 B→A。当前真实债务应拆成**两项**：
> 1. **方向违规（非环）**：`runtime → services` 共 **23 处 / 12 文件**（`runtime/gateway.py:45-48`、
>    `kernel.py:31-32`、`graph.py:29-30` 等）。这违反 ADR-0001 D3 的「禁止 `runtime → services`」，
>    须按上面的示例逐项下沉 / 注入。
> 2. **R7 真环**：L2 内部另有 **4 个同层环**（清单见 §6），全部落在 `services` / `adapters` 侧，
>    与 `runtime ⇄ services` 无关。
>
> 两者都指向同一收敛目标（让引擎不绑定业务实现），但**计数与判据不同**，不可再合称「43 处互锁」。

---

## 8. 事实复验（本文件所有数字的来源）

本节记录撰写本文件时的**只读核实**结果，含与侦察不一致处（**如实推翻，不硬凑**）。

| 事实 | 侦察值 | 实测值 | 方法 | 结论 |
|---|---|---|---|---|
| `services/` 文件数 | 171 | **171** | 目录枚举 | ✅ 一致 |
| `runtime/` 文件数 | 23 | **23** | 目录枚举 | ✅ 一致 |
| `db/` 文件数 | 27 | **27** | 目录枚举 | ✅ 一致 |
| `adapters/` 文件数 | — | **14** | 目录枚举 | 新增 |
| `workflows/` 文件数 | 8 | **7** | 目录枚举 | ❌ **推翻侦察** |
| `api/routes/` 文件数 | 63 | **63** | 目录枚举 | ✅ 一致 |
| `web/src/pages/`（非测试） | 28 | **28**（全部 51） | 目录枚举 | ✅ 一致 |
| `web/src/components/`（非测试 `.tsx`） | 114 | **113** | 目录枚举 | ❌ **推翻侦察** |
| `web/src/api/*.ts`（非测试） | 38 | **39**（含 `client.ts`/`types.ts`） | 目录枚举 | ❌ **推翻侦察**（见下方口径说明） |
| `services→runtime` import | 20 | **21 处 / 15 文件** | `grep '^\s*(from\|import)\s+\.\.runtime' services/` | ⚠️ G7-b 同期新增 `services/dsl_run_events.py:23`，故 +1 |
| `runtime→services` import | 23 | **23 处 / 12 文件** | `grep '^\s*(from\|import)\s+\.\.services' runtime/` | ✅ 一致 |
| **「`runtime ⇄ services` 互锁」** | 43 处 | **交叉环对 0**（双向计数之和 ≠ 成环） | 门禁 `collect_d3_cross_pairs`（`ast`）+ 双向 Grep 交叉验证 | ❌ **推翻原表述**：43 = 20 + 23（侦察口径）是两方向计数相加，非环；按今日实测应为 21 + 23 = 44，同样非环 |
| L2 内部同层真环（R7） | — | **4 个**（1 个 6 模块 + 3 个 2 模块） | 门禁 Tarjan SCC + 逐边 Grep | 新增（清单见 §6） |
| L1 反向依赖 L2（V2 / R5） | 1 处 | **3 处** | 门禁 `collect_reverse_deps`（`ast`，覆盖绝对导入）+ Grep | ❌ **推翻原表述**：原 1 处只覆盖相对导入 |
| 路由越层直连 runtime | 11 处 / 9 文件 | **11 处 / 9 文件** | `grep '^\s*(from\|import)\s+\.\.\.runtime' api/routes/` | ✅ 一致 |
| 契约双份 `.slice(3)` | 2 处（`dsl_canvas.py:87` + `dslCanvas.ts:53`） | 全仓仅 **1 处**（`web/src/api/dslCanvas.ts:54`） | `grep '\.slice(3)'` | ⚠️ 部分推翻：Python 侧未命中该写法 |
| `HOOK_EVENTS` 事件数 | — | **25** | `runtime/hooks.py:28-50` | 与 `hooks.py:5` 自述一致 |
| N7 门禁定义文本 | `ci.yml:192` | **`.github/workflows/ci.yml:189-199`** | 读文件 | ✅ 一致（注释块 191-197 行） |

**口径说明**：
- `workflows/` 实测 7（`__init__.py`、`activities.py`、`fake.py`、`models.py`、`ports.py`、
  `worker.py`、`workflow.py`）—— 侦察 8 多计 1，**以 7 为准**。若侦察把某个已删除/未落盘文件
  计入，本文件无从复验，故**如实记为 7**。
- `components/` 实测非测试 `.tsx` = 113；若把 `components/ui/` 的基座原语按 L1 单列，则 L3 的
  业务组件更少。侦察 114 与实测 113 **差 1**，以 113 为准。
- `api/*.ts` 实测非测试 = 39（含 `client.ts`、`types.ts`）。侦察 L2 记 38，其口径应已把
  `client.ts` / `types.ts` 归 L1；则 L2 领域客户端 = 39 − 2 = **37**。侦察的 38 落在 37–39 之间，
  **本文件以「非测试 39 / L2 领域客户端 37」双口径并列澄清**，不强行对齐 38。
- `slice(3)`：全仓仅 `web/src/api/dslCanvas.ts:54` 命中。侦察称 `services/dsl_canvas.py:87` 亦有
  一处派生扩展节点集，但该处**不是** `.slice(3)` 字面写法（可能为 `[3:]` 或经变量派生），本次
  grep **未能复验**。故「契约双份」结论**保留为待查（G7-e 处理），不作定论**。

**未能复验 / 存疑（诚实记录）**：
1. 侦察称 `.slice(3)` 有 **2 处**，本次仅复核到前端 1 处 —— Python 侧对应位置未定位，列为待查。
2. 侦察 §9.3 称前端 `components/ui/` 属 L2、`components/`(114) 属 L3；但 `components/`(113) 的枚举
   **是否已包含 `components/ui/`** 未澄清 —— 若包含，则存在重复计数。本文件按「`components/ui/`
   为基座原语、其余 `components/` 为 L3 业务组件」定义，并**标注该计数口径待 G7-b 落地时以 glob
   精确界定**。
3. `services/quality/base_contract.py` 的模块 docstring 自述「21 个页面尚未接线」（`base_contract.py:16`），
   而 `ci.yml:196` 自述「1 个可编辑页面未接入（`FortunePage.tsx`）」，两者**自相矛盾**（对应侦察
   G7-n「门禁自述失真」）。本文件如实并列两处自述，**不自选其一**。

---

## 9. 供 CI 引用的锚点（G7-b 接口）

以下为**稳定标识**，供后端分层门禁（G7-b）等后续切片直接引用。

### 9.1 层标识

```
L1 = 契约与基础设施（底座）
L2 = 领域能力与执行引擎
L3 = 接口与界面
```

### 9.2 目录锚点（可 glob / grep）

```
# 后端 L1
BACKEND_L1 = src/find_yourself/{contracts.py,config.py,api/errors.py,runtime/hooks.py,identity.py,scheduling.py,hashing.py,anchors.py,audit.py,budget.py,offline.py} + src/find_yourself/db/**
# 后端 L2
BACKEND_L2 = src/find_yourself/{services/**,runtime/**,adapters/**,workflows/**,charts/**,skills/**} + find_yourself_dsl/**
# 后端 L3
BACKEND_L3 = src/find_yourself/api/{routes/**,app.py,main.py,deps.py,oidc.py,schemas.py}

# 前端 L1
FRONTEND_L1 = web/src/{api/client.ts,api/types.ts,hooks/useAutosave.ts,components/ui/SaveStatusIndicator.tsx}
# 前端 L2
FRONTEND_L2 = web/src/api/*.ts   # 排除 client.ts / types.ts
# 前端 L3
FRONTEND_L3 = web/src/{pages/**,components/**,App.tsx}
```

### 9.3 规则锚点（R1–R7）

门禁应实现 R1–R7（§4.1）、§4.3 的 W1 与 ADR-0001 D3 的 D3R。初始 baseline 见 §6（V1 / V2 / W1）。

**已落地门禁**：G7-b 已实现上述判据，权威入口 `scripts/p0/run_layering_gate.py`
（`PYTHONPATH=<tree>/src python scripts/p0/run_layering_gate.py`，可用 `--w1 hard|warn` 切换
W1 严重度、`--report-cycle-paths` 打印环路径），baseline 为 `scripts/p0/layering_baseline.json`
（当前 **19 条** = 2 R5 + 4 V1 + 11 W1 + 2 D3R）。门禁带 **stale 检测**：已不再复现的 baseline 条目会被
提示裁剪（**baseline 只能变短不能变长**）。

> **L1 是「点名单」机制**（`classify_layer()`）：只有被列进点名单的模块才判为 L1，
> 不在名单里返回 `None`。因此**新建 L1 模块必须同步加入点名单**，否则依赖它的边会因
> 「目标不属于任何层」而**假性消失**——门禁显示绿灯但债其实还在。当前点名单见 §9.2 的
> `BACKEND_L1`（11 个模块 + `db/**`），与门禁代码中的元组必须逐项一致。

### 9.4 违规识别样式（后端相对导入 · **口径有盲区**）

```text
# runtime→services（L2 内部，R7 关注）
^\s*(from|import)\s+\.\.services
# services→runtime（L2 内部，R7 关注）
^\s*(from|import)\s+\.\.runtime
# 路由→runtime（W1 越层直连，注意路由多一层用三点）
^\s*(from|import)\s+\.\.\.runtime
# 任何 L2/L1 反向 import api（R5/R6）
^\s*(from|import)\s+\.\.api|^\s*(from|import)\s+\.\.\.api
```

> **⚠️ 口径盲区（必读，勿再据此下结论）**：上述正则**只覆盖相对导入**，**会整类漏掉绝对导入**
> 的等价违规。已实测的漏检实例：
> - `db/workbench_models.py:45` `from find_yourself.services.hub import CONNECTION_STATES, HUB_KINDS`
>   —— L1→L2 的 R5 违规，但**不匹配**上面任何一条正则；
> - `adapters/community_harness_adapter.py:862` `from find_yourself.services.hub.access import ...`
>   —— §6 环 2 的回指边，同样查不出。
>
> 这正是 §6 V2 原记「1 处」而真实为 **3 处**的原因：原口径只看见了 `api/errors.py:20`
> 那一条**相对导入**。已落地的门禁改用 **`ast` 精确解析 import**，相对 / 绝对导入一律覆盖，
> 并以 Tarjan SCC 做同层成环检测。本节正则**仅保留为口径说明与人工 grep 速查**，不作判定依据。

---

## 修订记录

| 版本 | 日期 | 修订人 | 修订原因 | 证据来源 |
|---|---|---|---|---|
| `1.0.1` | 2026-10-13 | 架构组 | **数据更正**（不改决策、不改规则，仅更正已失效的实测数字）：① §6/§7/§8 的「43 处 `runtime ⇄ services` 互锁成环」表述不成立——43 是 `services→runtime` 与 `runtime→services` 两个方向各自 import 计数**相加**，不等于成环；门禁实测 ADR-0001 D3 **交叉环对为 0**。② §6 V2 由 1 处更正为 3 处（原口径只覆盖相对导入，漏掉绝对导入形态）。③ §6 V1 补入 L2 同层**真环**清单（4 个）。④ §4.2 判定表、§3.1 诚实标注、§9.3 / §9.4 同步更新，并标注 §9.4 正则口径的盲区。 | `scripts/p0/run_layering_gate.py` 实测（`exit 0`；R5 3 / V1 4 / W1 11 / D3 交叉环对 0）+ baseline `scripts/p0/layering_baseline.json`（18 条）+ 对 4 个环与 3 处 V2 的逐边 Grep / Read 复核 |
| `1.0.2` | 2026-10-13 | 架构组 | **数据同步**（决策与规则仍不变，仅把 §4.2 判定表与 §6 债务表对齐到 G7-c 四轮下沉后的实测现状）：① §4.2 判定表 `L1 → L2` 行与 §6 债务表 **V2 行**由「3 处」更正为 **2 处**——原 3 条中的 `api/errors.py:20` 已在阶段 1 随 errors 下沉 L1 一并消解（同文件 §6 末尾的「R5 从 3 降到 2」此前只有注解、未回填到这两张表，本次补齐）。② 修订记录补记 `1.0.1` 行之后发生的四项演进：新增 **D3R 规则族**致 baseline 40 → 四轮下沉 → **19 条**（2 R5 + 4 V1 + 11 W1 + 2 D3R）、消解手段为「整簇下沉 L1 + 留垫片」而非原设想的抽 Protocol。 | `scripts/p0/run_layering_gate.py` 实测（`exit 0`；`scanned=340`；新增违规 **0**；19 条 baseline 全匹配、无 stale）+ `scripts/p0/layering_baseline.json` + 对 `api/errors.py:20` 已无 L2 依赖的 Grep 复核 |

> 本次为**对已 Accepted 文档的数据更正**，不新增 ADR、不改变任何规则判定；相关决策
> 见 [`ADR-0001`](../adr/0001-three-layer-architecture.md) 的 D3 / D4（方向约定本身不变，
> 只是把「已成环」的预判更正为「单向方向违规 + 另存在 4 个同层环」）。

---

## 附：术语与产品名对照

- 产品名统一书写：**FY-Orbit · 星轨**。
- 后端包名：`find_yourself`（源码根 `src/find_yourself/`）。
- 前端根：`web/`（源码 `web/src/`）。
- 层标识恒为 `L1` / `L2` / `L3`（大写字母 + 数字），供 CI/lint 文本匹配。
