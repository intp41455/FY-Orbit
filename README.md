# FY Orbit · 星轨 (Find Yourself)

<p align="center">
  <strong>本地优先 · 隐私自主 · 多智能体协同编排与生活模拟一体化平台</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-18.3-61dafb?logo=react&logoColor=white" alt="React" />
  <img src="https://img.shields.io/badge/PixiJS-8.0-e72264?logo=pixijs&logoColor=white" alt="PixiJS" />
  <img src="https://img.shields.io/badge/Three.js-WebGL%203D-black?logo=three.js&logoColor=white" alt="Three.js" />
  <img src="https://img.shields.io/badge/Storage-SQLite%20%7C%20Postgres-003B57?logo=sqlite&logoColor=white" alt="Storage" />
  <img src="https://img.shields.io/badge/Tests-1268%20Passed-success" alt="Tests" />
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange" alt="License" />
</p>

---

## 🌟 项目简介

**FY Orbit · 星轨** 是一套面向个人与企业团队的高性能、本地优先（Local-First）智能体（Agent）编排框架与人机协同平台。

与传统重度依赖云端、强制联网并存在数据外泄风险的 AI 工作流工具不同，**FY Orbit** 坚持“**数据主权完全归属于用户**”的工程准则：核心调度引擎、本地嵌入式向量检索、任务时间旅行快照、命理天文算法以及治愈系生活模拟系统，全部可在单机断网环境下零依赖极速运行。

无论您是零代码基础的创作者、追求代码与可视化双向同源的全栈工程师，还是重视组织权限与合规审计的企业团队，FY Orbit 都能通过其独创的**三重工作模式**为您提供流畅、敏捷且可靠的生产力体验。

---

## ✨ 核心特性

### 1. 16 类工业级画布节点 (Flow Workshop)
对标顶级开源工作流工具，提供兼具易用性与扩展性的 16 类标准化节点库：
* **核心推理**：`llm` 大模型推理、`rag_query` 双路混合召回、`condition` 分支判定、`loop` 迭代批处理。
* **开发扩展**：`code` 内存受限脚本沙箱、`tool` 外部工具调用、`api_call` RESTful 请求、`subgraph` 模块化子图嵌套。
* **高并发协作**：`parallel` 并行派发、`join` 结果聚合器、`delay` 时序控制、`event_emit` / `event_listen` 异步事件总线。
* **状态与把关**：`variable_assign` 运行时变量管理、`data_transform` 结构转换、`human_review` 人在回路中断审批。

### 2. 三重模式同源切换 (Triple Mode)
* **小白向导模式 (Beginner)**：零代码问答向导式配置，张口即用。
* **技术工程师模式 (Technical)**：全功能可视化画布与 Monaco 代码编辑器双向同源，实时双向热同步。
* **企业治理模式 (Enterprise)**：聚焦组织架构、四元组细粒度鉴权策略、预算额度熔断与合规审计链。

### 3. 高级敏捷看板与纯原生 SVG 规划图表
* **四列任务状态机**：流转掌控待办、进行中、阻塞与完成状态，支持卡片清单与前置依赖。
* **加权进度 Donut (`ProgressDonut`)**：根据子任务权重精准计算百分比，中心留白优雅呈现。
* **甘特式时间线规划视图 (`GanttChart`)**：纯原生 SVG 渲染多任务排期重叠、排期冲突与依赖连线。
* **燃尽图与燃起图 (`BurndownChart`)**：真实采样实线与理想斜坡虚线对比，诚实呈现迭代健康度。
* **置顶阻塞红带**：强刺激强红标记超时阻塞，自动触发升级警报。

### 4. 交互革命：「点哪评哪」与多模态批注 (In-Place Review)
彻底抛弃截图+打字的低效协作方式，实现面向界面资产的闭环交互：
* **DOM 点选**：悬停高亮组件，原地呼出反馈框，自动绑定组件源码标识。
* **区域框选**：鼠标划定矩形区域，提出宏观排版与视觉改动需求。
* **画笔涂鸦批注**：自由绘制引导箭头与圈选手写重点。
* **热刷新闭环**：修改完成后即时触发局部页面热更新，自动生成红线条（红增灰删）版本对比。

### 5. 纯端侧双轨 RAG 与 3D 银河知识星图
* **零外部模型依赖**：内置哈希特征向量（`hash_embedding`）与 `sqlite-vec` 向量数据库扩展。
* **双路混合检索**：SQLite FTS5 (BM25 全文检索) + 向量语义搜索，通过 RRF (Reciprocal Rank Fusion) 算法精准融合。
* **3D 立体交互式星图**：基于 Three.js WebGL，在三维空间中渲染星系旋臂与引力连线，支持 Orbit 全景漫游与光晕聚焦抽屉。

### 6. 数码小屋像素生活模拟与治愈陪伴 (Cabin Life)
* **CC0 高精微像素重构**：引入 Tiny Swords 与 Forest Atlas 高精手绘瓦片与角色动画。
* **9 大主题与 90 位具名 NPC**：老林子、溪水边、观星台、古风桃源等 9 大世界，NPC 拥有独立作息与好感度剧情。
* **室内建造与天气系统**：20 余种家具网格吸附、晨昏昼夜光影变幻与动态萌宠陪伴。

### 7. 确定性排盘引擎与个人画像
* **纯算法本地排盘**：四柱生辰八字（干支、藏干、十神、纳音、旺相休囚死）确定性推算。
* **现代占星与每日运势**：紫微斗数、星盘相位、每日签卡与塔罗抽牌正逆位解析，内置合规免责声明。

### 8. 工业级高可用性与基座质保 (T6 Resilience)
* **全局自动保存 (`BaseBound`)**：20 个主要业务页面全量挂载 2 秒防抖本地持久化，断电/刷新原样恢复。
* **事务外发箱 (`Outbox`)**：先抢占再发送机制，防止外部调用重复触发。
* **WorkStash 任务状态暂存**：进程崩溃或重启后自动寻找断点继续执行。
* **多 Provider 智能降级链**：OpenAI / Claude / Ollama / DeepSeek 多源聚合，遇 429 自动指数退避重试。

---

## 🏗️ 架构概览

```mermaid
graph TD
  Client[用户交互端: 浏览器 Web UI / 桌面端 Webview] --> Gateway[API 网关 · 421 个 RESTful 端点]
  
  subgraph 前端渲染层 (React 18 + Vite + PixiJS + Three.js)
    TripleMode[ModeSwitcher 三重工作模式]
    FlowEditor[FlowEditor 16 类节点画布]
    Star3D[Three.js 3D 银河知识星图]
    CabinStage[PixiJS 瓦片地图生活模拟]
    BaseBound[BaseBound 全局自动保存]
  end

  subgraph 后端核心服务层 (FastAPI + LangGraph)
    CapBroker[CapabilityBroker 统一能力网关]
    ClawGates[三层把关质检: 自审 / 交叉 / 独立质检]
    ModelRouter[多 Provider 模型网关 / 熔断降级链]
    HybridRAG[双路混合检索: BM25 + sqlite-vec]
    AstroEngine[确定性天文排盘与星座算法]
    Resilience[T6 抗打断: Outbox + WorkStash]
  end

  subgraph 存储与数据底座 (本地优先)
    SQLiteDB[(SQLite 嵌入式单文件: find-yourself.db)]
    VecDB[(sqlite-vec 本地向量扩展)]
    FileSystem[(本地资产库 .runtime/assets + S3 兼容对象存储)]
  end

  Gateway --> CapBroker
  Gateway --> ModelRouter
  Gateway --> HybridRAG
  Gateway --> AstroEngine
  Gateway --> Resilience

  CapBroker --> SQLiteDB
  HybridRAG --> VecDB
  Resilience --> SQLiteDB
```

---

## 🚀 快速启动

### 方式一：Windows 桌面一键免安装运行（推荐）

1. 克隆或下载本仓库代码；
2. 双击根目录下的快速启动脚本：
   ```cmd
   start.bat
   ```
3. 系统将自动检测 Python 运行时、初始化本地 SQLite 数据库并启动服务，在浏览器自动打开：  
   👉 **`http://127.0.0.1:8000`**

### 方式二：命令行手动启动

```bash
# 1. 准备 Python 3.11+ 虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. 安装依赖并启动
pip install -e .
python run.py --port 8000
```

### 方式三：使用预编译桌面安装包
直接下载随附的 Windows 安装压缩包 [`FindYourself-Windows-Setup-Preview.zip`](assets/FindYourself-Windows-Setup-Preview.zip)，解压后双击 `Install.bat` 即可开箱即用。

---

## 📖 开发者与常用地址

启动服务后，可通过浏览器直接访问以下核心端点：

| 地址 | 功能说明 |
| :--- | :--- |
| `http://127.0.0.1:8000/` | Web 主工作台与系统首页 |
| `http://127.0.0.1:8000/docs` | OpenAPI / Swagger 交互式接口文档 (421 个端点) |
| `http://127.0.0.1:8000/redoc` | ReDoc 规格文档 |
| `http://127.0.0.1:8000/api/charts/health` | 确定性排盘引擎健康状态检查 |

---

## 🧪 自动化测试套件

本项目具备覆盖率严密、断言清晰的工业级测试矩阵：

```bash
# 运行后端全量测试套件 (109 个测试文件，1268 个用例)
python -m pytest tests/ -q

# 运行前端组件与单元测试
cd web
npm run test
```

---

## 🔒 隐私、安全与合规声明

1. **零遥测与断网保障**：系统默认 `FY_OFFLINE_MODE=True`，未经用户显式授权，任何本地私有数据、知识文档与排盘记录绝不回传任何云端服务器。
2. **审计留痕**：关键配置变更与权限越权拦截均写入不可逆 SHA-256 审计哈希链，满足企业级安全合规。

---

## 📄 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 协议开源。
