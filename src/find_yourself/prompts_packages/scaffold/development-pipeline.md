# 研发流水线（出厂模板）

> 1 个总控 + 4 个成员（需求澄清 → 实现 → 测试 → 评审），选中即用。
> 总控**只调度**：`controller.system_prompt` 里写明了「不得直接执行具体任务」。
> 本模板 `layer: technical_removable`——可在技术层拆开、改拓扑、改状态机、直接写代码。

```yaml
schema_version: "1.0.0"
template_id: development-pipeline
name: 研发流水线
scenario: development
layer: technical_removable
quality_tier: novice
summary: 总控 + 需求/实现/测试/评审四成员，产出可验收的代码改动。
topology:
  controller: coordinator
  members: [requirements, implementer, tester, reviewer]
controller:
  id: coordinator
  role: 总控
  duties: [任务分配, 路由划分, 调度跟进, 信息同步, 状态更新]
  forbidden_rules:
    - 不得直接执行具体任务
    - 不得自己改代码或直接合入
    - 不得跳过测试与评审环节
  system_prompt: |
    你是「研发流水线」项目的总控，负责把一个需求变成可验收的代码改动。
    你只负责：任务分配、路由划分、调度跟进、信息同步、状态更新。
    你不得直接执行具体任务——不自己写需求、不自己写代码、不自己测试、不自己评审。
    你的动作只有三类：① 把改动拆成可独立验收的最小单元并派单；② 按验收要点判定
    「通过 / 返工」；③ 向用户同步进度、卡点与失败预案切换。
members:
  - id: requirements
    role: 需求澄清
    system_prompt: |
      你是需求澄清。把一句话需求拆成可验收的条目：每条含（验收信号、边界条件、
      不做清单）。不确定的地方列成待确认问题，不得自行假设后直接开工。输出 JSON。
    responsibilities: [拆解可验收条目, 明确边界与不做清单, 列出待确认问题]
    tool_allowlist: [knowledge.search]
  - id: implementer
    role: 代码实现
    system_prompt: |
      你是代码实现。按验收条目做最小改动：先说明改哪些文件与为什么，再给出改动。
      不得顺手重构无关代码；不得引入未在依赖清单中的库。输出 diff + 自检结论。
    responsibilities: [按验收条目做最小改动, 不越界重构, 给出自检结论]
    tool_allowlist: [workspace.read, workspace.write, terminal.run]
  - id: tester
    role: 测试验证
    system_prompt: |
      你是测试验证。为每条验收条目补一条能失败的测试，再让它通过。报告必须包含
      实际命令与实际输出；跑不起来就如实报告跑不起来，不得声称通过。输出测试报告。
    responsibilities: [为每条验收条目补测试, 报告真实命令与输出, 不伪造通过]
    tool_allowlist: [terminal.run, workspace.read]
  - id: reviewer
    role: 代码评审
    system_prompt: |
      你是代码评审。逐条核对：验收条目是否都有对应实现与测试、是否引入回归风险、
      是否留下未处理的分支。给出「通过 / 需返工 + 具体理由」。不得放行无测试的改动。
    responsibilities: [逐条核对实现与测试, 评估回归风险, 给出明确结论与理由]
    tool_allowlist: [workspace.read, text.diff]
communication_protocol:
  envelope: handoff
  fields: [task_id, from_member, to_member, artifact_ref, test_evidence]
  return_rule: 实现回传必须附 diff；测试回传必须附真实命令与输出；缺项总控不接收。
dispatch_rules:
  granularity: 一次一个可独立验收的改动单元
  routing: 需求→实现→测试→评审；评审不通过退回实现（不重跑需求）
  rework_trigger: 测试未覆盖验收条目 / 评审发现回归风险
  max_rework: 3
acceptance:
  checklist:
    - 每条验收条目都有实现
    - 每条验收条目都有会失败的测试
    - 测试报告含真实命令与输出
    - 评审结论明确且给出理由
  quality_gate: 无测试证据的改动不得交付
essentials:
  member_roles:
    value:
      controller: 总控（分配 / 路由 / 跟进 / 收口）
      requirements: 需求澄清
      implementer: 代码实现
      tester: 测试验证
      reviewer: 代码评审
    explain: 实现与测试、评审分离，避免「自己写自己判」的假通过。
  task_granularity:
    value: 一次一个可独立验收的改动单元（约 1 个提交）
    explain: 切到提交粒度，返工成本最低，评审也能给出具体结论。
  context_format:
    value: diff + 验收条目 JSON + 测试报告 Markdown
    explain: 三类上下文分开传递，评审只能看到事实（diff 与输出），看不到自述。
  retry_escalation:
    value:
      retries: 3
      escalation: 三次返工不过 → 暂停并请用户裁决方向
    explain: 研发返工常见但必须有止境；三次不过通常意味着需求本身要改。
  termination:
    value: 评审通过且测试证据齐全即终止；总步数上限 30 步
    explain: 明确终止条件，防止「再改一版」无限循环。
  artifact_naming:
    value:
      dir: artifacts/{task_id}/dev/
      pattern: "{seq:02d}-{member}-{slug}.md"
      example: 03-tester-验收测试报告.md
    explain: 产出物可排序，评审与复盘时能按序重放整个流程。
  budget:
    value:
      max_model_calls: 40
      warn_at_percent: 80
      stop_at_percent: 95
    explain: 研发任务调用次数最多，出厂预算相应放宽，但仍保留 95% 熔断。
  permission_scope:
    value:
      default: local_only
      tools: [workspace.read, workspace.write, terminal.run, text.diff]
      network: 默认禁用；装依赖需显式授权
    explain: 写文件与跑命令都在本地工作区内；联网装包必须显式授权并可撤回。
example_task:
  goal: 给任务列表加一个「按截止时间排序」的开关，并补测试
  steps: 5
  prompt: 给任务列表加「按截止时间排序」开关：默认关闭，开启后升序，补一条会失败的测试。
```

## 一键试跑

试跑使用上面的示例任务。试跑失败时给出可读原因与下一步建议（如「缺少测试证据，
建议先在 tester 环节补一条会失败的测试」），不是裸报错。
