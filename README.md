# FY Orbit · 星轨 (Find Yourself)

<p align="center">
  <strong>全能型本地优先多智能体协同中枢 · 自适应工作流工坊 · 隐私主权数字空间</strong><br>
  <em>Universal Multi-Agent Orchestration & Adaptive Workflow Studio | 100% Local-First & Air-Gapped</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/React-18.3-61dafb?logo=react&logoColor=white" alt="React" />
  <img src="https://img.shields.io/badge/PixiJS-8.0-e72264?logo=pixijs&logoColor=white" alt="PixiJS" />
  <img src="https://img.shields.io/badge/Three.js-WebGL%203D-black?logo=three.js&logoColor=white" alt="Three.js" />
  <img src="https://img.shields.io/badge/Architecture-Local--First%20%7C%20Air--Gapped-green" alt="Local First" />
  <img src="https://img.shields.io/badge/Storage-SQLite%20%7C%20Postgres-003B57?logo=sqlite&logoColor=white" alt="Storage" />
  <img src="https://img.shields.io/badge/Tests-1268%20Passed-success" alt="Tests" />
  <img src="https://img.shields.io/badge/License-Apache%202.0-orange" alt="License" />
</p>

---

## 🌟 项目定位与核心愿景

**FY Orbit · 星轨** 是一套面向个人探索者与企业级团队的**全能型智能体统一调度中枢与自适应工作流编排工坊**。

传统的 AI 工作流工具往往陷入两个极端：要么是深度绑定公网云端、必须上传隐私数据的 SaaS 平台；要么是仅面向专业程序员的晦涩代码库，缺乏直观、可靠的交互闭环与任务容灾能力。

**FY Orbit 打破了这种壁垒：**
1. **统一调度与全能多智能体协同**：提供强大的多智能体自组织分工、层级派发、事件驱动总线以及独立第三方质量检验体系，轻松应对极度复杂的长链路任务。
2. **零门槛与全维度自由度（小白与资深工程师通吃）**：
   * **零基础小白 / 业务专家**：无需编写一行代码，通过问导式向导、一句话自然语言直译以及直观的视觉拖拽画布，秒级组装出专属智能体与生产力工作流。
   * **全栈工程师 / 极客架构师**：拥有 Monaco 深度代码编辑器、AST 语法树双向同源热同步、LSP 语言服务、原生终端沙箱以及无缝集成的 Git 提交图谱与 DSL 脚本编排，掌控每一微秒的执行细节。
3. **100% 本地优先与离线断网自洽（Local-First & Air-Gapped）**：
   * 零配置首发即跑，无需连外网。在纯内网、局域网乃至物理隔离的保密环境中皆能完整构建、调试与运转。
   * 专有数据资产、知识文档与 API 密钥完全驻留本地磁盘，无任何强制云端依赖与数据泄露隐患。
4. **侧面融入人文关怀（创作者专属空间）**：
   * 与市面上冰冷生硬的传统生产力工具不同，星轨创新融入了属于创作者的「个人数码空间与闲暇小屋」——提供互动桌宠相伴、闲暇轻互动与休闲小项目。在高强度自动化调度之余，为创作者打造一处治愈温情的灵感栖息地。

---

## 🥊 核心竞争力横向深度对比

与其他主流工作流工具与智能体框架相比，**FY Orbit · 星轨** 展现出代际级的综合优势：

| 评测维度 | **FY Orbit · 星轨** | **Dify** | **Coze (扣子)** | **Langflow** | **AutoGen / CrewAI** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **部署与运行架构** | **100% 本地离线优先**（开箱单机秒启，数据绝对自治，断网可用） | 偏重容器集群（Docker 庞大）或云端 SaaS | 纯云端闭源托管，强制联网且依赖第三方 | 本地服务为主，但深度依赖外部云端模型与网络 | 纯代码库与 CLI，无开箱即用图形界面与持久化底座 |
| **用户跨度与受众** | **全角色覆盖**（小白向导/自然语言直译 + 极客 Monaco IDE/DSL 脚本） | 偏向提示词工程人员与中度技术用户 | 偏向轻量无代码运营与小白用户 | 偏向 Python/AI 开发者 | 仅面向资深 Python 程序员 |
| **协同交互模式** | **独创三重同源模式**（向导模式 / 视觉画布 / 极客工作台毫秒同源） | 链式工作流与单向 Chat 界面 | 固定卡片与单向对话流 | 节点连线单向画布 | 终端命令行交互，无可视化画布与编辑器 |
| **抗打断与断点续作** | **T6 状态台账全量流式落盘**（断网/崩溃原位秒级续作，时间机器分叉） | 依赖云端异步队列，网络断连易失步 | 任务超时直接报错中断 | 节点崩溃常需重头全量重跑 | 脚本进程挂掉即任务丢失 |
| **交互审查闭环** | **独创「点哪评哪」**（DOM 点选/区域框选/涂鸦批注/红线版本对比） | 纯文字表单或聊天窗口反馈 | 纯文字 Chat | 简单 Output 查看 | 控制台 Log 打印 |
| **本地多模型与网关** | **Ollama 本地私有大模型 + 商业 API + 指数退避智能降级链** | 支持多 Provider，需手动配置 | 平台强绑定模型体系 | 支持部分本地与远程调用 | 需开发者自行编写调度与容灾逻辑 |
| **知识库与空间检索** | **端侧双轨 RAG**（BM25 + sqlite-vec）+ **3D 银河知识星图** | 外部向量库（Milvus/Qdrant/Weaviate） | 平台云端托管索引 | Chroma / FAISS 本地向量库 | 依赖外部 LangChain 胶水层 |
| **人机关怀与专属空间** | **内置创作者数码空间与闲暇小屋**（互动桌宠/闲暇轻应用/放松休闲） | ❌ 纯工业冰冷后台 | ❌ 纯机器人后台 | ❌ 纯节点连线环境 | ❌ 纯代码库 |

---

## ⚡ 核心功能全景

### 1. 16 类工业级画布节点与全自适应工作流 (Flow Workshop)
从微观工具链到宏观多智能体编排，支持任意复杂图拓扑与循环逻辑：
* **核心推理**：`llm` 大模型推理、`rag_query` 双路混合召回、`condition` 分支判定、`loop` 迭代批处理。
* **开发扩展**：`code` 内存受限脚本沙箱、`tool` 外部工具调用、`api_call` RESTful 请求、`subgraph` 模块化子图嵌套。
* **高并发协作**：`parallel` 并行派发、`join` 结果聚合器、`delay` 时序控制、`event_emit` / `event_listen` 异步事件总线。
* **状态与把关**：`variable_assign` 运行时变量管理、`data_transform` 结构转换、`human_review` 人在回路（HITL）中断审批。

### 2. 突破性的三重工作模式同源切换 (Triple Mode)
同一套工作流逻辑在三种形态间无损秒切，满足不同角色需求：
* **🌱 小白向导模式 (Beginner)**：零技术门槛，采用向导式自然语言提问，小白输入“我想实现……”即刻自动拆解并组装出完整工作流。
* **⚡ 视觉画布模式 (Visual Canvas)**：基于高自由度平移缩放画布，多色端口类型防错吸附，直观拖拽编排。
* **🛠️ 极客技术模式 (Technical IDE)**：Monaco 顶级代码编辑器，支持 DSL、AST 语法树实时双向热同步与终端调试。

### 3. T6 工业级抗打断与断点原位续作引擎 (Resilience & Outbox)
解决业界 AI 智能体“跑一半断网或被平台限流导致前功尽弃”的核心痛点：
* **暂存必须落库**：执行状态、会话快照与中间产物全量流式落盘至 SQLite，杜绝仅驻留内存。
* **断网/重启秒级续作**：系统重启或网络恢复后，自动扫描未决断点并无缝原位接力开工。
* **时间机器存档分叉 (`ArchiveForks`)**：支持对运行状态打快照并随时分叉测试新分支，探索多元决策路径。

### 4. 革命性交互审查：「点哪评哪」与多模态涂鸦批注 (In-Place Review)
打破“截图再打字”的低效沟通阻碍：
* **DOM 原地锁定**：悬停点选界面元素，原地气泡即刻呼出评审窗口，自动挂载组件源码 ID。
* **区域矩形框选与画笔涂鸦**：自由圈选页面排版、绘制箭头指向细节。
* **即时闭环对比**：修改后触发局部热重载，自动生成红线条（红增灰删）版本对比，所见即所得。

### 5. 纯端侧双轨 RAG 知识引擎与 3D 银河知识星图
* **零外部服务依赖**：内置轻量高效的哈希特征向量与 `sqlite-vec` 向量数据库扩展。
* **双路精准融合召回**：SQLite FTS5 (BM25 词法全文检索) + 向量语义搜索，通过 RRF 算法智能重排。
* **Three.js 3D 银河星图**：将海量知识沉淀为三维星系旋臂与引力连线，支持全景空间漫游与光晕聚焦抽屉。

### 6. 高级敏捷规划看板与纯原生 SVG 甘特图
* **四状态任务流转**：待办、进行中、阻塞与完成状态机，支持前置依赖与抢占认领。
* **原生 SVG 甘特规划图 (`GanttChart`)**：直观呈现任务排期重叠、时序依赖与里程碑节点。
* **动态燃尽与燃起图 (`BurndownChart`)**：真实执行轨迹与理想工期斜率对比，量化项目健康度。

### 7. 惬意专属空间：创作者数码小屋与轻量闲暇 (Personal Cabin & Pets)
让生产力工具不再冰冷单调：
* **个人专属数码空间**：提供温馨的像素手绘场景，支持 20 余种家具自由网格布置与晨昏昼夜光影变幻。
* **动态桌宠伴侣**：具备生动状态反馈与伴读交互，时刻陪伴左右。
* **轻量休闲娱乐**：内嵌数款减压轻互动与闲暇趣味小项目，工作间歇轻松解压，随时重焕灵感。

---

## 🏗️ 架构全景

```mermaid
graph TD
  Client[用户交互端: 浏览器 Web UI / 桌面端 Webview] --> Gateway[API 网关 · 421 个 RESTful 端点]
  
  subgraph 前端渲染与交互层 (React 18 + Vite + PixiJS + Three.js)
    TripleMode[ModeSwitcher 三重同源模式: 小白向导 / 视觉画布 / 极客 IDE]
    FlowEditor[FlowEditor 16 类工业级节点画布]
    Star3D[Three.js 3D 银河立体知识星图]
    ReviewOverlay[点哪评哪: DOM 原地点选 / 框选 / 涂鸦批注]
    PersonalCabin[创作者专属数码空间: 动态桌宠 / 闲暇轻互动]
    BaseBound[BaseBound 全局自动保存 2s 防抖]
  end

  subgraph 后端核心服务层 (FastAPI + LangGraph)
    CapBroker[CapabilityBroker 统一能力网关: 权限四元组]
    ClawGates[三层把关质检: 自审 / 交叉 / 独立第三方把关]
    ModelRouter[多 Provider 模型网关: Ollama 本地 / 云端 API / 指数退避智能降级]
    HybridRAG[端侧双轨检索: BM25 + sqlite-vec 混合召回]
    Resilience[T6 抗打断引擎: 状态台账流式落盘 + 断点原位续作]
  end

  subgraph 存储与数据底座 (100% 本地优先 · 隐私完全自治)
    SQLiteDB[(SQLite 嵌入式单文件: find-yourself.db)]
    VecDB[(sqlite-vec 本地向量扩展)]
    FileSystem[(本地资产库 .runtime/assets + S3 兼容对象存储)]
  end

  Gateway --> CapBroker
  Gateway --> ModelRouter
  Gateway --> HybridRAG
  Gateway --> Resilience

  CapBroker --> SQLiteDB
  HybridRAG --> VecDB
  Resilience --> SQLiteDB
```

---

## 🚀 极速启动与使用

### 方式一：Windows 桌面一键免安装极速运行（推荐）

1. 克隆或下载本仓库代码；
2. 双击根目录下的快速启动脚本：
   ```cmd
   start.bat
   ```
3. 系统将自动检测 Python 运行时、初始化本地 SQLite 数据库并拉起服务，在浏览器自动开启：  
   👉 **`http://127.0.0.1:8000`**

### 方式二：命令行手动启动

```bash
# 1. 创建并激活 Python 3.11+ 虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. 安装依赖并启动
pip install -e .
python run.py --port 8000
```

### 方式三：使用预编译桌面绿色包
直接下载随附的 Windows 安装压缩包 [`FindYourself-Windows-Setup-Preview.zip`](assets/FindYourself-Windows-Setup-Preview.zip)，解压后双击 `Install.bat` 即可开箱即用。

---

## 📖 开发者与核心服务端点

服务启动后，可在浏览器直接访问以下核心端点进行调试与使用：

| 访问地址 | 对应功能与说明 |
| :--- | :--- |
| `http://127.0.0.1:8000/` | Web 主工作台、画布工作流与用户首页 |
| `http://127.0.0.1:8000/docs` | OpenAPI / Swagger 交互式接口文档 (421 个标准化 API) |
| `http://127.0.0.1:8000/redoc` | ReDoc 工业级规格说明文档 |
| `http://127.0.0.1:8000/api/workbench/tree` | 极客工作台工作区文件目录树接口 |

---

## 🧪 自动化测试验证

本项目拥有覆盖率严密、断言清晰的工业级测试矩阵，保障各模块极其稳定：

```bash
# 运行后端全量测试套件 (109 个测试文件，1268 个用例全部通过)
python -m pytest tests/ -q

# 运行前端组件与单元测试
cd web
npm run test
```

---

## 🔒 隐私主权与离线安全承诺

1. **默认离线运作（Default-Offline）**：系统默认处于 `FY_OFFLINE_MODE=True`，未经用户明确授权，绝不静默向任何公网外传数据。
2. **数据资产主权**：所有工作流配置、本地向量索引、文档知识库与中间过程状态均保存在本地磁盘，彻底隔绝外部嗅探。
3. **不可逆审计链**：关键配置变更与越权拦截实时计入 SHA-256 审计哈希链，满足严苛的企业合规要求。

---

## 📄 开源许可证

本项目基于 [Apache License 2.0](LICENSE) 协议开源。
