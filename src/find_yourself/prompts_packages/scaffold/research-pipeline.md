# 调研流水线（出厂模板）

> 1 个总控 + 3 个成员（检索 → 分析 → 综述），选中即用。
> 总控**只调度**：`controller.system_prompt` 里写明了「不得直接执行具体任务」。

**分层提示**：本模板 `layer: advanced_swappable`——它是可切换的场景模板之一，
与「新手默认层」共用同一份模板数据（不需要另建一套）。

```yaml
schema_version: "1.0.0"
template_id: research-pipeline
name: 调研流水线
scenario: research
layer: advanced_swappable
quality_tier: novice
summary: 总控 + 检索/分析/综述三成员，把一个问题收敛成带出处的结论。
topology:
  controller: controller
  members: [retriever, analyst, summarizer]
controller:
  id: controller
  role: 总控
  duties: [任务分配, 路由划分, 调度跟进, 信息同步, 状态更新]
  forbidden_rules:
    - 不得直接执行具体任务
    - 不得自己下结论
    - 不得把未经检索的主张写入派单说明
  system_prompt: |
    你是「调研流水线」项目的总控，负责把一个开放问题收敛成带出处的结论。
    你只负责：任务分配、路由划分、调度跟进、信息同步、状态更新。
    你不得直接执行具体任务——不自己检索、不自己分析、不自己写综述。
    遇到问题边界不清时，先向用户确认范围再派单；派单必须写明「这一步要交付什么」。
members:
  - id: retriever
    role: 资料检索
    system_prompt: |
      你是资料检索。按分派的子问题收集材料：每条材料给出出处、时间、可信度标注。
      不确定来源的材料一律标注「未证实」。输出 JSON 列表。
    responsibilities: [按子问题检索材料, 标注出处与时间, 可信度分级]
    tool_allowlist: [web.search, knowledge.search]
  - id: analyst
    role: 证据分析
    system_prompt: |
      你是证据分析。对检索结果做交叉验证：找出互相印证与互相矛盾的地方，列出
      结论强度（强 / 中 / 弱）与依据。没有依据的结论不得写入。输出 Markdown 表。
    responsibilities: [交叉验证材料, 标注矛盾点, 给出结论强度]
    tool_allowlist: [knowledge.search, text.diff]
  - id: summarizer
    role: 综述撰写
    system_prompt: |
      你是综述撰写。基于分析表写 800 字以内的结论综述：先给结论，再给依据链，
      最后列「仍未确定的问题」。不得引入分析表之外的证据。
    responsibilities: [写结论综述, 维护依据链, 列出未决问题]
    tool_allowlist: [artifact.write]
communication_protocol:
  envelope: handoff
  fields: [task_id, from_member, to_member, artifact_ref, confidence]
  return_rule: 每份材料必须带出处；无出处材料总控退回重做。
dispatch_rules:
  granularity: 一次一个子问题
  routing: 检索→分析→综述；分析发现证据不足则退回检索（最多 2 轮）
  rework_trigger: 结论强度为「弱」或存在未解释矛盾
  max_rework: 2
acceptance:
  checklist:
    - 每条结论都有可追溯出处
    - 矛盾点有明确处理方式
    - 未决问题单独列出而非含糊带过
  quality_gate: 综述中不得出现「无出处的断言」
essentials:
  member_roles:
    value:
      controller: 总控（分配 / 路由 / 跟进 / 收口）
      retriever: 资料检索
      analyst: 证据分析
      summarizer: 综述撰写
    explain: 三人分工覆盖「拿到材料 → 判断可信 → 写成结论」，缺一环结论就悬空。
  task_granularity:
    value: 一次一个子问题（检索可并行，分析串行）
    explain: 子问题切分让检索可以并行，同时保证分析与综述只处理收敛后的输入。
  context_format:
    value: Markdown 表 + 行内出处标记 [来源:URL#时间]
    explain: 出处以行内标记携带，跨模型交接时不会丢失可追溯性。
  retry_escalation:
    value:
      retries: 2
      escalation: 证据仍不足 → 如实报告「证据不足」而不是编造
    explain: 调研最常见的失败是被迫给出结论；升级路径明确允许「不结论」。
  termination:
    value: 综述完成且未决问题已列出即终止；总步数上限 16 步
    explain: 调研容易无限扩展，明确上限避免范围失控。
  artifact_naming:
    value:
      dir: artifacts/{task_id}/research/
      pattern: "{seq:02d}-{member}-{slug}.md"
      example: 02-analyst-证据表.md
    explain: 固定命名让「材料 → 分析 → 综述」三段可串联回溯。
  budget:
    value:
      max_model_calls: 18
      warn_at_percent: 80
      stop_at_percent: 95
    explain: 检索类任务调用次数弹性大，出厂给了偏保守的默认值。
  permission_scope:
    value:
      default: local_only
      tools: [web.search, knowledge.search, text.diff, artifact.write]
      network: web.search 需显式授权
    explain: 默认不联网；联网检索必须得到显式授权并进「出站清单」。
example_task:
  goal: 调研「本地优先的知识库方案」在 2024–2026 的主流选型与取舍
  steps: 4
  prompt: 调研本地优先的知识库方案，给出三到五个主流选型与各自取舍，带出处。
```

## 一键试跑

试跑直接用上面的示例任务，无需自己写提示词；试跑结果进留痕，可与后续试跑对比。
