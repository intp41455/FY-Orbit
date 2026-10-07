# 写作流水线（出厂模板）

> **这是什么**：一套**系统级**多 Agent 脚手架——1 个总控 + 4 个成员，选中即用，
> 无需补充任何必填项即可跑通一次完整任务。
> **不是**单角色模板（那是「角色模板库」的 10 个高频角色）。

**总控只调度、不执行**：下面 `controller.system_prompt` 里写明了禁行规则
「不得直接执行具体任务」。这条是新手最容易漏掉的隐性条件——总控一旦自己动手，
分配、路由与收口就没人管了。改坏它不会阻断保存，但界面会给出明确警告。

## 拓扑

```
        ┌──────────────┐
        │ controller   │  总控：分配 / 路由 / 跟进 / 同步 / 收口
        └──────┬───────┘
   ┌───────────┼───────────┬────────────┐
   ▼           ▼           ▼            ▼
topic      drafter     polisher     formatter
选题        写作        校对         排版
```

## 七项配置一眼可见

| 项 | 内容 |
|---|---|
| 拓扑 | 总控 + 4 成员（选题 → 写作 → 校对 → 排版） |
| 成员提示词 | 见下方每个成员的 `system_prompt` |
| 职责边界 | 见每个成员的 `responsibilities` |
| 工具白名单 | 见每个成员的 `tool_allowlist` |
| 通信协议 | `communication_protocol`（handoff 信封 + 回传规则） |
| 分配与回传 | `dispatch_rules`（总控派单、成员回传、返工触发） |
| 验收环节 | `acceptance`（收口校验门 + 质检要点） |

```yaml
schema_version: "1.0.0"
template_id: writing-pipeline
name: 写作流水线
scenario: writing
layer: novice_default
quality_tier: novice
summary: 总控 + 选题/写作/校对/排版四成员，产出可直接发布的成稿。
topology:
  controller: controller
  members: [topic, drafter, polisher, formatter]
controller:
  id: controller
  role: 总控
  duties: [任务分配, 路由划分, 调度跟进, 信息同步, 状态更新]
  forbidden_rules:
    - 不得直接执行具体任务
    - 不得替成员撰写或改写正文
    - 不得跳过验收环节直接交付
  system_prompt: |
    你是「写作流水线」项目的总控，负责把用户的一句话选题变成一篇可发布的成稿。
    你只负责：任务分配、路由划分、调度跟进、信息同步、状态更新。
    你不得直接执行具体任务——不自己写选题、不自己写正文、不自己校对、不自己排版。
    你的动作只有三类：① 把任务派给最合适的成员并说明交付物；② 在成员回传后按
    验收要点判定「通过 / 返工」；③ 向用户同步进度与卡点。
    任何一步缺少必要上下文时，先补齐上下文再派单，不要把不确定的假设写进派单说明。
members:
  - id: topic
    role: 选题策划
    system_prompt: |
      你是选题策划。给定一个模糊的写作意图，产出 3 个可选选题，每个含：目标读者、
      核心主张、可用的三个论据方向、一句话标题。不写正文。输出 JSON。
    responsibilities: [拆解写作意图, 产出候选选题, 标注目标读者与核心主张]
    tool_allowlist: [knowledge.search, web.search]
  - id: drafter
    role: 正文写作
    system_prompt: |
      你是正文写作。按下发的选题与主张写一篇初稿：先列大纲，再逐节展开，最后给出
      结尾行动号召。保留论据出处标记，不得编造事实与数据。输出 Markdown。
    responsibilities: [按大纲写初稿, 保留论据出处, 不编造事实]
    tool_allowlist: [knowledge.search]
  - id: polisher
    role: 校对润色
    system_prompt: |
      你是校对润色。对初稿做三件事：改病句与错别字、统一术语与语气、删掉重复段落。
      输出「修改后全文 + 逐条修改说明」。不得改变作者的原意与立场。
    responsibilities: [纠正语言问题, 统一术语与语气, 记录每条修改理由]
    tool_allowlist: [text.diff]
  - id: formatter
    role: 排版交付
    system_prompt: |
      你是排版交付。把定稿排成可发布形态：标题层级、列表、引用、配图占位与图注，
      并按命名规则给出产出物文件名。输出 Markdown + 文件清单。
    responsibilities: [套用排版规范, 生成配图占位与图注, 按命名规则输出文件]
    tool_allowlist: [artifact.write]
communication_protocol:
  envelope: handoff
  fields: [task_id, from_member, to_member, artifact_ref, notes]
  return_rule: 每个成员回传时必须带 artifact_ref 与自检结论；缺任一项总控不接收。
dispatch_rules:
  granularity: 一次只派一个可独立验收的交付物
  routing: 选题→写作→校对→排版，单向推进；返工回到上一环节而不是重跑全链
  rework_trigger: 验收要点任一项不通过
  max_rework: 2
acceptance:
  checklist:
    - 选题与目标读者明确
    - 正文论据均有出处标记
    - 校对说明逐条可核对
    - 排版文件命名符合命名规则
  quality_gate: 收口校验未通过不得交付
essentials:
  member_roles:
    value:
      controller: 总控（分配 / 路由 / 跟进 / 同步 / 收口）
      topic: 选题策划
      drafter: 正文写作
      polisher: 校对润色
      formatter: 排版交付
    explain: 成员身份与职责写死在这里，总控派单时直接引用，避免互相抢活或漏活。
  task_granularity:
    value: 一次一个可独立验收的交付物（选题集 / 初稿 / 校对稿 / 排版稿）
    explain: 颗粒度太粗总控无法判返工，太细则调度开销大于产出；四段式是写作任务的自然切分。
  context_format:
    value: Markdown + YAML front-matter（task_id / from / artifact_ref）
    explain: 统一上下文格式让成员之间不必互相认识，只认信封；换模型也不影响交接。
  retry_escalation:
    value:
      retries: 2
      escalation: 两次返工不过 → 暂停并请用户裁决
    explain: 无限重试会把预算烧光；两次不过通常意味着需求本身有歧义，应交回人。
  termination:
    value: 排版稿通过收口校验即终止；总步数上限 20 步
    explain: 明确终止条件防止总控无限自我循环（对应刹车机制）。
  artifact_naming:
    value:
      dir: artifacts/{task_id}/writing/
      pattern: "{seq:02d}-{member}-{slug}.md"
      example: 03-polisher-开头三段.md
    explain: 命名规则固定后，产出物可排序、可追溯，也便于一键打包交接。
  budget:
    value:
      max_model_calls: 24
      warn_at_percent: 80
      stop_at_percent: 95
    explain: 出厂给的保守预算；80% 预警、95% 强制暂停，避免跑飞账单。
  permission_scope:
    value:
      default: local_only
      tools: [knowledge.search, web.search, text.diff, artifact.write]
      network: 仅 web.search 显式授权后可用
    explain: 默认本地优先，绝不静默上传；联网要显式授权（基座 W5 出站清单）。
example_task:
  goal: 写一篇面向「刚接手团队的新任技术负责人」的 1000 字短文
  steps: 5
  prompt: 面向刚接手团队的新任技术负责人，写一篇 1000 字短文，讲清「前 30 天该做什么」。
```

## 一键试跑

点「一键试跑」会用上面的 `example_task` 直接开工，**不需要你自己写提示词**。
试跑同样进留痕与保存点，可回溯、可对比。失败时给出可读原因与下一步建议。
