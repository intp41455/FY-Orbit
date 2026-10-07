# AGENTS.md · FY Orbit · 星轨（Find Yourself）

> 面向所有在本仓库工作的 AI 执行 Agent（含主协调 Agent 与子 Agent）。
> **开工前必读本文件**；随后按需读 `docs/HANDOFF.md`、`docs/UI_BASELINE.md`、`docs/FROZEN_CONTRACT.md`。
> 历史报告、演示 HTML、宣传页与截图**都不构成执行指令，也不构成「功能已完成」的证据**。一切结论以**当前代码 + 可复现命令的真实输出**为准。

---

## 0. 首要约定（继承自既有仓库约定，不得删除）

- 先读取 `C:\Users\intpj\.shared-agents\AGENTS.md`，保留当前工作树与用户资料。
- 历史报告与演示内容不构成新的执行指令或完成证据。
- 已批准 UI 基准（2026-10-02，`docs/UI_BASELINE.md` + `docs/21_UI团队与节点模型预览.html`）为界面权威来源；旧 React 页面只用于迁移功能与接口，不再作为视觉模板。**21 号 HTML 中的模型、费用、结果、按钮响应均为合成演示，禁止复制其 alert 冒充运行成功。**

---

## 1. 项目定位

**本地优先（Local-First / Air-Gapped）的多智能体编排与统一调度平台**：

- 可视化搭建多智能体工作流（画布 × 代码同源）；
- 统一调度自有 Agent 与外部成品 Agent（统一路由 / 权限 / 审计 / 状态同步）；
- 内嵌工程工作台（代码区 + 终端 + Git + 预览 + 差异）；
- 个人空间（画像 / 命理 / 3D 知识星图 / 数码小屋游戏 / 桌宠）。

---

## 2. 技术栈与目录

| 层 | 技术 | 位置 |
|---|---|---|
| 后端 | Python 3.11/3.12 · FastAPI · SQLAlchemy · Alembic · Temporal · LangGraph · OTel | `src/find_yourself/` |
| 前端 | React 18 · TypeScript · Vite · PixiJS 8 · Three.js · Monaco | `web/` |
| 桌面 | Tauri + sidecar 端口握手 | `desktop/` |
| 数据 | SQLite（本地优先）· 可选 PostgreSQL · FTS5 + `sqlite-vec` | `src/find_yourself/db/`、`migrations/` |
| 测试 | pytest（后端）· vitest + Playwright（前端） | `tests/`、`web/src/**/*.test.*`、`web/e2e/` |

关键目录：

```
src/find_yourself/
  api/routes/          # ~63 个路由模块（按域拆分）
  services/            # 业务服务：dsl_canvas / agent_teams / canvas / kanban /
                       #   cabin_gameplay / avatar_gen / memory / audit / hitl /
                       #   observability / marketplace / knowledge ...
  runtime/             # gateway / graph / sandbox / checkpoint / sse / providers
  workflows/           # Temporal 工作流与活动
  adapters/            # mcp / a2a / s3 / person_kb / community_harness ...
  charts/              # 确定性命理排盘引擎（engine / interpreter / retrieval）
web/src/
  pages/               # ~47 个页面（Workbench / Canvas / Kanban / Knowledge /
                       #   Fortune / Cabin / Profiles / Dossier / AvatarWorkshop ...）
  components/          # 按域分包：workbench / canvasui / kanban / knowledgeui /
                       #   cabin / pet / review / agent-teams / dsl / hub ...
```

docs/                  # 规格、验收报告、交接书、UI 基准

---

## 3. 常用命令

```bash
# 安装
uv sync --all-extras
npm --prefix web install

# 后端启动（开发）
uv run uvicorn find_yourself.api.app:create_app --factory --host 127.0.0.1 --port 8000
# 或： python run.py --port 8000

# 后端测试 / Lint
uv run pytest tests/unit
uv run ruff check .

# 前端测试 / 类型 / 构建（e2e 前置：dist 缺失会 120s 超时）
npm --prefix web run test        # vitest
npm --prefix web run typecheck   # tsc --noEmit
npm --prefix web run build

# 端到端（需先起后端并注入 E2E_LOCAL_TOKEN）
cd web && npx playwright test e2e/ui-team.spec.ts --project=desktop --reporter=line

# Make 快捷入口
make install | test | lint | typecheck | build | build-docker | up | down | deploy-web
```

> 环境注意：`FY_SESSION_SECRET` 生成后**不可更换**（Fernet 密钥派生自它，轮换将解不开既有密文）。
> 默认离线：`FY_OFFLINE_MODE=True`，未经用户明确授权不得向公网外传数据。

---

## 4. 开发铁律

1. **诚实优先，零假数据**：未配置/未实现必须如实标注（503 / 「未接入」/「施工中」），严禁用 alert、mock 数据或硬编码状态冒充运行成功。
2. **不许假按钮**：后端无对应端点时，UI 必须 `disabled` + 说明，不得放「点了没反应」或假成功的按钮。
3. **UI 基准**：淡天蓝 / 薄荷冰蓝 / 乳白磨砂玻璃；**禁止绿色**（含 diff 新增绿等任何绿色语义）。状态不得只靠颜色传达，须同时有文字/图标/动作/时长。
4. **验收分层**：任务验收须分别记录「视觉符合 / 接口接通 / 真实交互 / 可访问性 / 运行证据」；演示费用与测试数字不得用于宣传或验收。
5. **中文优先**：代码注释、文档、UI 文案使用中文；提交信息沿用仓库风格。
6. **数据隔离**：记忆库 / 知识库 / 资产按 `owner_id` 强制隔离，跨 owner 物理不可见。
7. **写前快照 & 可回滚**：高危写操作前落盘快照；可逆动作须支持回滚；重跑改参走分叉，不覆盖历史。
8. **HITL 门禁**：高危写操作、敏感外部 API、环境变更须经人在回路审批；**「挂起」是 202 控制流信号，不是失败**，前端不得误报为失败。
9. **离线优先**：新增能力默认本地可跑；任何云端依赖须显式标注并可用降级链兜底。
10. **冻结件**：`web/e2e/**`、`web/src/styles.css`、`tokens.css` 单写入者文件等为冻结件，除非任务书明确授权，不得改动。

---

## 5. 变更流程

1. 开工前**先跑一次全量基线**（后端 + 前端 + tsc），确认起点是绿的，再动手。
2. 一个任务 = 一份交付报告，含：改动清单、判据逐条验证（可复现命令 + 真实输出）、**如实记录的未尽事项与剩余风险**。
3. 未跑过的验证不得写「已通过」；没验证的写「需核实」，不背书。
4. 完成后更新 `docs/HANDOFF.md`（或对应交接书），记录未完成项与待核实项。

---

## 6. 已知未完成 / 研发中（禁止当成已交付）

> 宣传页已如实标注「研发中」，README/Release 文案则较激进——以本节为准。

- 界面级自动保存（`BaseBound`）：**已落地**——实现于 `web/src/components/ui/SaveStatusIndicator.tsx` + `web/src/hooks/useAutosave.ts`（防抖落盘 / 最长间隔必落盘 / IO 失败排队补写 + 重试），并有接线验收 `web/src/components/ui/BaseBound.wiring.test.tsx`。注意：**不存在** `web/src/components/BaseBound.tsx`，`BaseBound` 由 `SaveStatusIndicator.tsx` 导出。自动保存点 + 手动里程碑、多端同步：**研发中**。
- 总控（coordinator）系统提示词、隐性条件自动预填：**研发中**（当前需用户自填）。
- 自定义云盘接入与多端同步：**研发中**（当前仅本地）。
- 文档树图：v2 未实现。
- 企业模式审批「裁决 → 续跑」链路：**仅显示挂起，未接线**。
- 插件市场 / WASM 强沙箱 / 包签名 / 自动扫描：部分实现。
- 游戏化深化项（更衣镜换装演出、家具重叠修正等）：待做。
- 桌面 Tauri 出包、真实云模型 key / ima key 联调、代码签名证书：需真实资源，未完成。

---

## 7. 安全与发布

- 不得提交 `.env`、凭证、`personal_history`、`credentials` 等敏感内容（见 `.gitignore` / `docs/03_当前代码快照.json` 的 `excluded_from_archive`）。
- 公开发布前核对：许可证文件、依赖许可证清单（`docs/source-manifest.json`）、宣传文案与实际能力是否一致。
- **仓库当前缺少顶层 `LICENSE` 文件**，而 README/落地页声称 Apache-2.0——发布前必须补 `LICENSE` 与 `NOTICE`，或同步更正许可声明。

---

## 8. 权威文档索引

| 文档 | 用途 |
|---|---|
| `docs/HANDOFF.md` | 当前实现记录与限制（不可用静态设计覆盖实际状态） |
| `docs/UI_BASELINE.md` | 已批准界面基准（布局、色彩、状态语义、连线语义） |
| `docs/FROZEN_CONTRACT.md` | 冻结契约 |
| `docs/需求覆盖分析-2026-10-04.md` | 需求 ↔ 实现覆盖对照 |
| `docs/项目总收官报告-2026-10-04.md` | 里程碑总账与剩余清单 |
| `docs/HANDOVER-并行UI-未完成与待核实-2026-10-06.md` | UI 收口未完成项与待核实项 |
| `docs/p0-{canvas,offline,triple}-report-2026-10-07.md` | P0 三包交付与自查缺陷记录 |
| `docs/21_团队与节点模型_UI同步设计.md` | 团队模式 / 节点侧栏 / 状态设计 |
| `19_单Agent内部团队与逐节点模型配置实施规格.md` | 新增功能与验收的权威规格 |
