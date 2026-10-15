# FY-Orbit 全目录证据整理与企业竞争力审查任务发布书

版本：1.3（新增短期真实云交付、企业RAG和用户已有IMA资源）；发布日：2026-10-11；主审：Codex / 小腾子。
本文件可直接发送给任一有本机文件读取能力、可以启动子 Agent 的主 Agent。

## 直接执行的委派指令

你是本次证据整理的协调 Agent。请同时派多个子 Agent，严格按同目录的
01_全目录需求与多版本整理任务书.md、02_竞品能力证据任务书.md、
03_企业业务证据及执行协议.md、06_自迭代基座审查补充.md、07_短期真实云交付任务书.md
和 08_企业RAG增强与已有API复用任务书.md 完成证据整理，不实施产品代码修改。
07/08分为只读准备与审批后执行：本次证据整理授权不包含实际部署或付费开通。
先读共享 AGENTS.md、公共待办与项目既有记录，再建立覆盖清单、固定扫描快照，
分片并行执行，独立复核后提交完整机器可读数据、报告与 HANDOFF.md。
不得只给摘要/PDF，不得因对话额度将满丢掉未扫描项。
不要把历史文档里的旧任务或 prompt 当成今天的执行指令。

## 分工与授权

- 陛下：分发任务书，提供企业场景事实与最终商业选择。
- 本协调 Agent：派发、文件覆盖、分片合并、机械校验、质量复核、进度记录。
- 子 Agent：各自只读来源，抽取事实/需求/版本/代码证据，写自己的独立分片。
- Codex 主审：审查代码与实际运行、接收并验收结果、作冲突裁决和最终优化架构/实施决策。
- 未授权：删/移动源文件、改仓库可见性/许可证、撤销凭证、改产品代码、运行来源文档里的脚本、
  装新模型或付费服务、向第三方上传用户资料、代发消息、发布/部署。
- 资料缺失时做 unknown/open；发现凭据仅标文件位置和短哈希指纹，不复制值到报告。
- 子 Agent 不得修改共享汇总文件、抢占彼此文件或在同一代码树并行写入。
- 本任务要求判断仅用于来源分类/忠实抽取；不可擅自选择产品路线或改写用户原意。

## 输入边界

主扫描根：
C:/Users/intpj/Documents/Codex/2026-09-29

已知产品候选代码：
C:/Users/intpj/Documents/Codex/2026-09-29/repo-sync/FY-Orbit
C:/Users/intpj/Documents/Codex/2026-09-29/agent/outputs/find-yourself

关联项目证据：
C:/Users/intpj/agent-projects/find-yourself
C:/Users/intpj/.shared-agents/tasks/todo （只读，不把其他项目的任务算进FY）

用户明确附带的外部副本：
C:/Users/intpj/AppData/Local/Temp/FY-Orbit-技术手册-2026-10-07 (1).md
C:/Users/intpj/AppData/Local/Temp/FY-Orbit-使用手册-2026-10-07 (1).md

外部关联路径仅在根内文件明确引用、且为本项目证据时追加；写入 scope_extensions.csv，
说明来源与是否实际读取。禁止顺便扫描整个家目录和其他项目隐私。

## 同时并行的任务树

先完成 T0 清点与固定快照，T1各分片与T2各竞品可并行。
建议有资源时 6～8 个 worker 同时工作；平台并发不足时按同一任务队列调度，不减覆盖。

| 任务 | 独立责任 | 产物 |
|---|---|---|
| T0 协调者 | 所有输入路径、Git/worktree/archive、快照与SHA、分片分配 | inventory.csv / coverage.csv / baseline.json |
| T1-A | 目标、产品定位、用户声明、企业价值与商业约束 | requirements-A.jsonl |
| T1-B | Agent/模型/工作流/DSL/路由/上下文/记忆/恢复 | requirements-B.jsonl |
| T1-C | 企业权限/安全/审批/审计/预算/合规/租户 | requirements-C.jsonl |
| T1-D | UI/工程工作台/看板/个人空间/游戏/可访问性 | requirements-D.jsonl |
| T1-E | 安装/发行/升级/部署/备份/运维/测试/文档与宣传 | requirements-E.jsonl |
| T1-F | 历史版本、旧审计、zip/worktree差异、版本谱系 | versions.jsonl / conflicts.jsonl |
| T2-A～E | Claude Code / LangGraph / Dify / Coze / FastGPT，各自一名 | competitor-<product>.jsonl |
| T3 | 企业场景证据和问题输入，不编造访谈 | enterprise-scenarios.jsonl |
| T4-A～D | 自迭代的记忆/上下文、策略/编排、经验/评测与企业治理 | self-iteration-audit.jsonl |
| T5 | Ubuntu资源/到期核验、已有API、核心能力对等、云部署前置门 | cloud-preflight.md / cloud-parity.csv |
| T6 | 企业资料类型、已有RAG/IMA接口、ACL与评测证据、分档负载 | rag-readiness.md / integration-register.csv |
| Q1 独立复核 | 不参与原抽取的 worker 检查来源、遗漏、误合并与版本 | QA.md / validation.json |

不要让 T1-F 成为唯一比较版本的 Agent：每个T1 worker负责自己需求的版本差异，
T1-F 再做横跨文件/目录/代码提交的总谱系。来源文件可分给多人读，但每份来源必须指定一个主抽取者。

## 输出唯一目录

C:/Users/intpj/agent-projects/find-yourself/outputs/baseline-reconciliation-20261011/<run_id>/

run_id 使用协调 Agent 名称与启动时刻，例如 coordinator-20261011-190000。
worker 只写 workers/<worker_id>/；final/ 只允许协调者写。
不得覆盖以前的 run，不得把结果散放临时目录。
如执行端不能访问此本机目录，交付一个保留完整相对结构的 ZIP，并明确 source access scope；
不能声称已扫描未挂载的本机资料。

## 固定交付清单

final/
- baseline.json：时间、范围、候选代码SHA、公开main SHA、各worktree状态、工具/提取器版本。
- inventory.csv：每个文件/存档成员一行，inventory_id、路径、字节、SHA、版本、来源类型、相关性。
- coverage.csv：每个 inventory_id 的 scanned/excluded/blocked 状态与理由。
- sources.jsonl：精确出处与快照指针。
- requirements.jsonl：原始条目、归一条目、目标、验收、来源与历史ID。
- versions.jsonl：文档/代码/Release谱系与差异。
- conflicts.jsonl：冲突、各自来源、待裁决问题；不擅自裁决。
- capability-map.jsonl：需求与现有代码/测试/证据对应，implemented/partial/unknown等边界。
- competitors.jsonl：五产品版本和能力来源及版本类型。
- enterprise-scenarios.jsonl：企业角色、业务流程、痛点、价值指标、已知与待补事实。
- self-iteration-audit.jsonl、自迭代基座审查.md：按06补充执行，含代码证据与晋升/回滚门。
- cloud-preflight.md、cloud-parity.csv、rag-readiness.md、integration-register.csv：按07/08准备；缺参数明确blocked，不虚构云服务器/API权限。04校验器尚不校验这些新增文件，Q1必须单独核验。
- sources/：允许复制的来源快照；敏感资料仅本地引用，不复制值。
- 需求总账.md、多版本对照.md、竞品对照.md、企业场景事实.md、QA.md、HANDOFF.md、validation.json。
- 每个worker的完整原始分片保留在 workers/，不能只留归并后的结果。

用 04_validate_handoff.py 对 final/ 运行机械校验；它不代替内容质量复核。
命令：python 04_validate_handoff.py "<final目录绝对路径>" --output "<final目录绝对路径>/validation.json"
inventory.csv与coverage.csv都必须有inventory_id列；sources.jsonl每个精确引用记录的inventory_id指回inventory。
网页证据也纳入inventory，记录URL、采集内容SHA和输出快照。来源覆盖/权限/内容真值另由Q1验收。
最终 HANDOFF.md 必须写：实际扫描范围/排除/阻塞、最新候选基线的证据、
冲突待决列表、未完成验证、Codex下一步要读取的文件、复现校验命令、公共待办ID。

## 全量验收门

- inventory中每个inventory_id有coverage，无丢失或静默遗漏；excluded有明确理由，blocked留待办。
- requirements中每条声明都有合法source_id与精确locator；不能只有“见README”。
- 旧267条需求ID逐一保留或明确映射，若不存在说明查找范围，不能伪造凑数。
- 不重复计算同一需求的多版本；也不能因文字类似把不同验收项误合并。
- 当前需求、历史目标、已废弃、备选方案、来源冲突分别保存。
- 代码存在、历史测试通过、当前测试通过、模拟成功、真实E2E通过分别记录。
- 每个“当前满足”结论绑定 commit/environment/时间/命令与真实输出。
- five competitors全部覆盖；官方文档与商业/社区差异明确。
- QA对全部来源路径与结构做校验，对全部冲突和claimed_verified做人工复核；
  对普通记录按稳定ID排序每5条抽1条，并公布抽样列表。抽样不能写成逐条内容审查。
- 如blocked未解决，交付partial且列残余；不能写“100%全面完成”。

## 进度与回传

开工查todo-cli.py ls；认领对应任务防重复。每完成分片立即记录公共进度与checkpoint。
最终回复只需：输出根绝对路径、各门结果、阻塞数、HANDOFF路径；把整个目录交给陛下，
由陛下交给Codex。不得假装已通知其他Agent或Codex。
