# Find Yourself

个人自我认知与 AI 画像系统。导入你自己的日记、对话、工作记录，系统据此做多维画像推演，并在本地保留完整证据链。

**当前状态：本地可跑，尚未达到可上市标准。**
上市审查结论见 [`docs/上市资格审查报告-2026-10-03.md`](docs/上市资格审查报告-2026-10-03.md)。

---

## 三步开箱

```powershell
# 1. 启动（首次会自动生成 .env、随机密钥、初始化数据库）
.\start.ps1

# 2. 写入引导示例数据（可选，但强烈建议）
.venv\Scripts\python.exe scripts\seed_demo_data.py

# 3. 打开http://127.0.0.1:8000
```

启动脚本会在浏览器打开界面，**登录页已预填口令，直接点登录即可**。

### 停止

```powershell
.\start.ps1 -Stop
```

### 常用参数

| 命令 | 作用 |
|---|---|
| `.\start.ps1 -Port 8010` | 换端口（默认 8000，被占用时必须换） |
| `.\start.ps1 -ResetData` | 清空数据库重来 |
| `.\start.ps1 -NoBrowser` | 不自动开浏览器 |
| `.\start.ps1 -LocalToken mytoken` | 自定义本地口令 |

脚本是**幂等**的：已在运行时会直接提示并打开浏览器，不会重复启动。

---

## 环境要求

- Windows + PowerShell
- Python 3.12（项目自带 `.venv`，通常无需另装）
- 无需 PostgreSQL / Redis / Docker —— 默认走 SQLite

**如果 `.venv` 不存在**：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

---

## 首次使用流程

1. **登录** —— 页面已预填本地口令，直接点「本地口令直接登录」
2. **建对象** —— 「画像」页新建对象（自己 / 某个人 / 某个项目）
3. **导入资料** —— 粘贴或上传日记、对话、工作记录。系统按确定性规则从素材归纳特征
4. **确认主体** —— 导入后需确认这段话是谁说的（防止「画像串味」）
5. **跑推演** —— 对对象触发推演，生成画像修订
6. **协作画布** —— 多 Agent 编排与可视化画布

---

## 目录结构

```
start.ps1                 一键启动（本项目入口）
scripts/
  seed_demo_data.py       引导示例数据（走真实 HTTP 接口）
  verify_launch_blockers.py  上市阻断项实证复现脚本
web/                      React + Vite 前端
  dist/                   已构建产物（由后端同端口托管）
src/find_yourself/        后端源码
  api/routes/             19 个子路由，175 条接口
  services/               业务服务
  db/                     SQLAlchemy 模型（44 张表）
  runtime/                运行时（模型网关、编排、沙箱）
migrations/               Alembic 迁移
docs/                     规格、审查报告
evidence/                 验收证据
```

---

## 常用地址

| 地址 | 说明 |
|---|---|
| `http://127.0.0.1:8000` | 应用界面 |
| `http://127.0.0.1:8000/docs` | 交互式 API 文档（Swagger） |
| `http://127.0.0.1:8000/health/live` | 健康检查 |

---

## 模型 API 说明

系统分两种模式：

**不配模型（默认）** —— 大部分功能可用，但：

- ⚠️ **核心对话返回硬编码模板**。用户换任何话题得到逐字相同的回答。实测证据见
  `scripts/verify_launch_blockers.py` 用例 A。这是当前**最重要的已知问题**。
- 画像推演走确定性规则，**不需要模型**，可正常产出结果
- `POST /api/inference/complete` 会诚实报错 503，**不会伪造模型答案**

**配置模型** —— 在 `.env` 中填入：

```
FY_MODEL_API_KEY=你的密钥
FY_MODEL_BASE_URL=https://api.example.com/v1
```

改完重启服务。系统目前只支持 **OpenAI 兼容协议**，尚不支持 Anthropic / Ollama 等。

> **注意**：密钥是全局单值，宿主配置。目前**没有让用户自带密钥（BYOK）的入口**。

---

## 已知限制

| 项| 状态 |
|---|---|
| 核心对话为硬编码模板 | 🔴 见上|
| 桌面安装包 | 仅 1.2 MB 源码分发包，**不含可执行程序**，需自行安装 Python 环境 |
| 用户注册 | 无。175 条接口中没有任何用户创建入口，仅支持本地口令 |
| 数据隔离 | 单所有者架构。记忆查询未按 owner 过滤，多用户场景不可用 |
| 数据删除 | 服务已实现，但**未暴露 API 入口** |
| 自带模型密钥（BYOK） | 不支持 |
| 自带 Agent 接入 | 不支持 |
| 插件扩展 | 零实现 |
| 游戏化体验层 | 零实现（现有DSL 画布是工具，非游戏） |
| 知识源适配器 | 仅 1 个，且路径硬编码 `D:\person-kb\kb.db` |

**完整清单见上市资格审查报告。**

---

## 复现上市阻断项

```powershell
.\start.ps1 -Port 8000
.venv\Scripts\python.exe scripts\verify_launch_blockers.py --base http://127.0.0.1:8000
```

该脚本只调用真实接口、不改代码，会实测输出：

- 三条不同输入的对话回复是否逐字相同（硬编码证据）
- `/api/memory/search` 是否按 owner 过滤
- `/api/agent-dispatch` 匿名访问是否可读

---

## 运维CLI

```powershell
.venv\Scripts\python.exe -m find_yourself.cli doctor
```

健康检查（不输出连接串/密钥）。生产环境运维另有 `backup` / `restore` / `verify-deletions` 子命令。
