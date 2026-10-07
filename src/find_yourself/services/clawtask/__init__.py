"""任务可移植（``.clawtask``）服务包 —— **P16**。

三层递进（A-任务可移植-03 / -04 / -05）：

* 第二层 :mod:`~find_yourself.services.clawtask.format` —— 标准任务格式
  ``.clawtask``：单文件、自描述、跨模型 / 跨框架可读可迁、像传游戏存档一样可分享。
* 第三层 :mod:`~find_yourself.services.clawtask.market` —— 任务市场（任务模板挂卖）。
* 贯穿 :mod:`~find_yourself.services.clawtask.hibernate` —— 冬眠机制（临界封存 + 一键唤醒）。

**schema 契约（跨包锁四）**：``.clawtask`` 文档里的 ``system_template`` 段引用
:data:`~find_yourself.services.templates.scaffold.TEMPLATE_SCHEMA_VERSION`
（P13 冻结的模板契约）。本包**照契约实现**，不自造第二套模板结构。

⚠️ **范围声明（诚实边界）**：``market`` 只做**本地目录式**的挂卖与分发机制
（发布 / 检索 / 导入 / 版本与完整性校验），**不含账号与支付**。需求原文的
「挂市场卖 5 块」涉及账号与交易，属合规敏感面——按派单通知书要求，
本轮不做，待主控确认后再单独立项。
"""
