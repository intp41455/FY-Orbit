"""任务档案库与企业模式适配层 —— **P15**（A-三重模式-03 / A-上下文持久化-02 / -03）。

两个模块：

* :mod:`~find_yourself.services.dossier.archive` —— 任务档案库：
  「翻档案」一秒上手（A-上下文持久化-02）、任务复盘与知识沉淀（-03）。
* :mod:`~find_yourself.services.dossier.enterprise` —— 企业模式（A-三重模式-03）：
  万能适配 / 转换层（类 Spring AI ``ChatClient``），把企业既有 agent 框架的
  定义声明式映射到本仓的单循环内核。

**零新表**：档案是**读模型**——直接从既有 ``tasks`` / ``task_events`` /
``task_dependencies`` / ``task_attempts`` / ``agent_instances`` / ``team_definitions``
/ 审计哈希链现算，不落第二份冗余（冗余必然与真源漂移）。沉淀出来的经验模板
落 ``FY_DOSSIER_DIR``（默认 ``.runtime/dossier``），与 ``services/snapshot.py``
落 ``.runtime/snapshots`` 同一范式。

**企业模式不另起多租户**：权限与隔离复用既有的
:mod:`~find_yourself.services.grant` / :mod:`~find_yourself.services.auth` /
:mod:`~find_yourself.services.team_approval`，企业身份映射到既有 actor 模型
（owner / service），**不发明第二套授权语义**。
"""
