# FY Orbit · 星轨 — 仓库体检与架构分析报告

- **仓库**：https://github.com/intp41455/FY-Orbit （public）
- **本地路径**：`C:\Users\intpj\Documents\Codex\2026-10-07\a-md\work\FY-Orbit`
- **分析日期**：2026-10-08
- **分支**：main

---

## 一、授权与清理（本次已执行）

### 1. 授权方案：BSL 1.1

| 项目 | 内容 |
|---|---|
| 许可证 | Business Source License 1.1 |
| 变更日 | 各版本首发后 4 年 |
| 变更许可 | Apache License 2.0 |
| 商用 | ❌ 禁止（需单独商业授权） |
| 联系方式 | intp41455@gmail.com |

**改动文件：**
- `LICENSE` — Apache-2.0 空模板（占位符 `[yyyy] [name of copyright owner]` 未填写、实际未生效、且允许商用）→ 全文重写为 BSL 1.1
- `README.md` — 徽章 `License-Apache 2.0` → `License-BSL 1.1`；插入中英双语「授权声明」段
- `NOTICE` — 补充 BSL 1.1 授权说明

### 2. 清理清单（已删除，历史保留在 git 中）

| 文件 | 问题 |
|---|---|
| `_p01_mcp_service.py` | AI 施工脚本，硬编码 `C:\Users\intpj\Documents\Codex\...` |
| `check_fixes.py` | 一次性修补脚本，硬编码本机路径 |
| `check_fixes_v2.py` | 同上 |
| `_tmp_verify_fontscale.cjs` | 临时 Playwright 验证脚本 |
| `TODO-2026-10-08.md` | 头部明写「本文件由 AI 助手生成」，含本机路径 |
| `landing-page-2026-10-06.html` | 临时落地页，含 trycloudflare 死链 |

### 3. 取消跟踪 / 忽略

- `FY-Orbit-v1.0.0-windows.zip` — 二进制发行包，改由 GitHub Release 分发（本地文件保留）
- `.wrangler/cache/pages.json` — Cloudflare 构建缓存
- `.gitignore` 追加：`.wrangler/`、`/FY-Orbit-v*.zip`、`/manual/`

### 4. 保留判定（扫描后确认正常）

- `AGENTS.md`（114 行）— **项目协作规范**，写的是技术栈/目录/工程铁律，非 AI 残留
- `evidence/`、`prompts_packages/`、`skills_packages/` — 项目功能组成部分
- `tests/` 中的内网 IP — 测试夹具模拟值，正常
- 全仓未扫到硬编码密钥（`sk-`/`AKIA`/`ghp_`/私钥头 全部为空）

---

## 二、仓库规模

### 后端（Python）

| 指标 | 数值 |
|---|---|
| 文件数 | 324 |
| 代码行数 | 95,362 |
| 顶层模块 | 10 + 8 个根级模块 |
| API 路由 | 62 个 route 文件 |
| 数据库迁移 | 43 个 Alembic 版本 |

**模块分布（按行数）：**

| 模块 | 文件 | 行数 | 职责 |
|---|---|---|---|
| services | 169 | 62,377 | 业务服务层（最大头） |
| api | 69 | 13,090 | FastAPI 路由 |
| runtime | 22 | 5,680 | 运行时内核（总线/检查点/委派） |
| adapters | 14 | 5,472 | 外部 Agent/工具适配器 |
| db | 27 | 3,841 | SQLAlchemy 模型 |
| workflows | 7 | 1,291 | Temporal 工作流 |
| charts | 5 | 1,175 | 图表 |
| skills | 3 | 827 | 技能加载 |

**services 子模块 Top：** knowledge(8,395) / cabin_life(4,577) / hub(3,061) / templates(2,071) / capability(1,999) / claw(1,303) / quality(1,242) / dossier(1,055) / scheduler(1,008)

### 前端（TypeScript/React）

| 指标 | 数值 |
|---|---|
| TS/TSX 文件 | 369 |
| 组件 | 240（24 个子目录） |
| 页面 | 51 |
| API 客户端 | 45 |
| 单测 | 111 文件 + 14 个 e2e |

### 测试

| 指标 | 数值 |
|---|---|
| 后端测试文件 | 238 |
| 后端测试行数 | 63,057 |
| 测试函数 | 3,551 |
| 测试/源码比 | **0.66 : 1** |

---

## 三、架构评价

### 分层结构（清晰）

```
api/routes (62)        ← 接入层，62 个路由模块按业务域拆分
   ↓
services (169)         ← 业务服务层，按域再分子模块
   ↓
runtime (22)           ← 运行时内核：agent_bus / checkpoint_sqlite /
                          delegation / interruption / gateway / graph
   ↓
db (27) + migrations   ← 持久层，SQLAlchemy + Alembic 43 版
   ↓
adapters (14)          ← 外部集成：mcp / a2a / hermes / community_harness
```

### 亮点

1. **接入层抽象到位** — 14 个 adapter 覆盖 MCP / A2A / Hermes / native_subagents / research_agent / community_harness，外部 Agent 统一接口。
2. **运行时内核完整** — 检查点（checkpoint_sqlite）、委派（delegation）、打断（interruption）、总线（agent_bus）、并行策略（parallel_policy）齐备。
3. **持久化演进规范** — 43 个 Alembic 迁移，说明 schema 是持续演进而非一次性堆砌。
4. **测试投入重** — 3,551 个测试函数、测试/源码比 0.66，属中上水平。
5. **工程化齐全** — CI(ci.yml) + Release(release.yml) + Dockerfile + docker-compose + Makefile + alembic.ini。
6. **技术栈现代且统一** — FastAPI + SQLAlchemy 2.0 + Pydantic v2 + React 18 + Vite + PixiJS 8 + Three.js。

### 风险 / 待改进

| 级别 | 问题 | 说明 |
|---|---|---|
| 🟡 | **超大单文件** | `dsl_canvas.py` 2,367 行、`canvas.py` 1,861 行、`avatar_gen.py` 1,768 行、`cabin_gameplay.py` 1,644 行、`agent_teams.py` 1,488 行 —— 单文件超 1,500 行，维护成本高，建议按职责再拆 |
| 🟡 | **services 层过重** | 169 文件 / 62,377 行占后端 65%，业务逻辑集中度过高；`knowledge` 单子模块 8,395 行 |
| 🟡 | **职责边界模糊** | 同一 repo 同时含「多 Agent 编排中台」+「命理排盘」+「像素小屋游戏」+「桌宠」+「3D 知识星图」—— 产品定位偏杂，对外叙事会分散 |
| 🟢 | **依赖较重** | 36 个主依赖，含 Temporal + LangGraph 两套工作流引擎，存在能力重叠 |
| 🟢 | **内网 IP 出现在测试** | 5 个测试文件含模拟内网 IP，属夹具值，但建议统一用 `192.0.2.0/24`（RFC 5737 保留段）避免误导 |

---

## 四、工程度评分

| 维度 | 评分 | 依据 |
|---|---|---|
| **代码规模** | 9.5/10 | 9.5 万行后端 + 369 个 TS 文件，个人项目中的超大体量 |
| **架构清晰度** | 8.0/10 | 分层清晰、接入层抽象好；扣分在超大单文件与混业经营 |
| **测试完备度** | 8.0/10 | 0.66 测试比 + 111 前端单测 + 14 e2e；扣分在超大文件的测试覆盖难度 |
| **工程化程度** | 9.0/10 | CI/Release/Docker/Makefile/Alembic 43 版齐全 |
| **可维护性** | 6.5/10 | 2,367 行单文件 + services 层 6.2 万行，新人上手成本高 |
| **产品完整度** | 8.5/10 | 端到端可跑（画布/调度/工作台/个人空间），有 Windows 发行包 |
| **复杂度** | 高 | 双工作流引擎 + 62 路由 + 14 适配器 + 多 Agent 机制全套 |

**综合：8.0 / 10** — 属「个人项目中罕见的工程化完整度」，架构分层规范、测试与 CI 投入到位；主要短板是**单文件过大**与**产品域过杂**，属可优化项而非缺陷。

---

## 五、复杂度 / 完成度结论

- **工程度**：**高**。不是 demo，是具备 CI、迁移、测试、容器化、发行包的完整工程。
- **复杂度**：**高**。9.5 万行后端 + 双工作流引擎 + 14 适配器 + 62 路由 + 多 Agent 编排全套机制。
- **完成度**：**约 85%**。核心链路端到端可跑并有发行包；未完成部分集中在超大模块的重构、部分拓扑的运行时切换，以及产品叙事的收敛。
