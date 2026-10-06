"""开箱模板（系统级多 Agent 脚手架）服务包。

本包是 **P13** 的唯一落点：

* :mod:`find_yourself.services.templates.scaffold` —— 模板 schema（v1.0.0，冻结）
  与 ``ScaffoldTemplateService``。出厂模板内容在
  ``prompts_packages/scaffold/*.md``（纯内容包，落文件即装）。

**schema 契约（跨包锁四）**：``TEMPLATE_SCHEMA_VERSION`` 与
:func:`~find_yourself.services.templates.scaffold.template_schema` 是本仓
「系统级模板」的唯一真源。P12（市场与导入导出）与 P16（``.clawtask``）
按此契约消费，**不得各写各的模板结构**。

⚠️ ``services/templates/market.py`` 归 **P12**（模板市场与导入导出）。
本包不提供市场能力，只提供模板实体、分层、质量档位与「展开为代码」通道。
"""
