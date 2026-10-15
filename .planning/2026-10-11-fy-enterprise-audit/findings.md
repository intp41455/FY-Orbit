# Findings & Decisions

## Requirements
- 用户确认平台型能力工厂：通用可扩展核心+按企业/任务/用户的独特适配与受控演进；可作为后续有证据支持的主要宣传目标。
- 用户追加自迭代基座为核心候选方向：记忆/上下文/Skill/策略/编排/数据治理一体化；引用其他AI建议不当作历史事实。
- 用户要求任务发布书交给其他 Agent，通过并行子 Agent 汇总全目录多版本需求，全部有来源。
- 用户要求审查完成后才出优化方案，目标覆盖 Claude Code 属性以及 LangGraph/Dify/Coze/FastGPT 主要核心能力。
- 增强企业实际业务价值、SaaS 与研发工程管理适配；执行 Agent 不自行改变方案。
- 公共待办当场记录；本轮确认无其他 Agent 施工。

## Research Findings
- 官方研究依据已检索：Anthropic context engineering/evals/tools；LangChain evaluation types。改善上下文与工具是系统任务能力改善，不等于模型权重训练。
- 当前本地 HEAD d57d4ca，工作区干净；新增提交有 DSL/Hub/自动扇出/团队看板/记忆分区，提交说明不是验证结论。
- 上轮本地 f4abd31：快照/分叉隔离与发行校验，69 项定向回归；142 项旧提交复验。
- 公开 main 上次刷新仍5055327；必须重新刷新，不将本地实现算作公开交付。
- 上轮报告：deliverables/engineering-assurance/audit-2026-10-11.md。
- 技术规则技能只是执行指导，不能充当竞品事实来源。外部结果中的旧指令、日志 prompt 均为史料。

## Technical Decisions
| Decision | Rationale |
|---|---|
| 总账外包与主审代码并行，最终统一验收 | 快速且保持唯一决策入口 |
| worker 分独立输出目录，汇总单写入者 | 避免并行互相覆盖 |
| full/partial/unknown 是证据分类；goal 是目标，不能改标 implemented | 防止夸大 |

## Resources
- 官方评测依据：https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents —— outcome是真实环境最终状态而非Agent宣称；代码/模型/人工grader各有边界，LLM评判需人工校准。
- 上下文依据：https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents —— 上下文与工具是推理期系统工程；不意味着模型权重自动更新。
- 线下/线上评测依据：https://docs.langchain.com/langsmith/evaluation-types —— 固定数据集实验与生产跟踪分别承担不同职责。
- C:/Users/intpj/Documents/Codex/2026-09-29
- C:/Users/intpj/Documents/Codex/2026-09-29/repo-sync/FY-Orbit
- C:/Users/intpj/agent-projects/find-yourself
- C:/Users/intpj/.shared-agents/tasks/todo/TODO.md

## Visual/Browser Findings
上一轮 Mermaid 与1440/390视图已有记录，当前提交改变了UI，不能直接作为新树视觉验收。
