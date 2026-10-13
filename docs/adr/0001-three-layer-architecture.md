# ADR-0001：采用三层分层统一底座

| 字段 | 值 |
|---|---|
| 编号 | `0001` |
| 标题 | 采用三层分层统一底座（L1 契约底座 / L2 领域与引擎 / L3 接口与界面） |
| 状态 | **Accepted** |
| 日期 | 2026-10-13 |
| 决策者 | 架构组（FY-Orbit · 星轨） |
| 相关定义 | [`../architecture/three-layer-model.md`](../architecture/three-layer-model.md)（本文档为其决策记录） |
| 需求来源 | N7「三层分层统一底座」 |

---

## Context（背景）

「三层」这个概念在 FY-Orbit 仓库里**从未被正式定义**。全仓对它的唯一提及是
`.github/workflows/ci.yml:192` 的一句注释（N7 工程闸门的说明文字）：

> 需求 N7「三层分层统一底座」的工程闸门：所有页面 / 可编辑组件必须接入基座（useBase / BaseBound）…

也就是说：**有需求、有一句注释、有一个只覆盖前端「基座接入」的局部闸门，但没有架构文档、
没有 ADR、没有分层判据。** 这带来三个直接问题：

1. **无法机检**：后续任何「分层门禁」都缺判据；「三层」是什么、哪层能 import 哪层，无据可依。
2. **后端事实上有「两层半」**：核实发现 `runtime/` 与 `services/` 存在**双向依赖** ——
   `services→runtime` **21 处 / 15 文件**、`runtime→services` **23 处 / 12 文件**（两向计数之和 44）。
   例：`runtime/gateway.py:45-48`、`kernel.py:31-32`、`graph.py:29-30` 依赖
   `services/actor`、`services/budget`、`services/errors`；而 `services/agent_teams.py:41`、
   `services/model_catalog.py:27-28,37` 又依赖 `runtime/gateway`、`runtime/providers`。
   **更正（2026-10-13）**：此前的「**互锁成环**…合计 43」表述不成立 —— 43 只是两个方向各自
   import 计数相加，**不等于成环**；成环要求存在同一对模块 `A∈runtime`、`B∈services` 同时满足
   `A→B` 与 `B→A`，实测交叉环对为 **0**（见定义文档 §6）。L2 内部真实存在的同层环是另外
   **4 个**，全部落在 `services/` / `adapters/` 侧，而非 `runtime ⇄ services` 之间。
   尽管不成环，`runtime` 与 `services` 的双向依赖仍使**后端无法被清晰切成两层**，
   N7 的「三层」在后端断裂。
3. **前端真有三层但无规则**：`web/src/api/`（领域客户端）→ `components/` → `pages/` 的层次客观
   存在，但**没有任何 import 规则强制它**；并且存在 `api/routes/*` 越过 service 直连 runtime
   的**越层直连 11 处 / 9 文件**。

**约束与前提**：
- 本仓库是 Python(FastAPI) + TypeScript(React) 的**单体仓库**，前后端同仓。
- 已有一条**已落地的底座强制线**可作范式：`services/quality/base_contract.py`（基座接入门禁，
  `BASE_CONTRACT_VERSION="1.0.0"`，四项能力 `realtime_save` / `audit_trail` / `local_first` /
  `error_receipt`）。它证明「底座能力可被 CI 强制收口」在本仓是可行的。
- 术语需统一：产品名 **FY-Orbit · 星轨**；后端包 `find_yourself`；前端 `web/`。

---

## Decision（决策）

**采用三层分层统一底座，并以稳定层标识 `L1` / `L2` / `L3` 固化定义。**

### D1. 层定义与目录归属

| 层 | 名称 | 后端 | 前端 |
|---|---|---|---|
| **L1** | 契约与基础设施（底座） | `contracts.py`、`config.py`、`api/errors.py`、`db/`、`runtime/hooks.py`（事件注册表） | `api/client.ts`、`api/types.ts`、`hooks/useAutosave.ts`、`components/ui/SaveStatusIndicator.tsx` |
| **L2** | 领域能力与执行引擎 | `services/`(171)、`runtime/`(23)、`adapters/`(14)、`workflows/`(7)、`charts/`、`skills/`、`find_yourself_dsl/` | `api/*.ts`（除 client/types） |
| **L3** | 接口与界面 | `api/routes/`(63)、`api/app.py`/`main.py`/`deps.py`/`oidc.py`/`schemas.py` | `pages/`(28)、`components/`(113 非测试)、`App.tsx` |

> 完整目录清单与逐条证据见定义文档 [§3](../architecture/three-layer-model.md#3-层定义)。

### D2. 依赖方向规则（规范）

设层序 `L3 > L2 > L1`。**允许**：`L3→L2`、`L3→L1`、`L2→L1`、同层 `L2↔L2`。
**禁止**：`L1→L2`、`L1→L3`、`L2→L3`（即**禁止任何向上依赖**）。**纪律**：L2 内部须无环。
完整判定表见定义文档 [§4](../architecture/three-layer-model.md#4-依赖方向规则规范性--可机检)。

### D3. 子层约定：`runtime` 为引擎底座，`services` 为其上领域服务

在 L2 内部引入次序：**`L2.0 执行引擎（runtime）` < `L2.1 领域服务（services）`**。
合法方向 `services → runtime`；**禁止** `runtime → services`。
该项是为 D4（切断反向依赖）服务的可执行判据。

### D4. 解耦第一刀：切断 `runtime → services` 反向依赖

**N7 要落三层，第一刀切在 `runtime → services` 这条反向依赖上，而不是先补前端。**
消解方式：把 `runtime` 对具体领域服务的依赖**下沉到 L1 契约**（Protocol / 值对象 / 错误类型）
或**改为依赖注入**（依赖倒置），使执行引擎不再绑定业务实现。

> **更正（2026-10-13）**：此标题原作「解环第一刀：切断 `runtime ⇄ services` 环」。实测
> `ADR-0001 D3 交叉环对: 0`，`runtime` 与 `services` 之间**并不存在环**，只有 23 处
> `runtime→services` 的**方向违规**（D3 禁止项）。L2 内部真正的 4 个同层环是独立问题，
> 归属 G7-c 一并处理，清单见定义文档 §6。

### D5. 统一底座（L1）五要素收口

五类共享能力**各归 L1 单点**，其余位置只引用不另起：契约单源（`contracts.py`）、错误信封
（`api/errors.py::_envelope`）、配置读取（`config.py::Settings`）、事件注册表
（`runtime/hooks.py::HOOK_EVENTS`）、前端 localStorage 持久化（`hooks/useAutosave.ts`）。
详见定义文档 [§5](../architecture/three-layer-model.md#5-统一底座l1五要素收口)。

### D6. 分层以职责判定，而非仅以目录判定

`runtime/hooks.py`（物理在 L2 目录）逻辑归 L1；`services/errors.py::DomainError` 是底座契约却
物理落在 `services/`。**基座要素散落在 L2 目录内**是已知的归位债务，解环时一并处理。

---

## Consequences（后果与代价）

### 收益

- 「三层」有了**唯一、可机检的定义源**，解锁 G7-b（后端分层门禁）、G7-c（解环）、
  G7-e（契约 parity）等后续切片 —— 这正是 N7 的**前置依赖**。
- 后端「两层半」有了明确的**收敛目标与方向**（D3/D4），可增量收敛而非一次性重构。
- 前端既有的 `api → components → pages` 层次从「事实」升级为「规则」，可加 lint 门禁。
- 统一底座五要素（D5）有了单点归属，为「错误处理收口 / localStorage 收口 / 契约不漂移」
  提供判据。

### 代价与风险

- **短期会出现「文档定义 ≠ 代码实况」的落差**：门禁（G7-b）已落地，实测 baseline 为 **19 条** =
  **2 条 R5**（`L1→L2` 反向依赖）+ **4 条 V1**（L2 内部同层环）+ **11 条 W1**（越层直连，
  严重度为 `warn`，不阻塞）+ **2 条 D3R**（`runtime→services` 单向反向依赖，严重度 `hard`）。
  门禁当前 `exit 0`，新增违规 0；baseline 只允许变短，变长即报 **stale**。
  > **更正（2026-10-13）**：此条原作「当前基线有 43 处 L2 内部环、1 处 L1→L2 反向依赖、11 处
  > 越层直连」。**两处数字均已被推翻**：43 是 `services→runtime` 与 `runtime→services` 双向
  > 计数之和，不等于环，D3 交叉环对实测为 **0**；`L1→L2` 反向依赖实际为 **3 处**（原 1 处
  > 只覆盖了相对导入口径，漏掉 `db/workbench_models.py` 的绝对导入）。19 条 baseline 的权威
  > 来源为 `scripts/p0/layering_baseline.json`。
  >
  > **再次更正（2026-10-13，G7-c 阶段 0–3b 后）**：① 补上 **D3R 规则**后 baseline 一度涨到
  > **40 条**（原口径完全看不到 `runtime→services` 这 23 处），经四轮下沉收敛到 **19 条**；
  > ② R5 从 3 降到 **2**（`api/errors.py` 随 errors 下沉 L1 一并消解）；
  > ③ **消解手段是「整簇下沉 L1 + 留垫片」，不是原设想的「抽 Protocol」**——
  > 实测证明这些实体的依赖链本就全在 L1 或簇内，Protocol 属多余抽象。
- **D3/D4 的方向选择是有代价的决策**：把 `runtime→services` 判为禁止，意味着
  `runtime/gateway.py` 等对 `Actor`/`BudgetService` 的直接依赖必须改造（注入或下沉协议），
  这是**真实的解耦工作量**（G7-c，侦察评估工作量为「大」）。
- **`L2.0 < L2.1` 是人为规定的次序**，并非仓库现状自然具备；它需要在门禁中显式编码，并可能
  在个别模块上遭遇例外（如 `runtime/scheduler_bridge.py`、`runtime/parallel_policy.py` 对
  `services/scheduler` 的依赖 —— 需在 G7-c 中逐一判归属）。
- **计数口径需在实现时以 glob 精确化**：定义文档 §8 记录了 `workflows/`=7、`components/`=113、
  `api/*.ts`=39 三处与侦察数字不一致，以及 `slice(3)` 仅 1 处。门禁定 baseline 时应以**实测
  glob** 为准，不要沿用侦察数字。

### 后续动作（解锁项）

1. **G7-b**：按定义文档 §9 的锚点实现后端分层门禁，baseline 取 §6（V1/V2/W1）。
   —— **已落地**（2026-10-13）：`scripts/p0/run_layering_gate.py` + `scripts/p0/layering_baseline.json`
   （19 条），门禁 `exit 0`。
2. **G7-c**：按 D3/D4 切断 `runtime → services` 反向依赖（23 处 / 12 文件），并解 L2 内部 **4 个
   同层环**（清单见定义文档 §6），同时消解 V2（3 处 `L1→L2`，含 `DomainError` 归位）。
3. **G7-e / G7-d**：补齐契约 parity 测试与 OpenAPI/SDK 生成进 CI。

---

## Alternatives Considered（备选方案）

### 备选 A：不定义，继续只保留一句注释

**否决理由**：这正是现状。缺判据导致每个后续切片各自理解「三层」，无法机检、无法收敛；
且侦察已证后端事实上无三层。**维持现状等于放弃 N7。**

### 备选 B：两层（后端 = services+api；前端 = src+pages）

**否决理由**：忽略 `runtime/` 这一独立的执行引擎维度（图 / 内核 / 网关 / 总线 / 时态），
会把「引擎机制」与「领域服务」混为一层，正是当前 `runtime` 与 `services` 双向依赖的成因之一。
两层也无法安置「契约底座」（L1）——而本仓已有 `contracts.py`/`config.py`/统一错误信封等
明确的底座要素。

### 备选 C：四层（把 L2 拆为「领域服务」与「执行引擎」两层，各占一层）

**否决理由**：`runtime` 与 `services` 当前是**双向依赖**而非清晰上下级，强行拆成两个正式层，
会立刻产生大量跨层违规（实测 `runtime→services` 23 处 / 12 文件），门禁无法以合理 baseline
落地；且 `adapters/` 归属会变得含混
（它与 `services`/`runtime` 均有耦合）。本 ADR 选择**三层 + L2 内部子层次序（D3）**：
既承认 `runtime`/`services` 的差异（可作门禁判据），又不在目录层面强行切分（保留增量收敛空间）。

### 备选 D：五层及以上 / 引入 Clean Architecture 的 use-case 层

**否决理由**：对当前体量的单体仓库过度设计。Clean Architecture 的 use-case 层在本仓可由
`services/` 承担；额外分层会显著增加目录与搬运成本，而收益（可替换性）在本仓阶段不迫切。
N7 的诉求是「统一底座」而非「重架构」，三层（+ 底座）已足以表达。

---

## 引用与出处

- 定义文档：[`docs/architecture/three-layer-model.md`](../architecture/three-layer-model.md)
- N7 门禁出处：`.github/workflows/ci.yml:189-199`
- 底座强制线范式：`src/find_yourself/services/quality/base_contract.py`
- 统一错误信封契约：`src/find_yourself/api/errors.py:25-26`（FROZEN_CONTRACT §1）
- 事件注册表真源：`src/find_yourself/runtime/hooks.py:28,53`
- 分层门禁（权威入口）：`scripts/p0/run_layering_gate.py`、`scripts/p0/layering_baseline.json`

---

## 修订记录

本 ADR 状态仍为 **Accepted**（决策本身不变），以下为对文档中**事实性数据**的更正。

| 版本 | 日期 | 修订人 | 修订内容 | 修订原因 | 证据来源 |
|---|---|---|---|---|---|
| `0001.1` | 2026-10-13 | 架构组 | ① Context 第 2 条「`runtime/` 与 `services/` **互锁成环**…合计 43」→ 改为「**双向依赖**」，并注明交叉环对实测为 **0**；② D4 标题「切断 `runtime ⇄ services` 环」→ 「切断 `runtime → services` 反向依赖」；③ Consequences 首条「43 处 L2 内部环、1 处 `L1→L2` 反向依赖」→ 改为实测 baseline **18 条**（3 R5 + 4 V1 + 11 W1，W1 为 `warn` 级）；④ 后续动作 G7-c 的「解环（43 处）」→ 23 处方向违规 + 4 个同层环 + 3 处 V2；⑤ 备选 B/C 的「互锁」措辞 | G7-b 门禁落地后的实测结论推翻了原文两处数据：**43 是双向计数之和，不等于成环**（成环要求同一对模块 `A∈runtime`、`B∈services` 满足 `A→B` 且 `B→A`）；`L1→L2` 反向依赖实际为 3 处，原 1 处只覆盖相对导入口径 | `scripts/p0/run_layering_gate.py --report-cycle-paths`（`exit 0`，输出 `ADR-0001 D3 交叉环对: 0`、`R5 合计 3`、`V1 合计 4`、`W1 合计 11 [严重度=warn]`、`scanned=333`）、`scripts/p0/layering_baseline.json`（18 条 records）、逐边 Grep 核实（清单见定义文档 §6） |
| `0001.2` | 2026-10-13 | 架构组 | **决策不变、仅同步后果段数字**：`0001.1` 记录的 18 条 / 3 R5 是**当时的真实实测值**，故保留不改写；但其后 G7-c 阶段 0–3b 又发生了两处演进，本修订补记并使后果段与之一致：① 新增 **D3R 规则族**（原门禁对 `runtime → services` 是双向盲区，无法区分「单向反向依赖」与「真环」），补规则后 baseline 一度涨到 **40 条**，经四轮下沉收敛到 **19 条** = **2 R5 + 4 V1 + 11 W1 + 2 D3R**（W1 为 `warn`、D3R 为 `hard`）；② R5 由 3 降到 **2**（`api/errors.py:20` 随 errors 下沉 L1 消解），`L1→L2` 反向依赖亦由 3 处更正为 **2 处** | 补记而非改写：`0001.1` 的 18 条是 D3R 规则引入**之前**的真实基线，改写它会伪造当时的实测记录；此处只追加后续演进，使正文「代价与风险」首条（已写 19 条 / 2 R5）与修订日志自洽。剩余 2 条 D3R 已终态判定为「暂不动」与「绝对不动」，理由见定义文档 §6 末尾 | `scripts/p0/run_layering_gate.py`（`exit 0`，`scanned=340`，新增违规 **0**，19 条 baseline 全匹配无 stale）、`scripts/p0/layering_baseline.json`、逐边 Grep 复核 |
