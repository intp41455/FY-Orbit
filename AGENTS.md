# AGENTS.md · FY Orbit · 星轨（Find Yourself）

> 本文件约定本仓库的项目结构、常用命令与工程铁律，面向所有在本仓库工作的协作者（含自动化编码助手）。
> **一切结论以「当前代码 + 可复现命令的真实输出」为准**；设计稿、截图与宣传文案都不构成「功能已完成」的证据。

---

## 1. 项目定位

**本地优先（Local-First / Air-Gapped）的多智能体编排与统一调度平台**：

- 可视化搭建多智能体工作流（画布 × 代码同源）；
- 统一调度自有 Agent 与外部成品 Agent（统一路由 / 权限 / 审计 / 状态同步）；
- 内嵌工程工作台（代码区 + 终端 + Git + 预览 + 差异）；
- 个人空间（画像 / 命理 / 3D 知识星图 / 数码小屋 / 桌宠）。

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
  api/routes/          # 按域拆分的路由模块
  services/            # 业务服务：canvas / agent_teams / kanban / cabin /
                       #   memory / audit / hitl / observability / knowledge ...
  runtime/             # gateway / graph / sandbox / checkpoint / sse / providers
  workflows/           # Temporal 工作流与活动
  adapters/            # mcp / a2a / s3 / person_kb ...
  charts/              # 确定性命理排盘引擎（engine / interpreter / retrieval）
web/src/
  pages/               # 工作台 / 画布 / 看板 / 知识库 / 命理 / 小屋 / 画像 ...
  components/          # 按域分包：workbench / canvasui / kanban / cabin / pet ...
```

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

# Make 快捷入口
make install | test | lint | typecheck | build | build-docker | up | down | deploy-web
```

> 环境注意：`FY_SESSION_SECRET`（≥32 字符）生成后**不可更换**——Fernet 密钥由它派生，轮换将解不开既有密文。
> 默认离线：`FY_OFFLINE_MODE=True`，未经用户明确授权不得向公网外传数据。

---

## 4. 工程铁律

1. **诚实优先，零假数据**：未配置 / 未实现必须如实标注（503 / 「未接入」/「施工中」），严禁用 alert、mock 数据或硬编码状态冒充运行成功。
2. **不许假按钮**：后端无对应端点时，UI 必须 `disabled` + 说明，不得留「点了没反应」或假成功的按钮。
3. **UI 基准**：淡天蓝 / 薄荷冰蓝 / 乳白磨砂玻璃；**禁止绿色**（含 diff 新增绿等任何绿色语义）。状态不得只靠颜色传达，须同时有文字 / 图标 / 动作 / 时长。
4. **验收分层**：分别记录「视觉符合 / 接口接通 / 真实交互 / 可访问性 / 运行证据」；演示费用与测试数字不得用于宣传或验收。
5. **中文优先**：代码注释、文档与 UI 文案使用中文；提交信息沿用仓库风格。
6. **数据隔离**：记忆库 / 知识库 / 资产按 `owner_id` 强制隔离，跨 owner 物理不可见。
7. **写前快照 & 可回滚**：高危写操作前落盘快照；可逆动作须支持回滚；改参重跑走分叉，不覆盖历史。
8. **HITL 门禁**：高危写操作、敏感外部 API、环境变更须经人在回路审批；**「挂起」是 202 控制流信号，不是失败**，前端不得误报为失败。
9. **离线优先**：新增能力默认本地可跑；任何云端依赖须显式标注并可用降级链兜底。
10. **冻结件**：`web/e2e/**`、`web/src/styles.css`、`web/src/styles/tokens.css` 等为单写入者冻结文件，未经明确授权不得改动。

---

## 5. 变更流程

1. 开工前先跑一次全量基线（后端 + 前端 + tsc），确认起点是绿的，再动手。
2. 一个任务 = 一份交付说明，含：改动清单、判据逐条验证（可复现命令 + 真实输出）、**如实记录的未尽事项与剩余风险**。
3. 未跑过的验证不得写「已通过」；没验证的写「需核实」，不背书。

---

## 6. 当前限制（如实记录）

- 界面级自动保存已落地（`web/src/components/ui/SaveStatusIndicator.tsx` + `web/src/hooks/useAutosave.ts`）；自动保存点 + 手动里程碑、多端同步仍在研发。
- 总控（coordinator）系统提示词、隐性条件自动预填：研发中（当前需用户自填）。
- 自定义云盘接入与多端同步：研发中（当前仅本地）。
- 企业模式审批「裁决 → 续跑」链路：仅显示挂起，尚未接线。
- 插件市场 / 强沙箱 / 包签名 / 自动扫描：部分实现。
- 桌面出包：发行构建链已可用（`deploy/build_release_package.py`，PyInstaller onedir + Vite + 完整性自检 verify.bat + SHA256SUMS），正式 Inno Setup 安装器脚本（`deploy/installer/FY-Orbit.iss`）已落地但需安装 Inno Setup 6 才能出 setup.exe。
- 代码签名证书：尚未采购，签名脚本 `deploy/sign_windows.ps1` 在未配置证书时如实跳过；拿到证书后无需改链即可签名。
- 真实云模型 key 联调：未完成（需要真实外部凭证）。
- 在线网页版为**静态前端预览**，完整功能需在本地运行后端服务。

---

## 7. 安全与发布

- 不得提交 `.env`、凭证、`personal_history`、`credentials` 等敏感内容（见 `.gitignore`）。
- 公开发布前核对：许可证文件、依赖许可证清单、宣传文案与实际能力是否一致。
- 许可证语义：顶层 `LICENSE` 为 BSL 1.1（变更日 4 年后自动转 Apache-2.0），README 徽章、正文与末尾声明必须与它保持一致，不得再出现「全文 Apache-2.0」的表述。