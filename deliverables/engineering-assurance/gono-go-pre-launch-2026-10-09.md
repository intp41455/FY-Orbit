# FY-Orbit 上市 Go/No-Go 核验报告

**日期**：2026-10-09
**工作流**：部署前检查（Go/No-Go）
**参与成员**：Cody / Archi / Rex / Tessa（多轮 worker 核验 + 主理人实测复核）
**目标树**：`FY-Orbit` 主工作树，HEAD `1f12f65`（含本轮提交 `1f12f65` 假绿守卫）

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🔴 **暂不 No-Go，但不可直接上市** —— 代码质量与 CI 链路已达标（4085 全绿），**但「多 Agent 协作制度」的用户核心需求只落地约 4/7 机制**，三道防死循环闸门缺两道，且**基座门禁未接入 CI**、**编排前端零入口**、**graph.py 三个活缺陷未修**。
- 严重度分布：🔴 严重 5 项 / 🟠 高 4 项 / 🟡 中 4 项 / 🟢 低 2 项
- 阻塞：**不构成对外虚假宣传的阻断**（已核实 P0 七项全修、技术栈三项已消除、许可证已统一 BSL 1.1），但构成**产品定位不成立**（用户要的「一个总控统管多 Agent 双向实时传话」闭环缺记忆分层与防循环闸门）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟡 **有条件通过**（补齐 5 项 P0 后可 Go） |
| 阻塞项数量 | 🔴 5 项（2 项产品定位、2 项工程质量、1 项安全残留） |
| 关键行动项 | 9 条（见行动清单） |
| 建议下一步 | 先补「协作制度落地度」P0 两项 + 基座门禁接 CI，再走 release-windows 演练通道验证发行链 |

---

## ✅ 本次实测已达标项（全部实测，非旧报告）

### 1. CI 链路全绿（与 CI 相同环境变量实跑）

| 检查 | 结果 | 证据 |
|------|------|------|
| 后端全量测试 | ✅ **4072 passed / 95 skipped / 0 failed**（12m22s） | CI 同 env（FY_ENVIRONMENT=test, FY_OFFLINE_MODE=true, 占位密钥） |
| ruff 检查 | ✅ All checks passed | `ruff check src tests` |
| alembic 单头 | ✅ `0047_hitl_vote (head)` 唯一 | `alembic heads` |
| HEAD Clean-Tree Gate | ✅ 6 passed | `test_migration_chain_gate.py` |
| 路由导入冒烟 | ✅ IMPORT_OK | `python -c "import find_yourself.api.routes"` |
| 前端 typecheck | ✅ 零错误 | `npm run typecheck` |
| 前端 vitest | ✅ **1275 passed / 110 files** | `npm run test` |
| 前端生产构建 | ✅ 成功（15M dist，PWA precache 21 项） | `npm run build` |

> ⚠️ 注意：**CI 中无覆盖率门槛**（grep `.github/workflows/*.yml` + `pyproject.toml` 的 `cov/coverage` 零命中）。fy-interconnect 旧树曾有 82% 门槛，当前主树未接。非阻断但建议补。

### 2. 发布链路静态核验

| 文件 | 判定 | 说明 |
|------|------|------|
| `.github/workflows/release-windows.yml` | ✅ 可用 | tag 触发 + workflow_dispatch 演练通道；PyInstaller sidecar → build_release_package.py → Inno Setup 可选 → 上传 `if: always()` |
| `dd910b9` 解门禁 | ✅ **真修复非软化** | 安装器步骤 `continue-on-error: true`（它是可选产物）+ 上传 `if: always()`（防制品随 runner 丢失，实证 v1.1.2 事故）+ 演练通道。**不是**把门禁改成永远通过 |
| `Dockerfile` | ✅ 合法 | 两阶段构建（末尾 `EXPOSE 8000` + `/health` 健康检查是**真实路由**）；`COPY web/dist/` 前置 web build 已具备（release.yml 先 build 再 docker） |
| `docker-compose.yml` | ✅ 本地形态 | 已如实标注「本地部署形态非生产」；`FY_ENVIRONMENT=local` + SQLite + 本地 token，**与 settings 校验不再冲突**（历史冲突已修） |
| compose env 一致性 | ⚠️ 小瑕疵 | `FY_LOCAL_ONLY` 不在 Settings 模型（代码 `os.environ.get` 直读），`extra="ignore"` 静默容忍 → **不会阻断启动**，但字段应清理 |

### 3. 合规与发布产物

| 项 | 判定 | 说明 |
|----|------|------|
| 许可证 | ✅ BSL 1.1 统一 | LICENSE/NOTICE/README（badge + 声明）三者一致，不再有 BSL/Apache 冲突 |
| 签名声明 | ✅ 诚实 | `deploy/build_release_package.py:208` 明确「未做 Authenticode 代码签名」 |
| 发行包产物 | ⚠️ 无预构建 | 本地 `dist/` 无 zip/exe（产物由 CI release-windows 生成）——**流程合法，但用户现在拿不到可安装桌面版，需跑一次演练通道** |
| Rust 工具链 | ✅ **可推翻旧报告** | `cargo 1.99.0` 本机可用（旧报告称「无 Rust 工具链」已过时） |
| ISCC | ⚠️ 本机无 | 安装器由 CI `choco install innosetup`（continue-on-error，可选产物） |

### 4. 上一轮已核实的上市阻断项（code-reviewer-4 完整回传 + 我复核）

| 项 | 判定 |
|----|------|
| P0-1 对话硬编码 gateway 零命中 | ✅ 已修（`companion.py:142-161` 调 ModelGateway） |
| P0-2 无 users 表 / 无哈希 | ✅ 已修（迁移 0011 + argon2id） |
| P0-4/P0-5/P0-6/P0-7 memory owner 过滤等 | ✅ 已修 |
| P1-1 36 处无 CSRF | ⚠️ 残留（预鉴权引导端点 + POST-as-read；`templates.py:201` 疑似有副作用无 CSRF） |
| 栈-langchain / 伪造 citations / 自建类冒充 | ✅ 已消除（真实用 langgraph / 零伪造 / local_agents 诚实声明） |

---

## 🔴 阻塞项（上市前必须补齐）

### P0-A1【产品定位】共享记忆三层池未落地

`db/models.py:436` `Memory` 表只有 `owner_id`，**无 team/agent 可见性维度**。用户设计的 global/team/private 三层池不存在；现有 short/medium/long 是**保活期**维度（`models.py:448-456`），与可见性分层是两回事。→ 要从数据模型开始加 scope 维 + 记忆路由。

### P0-A2【产品定位】三道防死循环闸门缺两道

- ❌ **血缘链 lineage：全仓零命中**（grep `lineage` src/ 无结果）——「同一 Agent 不超 N 次」完全未实现
- ❌ **子任务数上限（10）零命中**
- ✅ 深度上限有：`delegation.py:63` max_depth=3、`contracts.py:38` le=4、`local_agents.py:33`=2、`dsl_canvas.py:115` SUBFLOW_MAX_DEPTH=3
- ⚠️ 依赖图循环检测仅 `dsl_canvas.py` 拓扑序，协作调度侧无

### P0-B1【工程质量】基座门禁（A-基座质保-11）未接入 CI

实跑 `base_contract.audit_tree`：`ok=False / blocking=True / scanned=189 / violations=7 / advisories=112`（7 页面未接线：CanvasPage / DslCanvasPage / FortunePage / GameStandalonePage / HubPage / PrivateSpacePage / TimelinePage）。
**但 grep `.github/workflows/` + `scripts/` 只有 `scripts/p0/grant_base_exemptions.py`，无任何 workflow 调 `audit_tree`** —— 这个能 `exit_code=1` 的真门禁只在人手动跑时存在，CI 不会拦。→ 需接入 CI 成为硬门禁。

### P0-B2【工程质量】graph.py 三个活缺陷

`runtime/graph.py` 被 `kernel.py:33` 真实驱动，但：
- `:388/:417/:446/:472` 四个节点**硬编码合成 spend**（0.001/0.002/0.005/0.003），全文件无任何 `ModelGateway`/`.complete()` 调用 → **费用记账是假的**
- `:501-507` max_steps 两分支返回完全相同 → 死代码
- 节点绕过 ModelGateway（违反「统一网关」架构约束）

### P0-C1【安全残留】P1-1 未挂 CSRF 的写端点 36 处

`api/routes/` 写端点 276 个，已挂 CSRF 240，**未挂 36**（预鉴权引导端点 + POST-as-read）——code-reviewer-4 AST 清点结果。其中 `templates.py:201 expand-to-code` 疑似有副作用无 CSRF。应复核这 36 处是否真无副作用。

---

## 🟠 高优先级（建议上市前处理）

| # | 项 | 证据 |
|---|----|------|
| H1 | 编排三端点前端零入口 | `grep -rn "orchestrate" web/src/` 零命中；`POST /api/hub/orchestrate` 等只能手 curl。**用户宣称的「总控统管」在 UI 上不可用** |
| H2 | 检查点字段与设计不符 | `runtime/checkpoint_sqlite.py:37-64` 是 LangGraph 原生（thread_id/checkpoint_ns/parent_checkpoint_id/...），用户设计的 11 字段（checkpoint_id/task_id/agent_id/step_index/status/intermediate_result/next_action/lineage/resume_count/idempotency_key/version）需映射层 |
| H3 | 幂等键算法不符 | `tasks.py:72` 有 `(owner_id, idempotency_key)` 查重，但**无 `hash(task_id+step_id+operation_type)`**（grep hashlib 全仓只在 hash 字段名） |
| H4 | 无恢复次数上限 | `resume_count` 零命中；`recovery.py` resume 无上限 |

---

## 🟡 中优先级

| # | 项 | 说明 |
|---|----|------|
| M1 | compose 冗余环境变量 | `FY_LOCAL_ONLY` 不在 Settings 模型（`os.environ.get` 直读，extra=ignore 容忍） |
| M2 | 缺失覆盖率 CI 门禁 | 当前树 pytest 无 `--cov` 参数、pyproject 无 `fail_under` |
| M3 | compose `version` 字段过时 | docker compose 警告 obsolete |
| M4 | 断点：Docker 无法本机实跑 | Docker Desktop daemon 未启动（环境问题非配置问题），无法在本机验证容器启动 |

---

## ✅ 行动清单（按优先级排序）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | 补 Memory scope 三值（global/team/private）+ 路由查询 | Archi + Cody | P0 | 1-2 天 |
| 2 | 实现血缘链 lineage 上限 + 子任务数上限，接入 orchestrator | Archi + Tessa | P0 | 1 天 |
| 3 | 基座门禁 `audit_tree` 接入 CI（release/PR 检查步） | Rex | P0 | 0.5 天 |
| 4 | graph.py 移除合成 spend，改走 ModelGateway 真实记账 | Cody | P0 | 0.5 天 |
| 5 | 复核 36 处未挂 CSRF 端点，确认/修复副作用 | Cody | P0 | 1 天 |
| 6 | 前端接 orchestrate 三端点（HubPage 编排入口） | Cody + Docu | 高 | 1-2 天 |
| 7 | 检查点字段映射层 + resume_count 上限 | Archi | 高 | 1 天 |
| 8 | 编排幂等键改 `hash(task+step+op)` 与操作同事务 | Cody | 高 | 0.5 天 |
| 9 | 跑 release-windows workflow_dispatch 演练通道，产出真实 ZIP | Rex | 高 | 触发后约 30 分钟 |

---

## ⚠️ 待完善 / 已知局限

- **容器启动未实测**：本机 Docker daemon 未运行，`docker compose up -d --build` 连不上 npipe。静态核验通过（Dockerfile 合法、compose 本地形态、healthcheck 路由真实），但「容器真能启动」这一条**未实证**——建议在本机启动 Docker Desktop 后补跑，或在 CI 加一步 `docker compose up + curl /health/live`。
- **旧报告大量过时**：本轮以当前树实测为主，发现 cargo 可用（推翻「无 Rust 工具链」）、覆盖率门禁缺失（推翻「82% 门槛存在」）等差异，历史报告的结论**不可直接采信**。
- 本机无 ISCC，安装器（可选产物）能否成功未实证，但其失败不阻断 ZIP 上传（dd910b9 已修）。

---

## 📚 数据来源 & 成员产出索引

- Cody（code-reviewer-4）：上市阻断项核验回传（P0 七项全修、P1-1 残留 36、graph.py 三缺陷）
- Archi（architect-3）：协作制度落地度核验（多轮 429 中断，主理人亲自补核）
- Rex（sre-engineer-2/3/4）：发布链路核验（多轮 429 中断，主理人亲自补齐实测）
- Tessa（testing-expert-3）：53 项 launch-gate 核验（429 中断）
- 主理人实测：全量回归 4085 绿、ruff/alembic/前端全绿、compose 一致性、LICENSE/签名、基座门禁 7 violations、graph.py 复核、lineage 零命中

---

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。