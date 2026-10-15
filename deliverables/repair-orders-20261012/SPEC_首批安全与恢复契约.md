# 首批安全与恢复契约（设计确认稿）

来源：T4 R2 CON-T4-A-001/A-005/D-001，T6 R2业务工具白名单缺口；用户原要求权限隔离、真实交付、抗中断续接与受控自迭代。
主审2026-10-12在80af5278隔离内存数据库/纯节点调用实测：
- node_tool_step.output.status=not_executed，但node_validate返回completed，spent_usd凭空增加0.005。
- node_research仅接受granted_source_ids就输出verified=True。
- RecoveryService.scan(Actor.owner('bob'))可读owner='alice'的事件。
- startup_autoresume处理auto事件抛KeyError('provider_fp')。
前三项真值/隔离为发布阻断；本包先修聊天调用授权与恢复读写边界，不冒充已完整修复graph。

## C1 聊天工具执行契约
每轮实际调用前必须同时满足：工具名在本次tools元数据名字集合中；actor已认证、未过期；actor.require_task(task_id)通过；actor.require_tool(name)通过。比较精确名字，不支持模型自己扩白名单、别名、通配符或从知识文本获得授权。
不在本次集合：PermissionDenied code=tool_not_bound，通用说明“不在本次绑定工具清单”，不读目标registry元数据、不invoke、不发tool_call/tool_result成功帧。现有异常包装发error后message_end.finish_reason=error。
已绑定但服务主体工具权限不足：复用actor.require_tool的tool_forbidden；任务绑定不符复用task_mismatch。拒绝均必须发生在任何业务invoke之前，包含第二/第三续写轮。
授权工具继续用现有registry.invoke与参数验证，正向真实builtin调用不能被“全部禁用”替代。
没有绑定工具时保留纯文本SSE语义；malformed marker不触发工具。此契约不证明全局registry已owner分区，相关问题另排。

## C2 恢复事件授权与策略契约
RecoveryService.scan/resume要求actor.require_authenticated、actor.require_owner，且actor.owner_id非空；空owner抛ValidationFailed code=owner_required。服务主体不允许通过通用恢复入口批量读取/恢复。
查询必须在SQL范围限定InterruptionEvent.owner_id==actor.owner_id；空owner旧记录不认领、不展示、不自动恢复。resume外owner/旧空owner/不存在ID均NotFound code=interruption_not_found，先拒绝再读检查点/片段或改变状态。
所有manual事件始终只能走专用恢复流，即使provider_changed且confirm_provider_change=True，也抛resume_policy_manual。provider变化只能把auto升级confirm，不能把manual降级。
scan内resume.effective_policy：manual优先；否则provider变化为confirm；否则沿用事件策略。现有resume.provider_changed字段保留。
startup_autoresume不访问scan未返回的provider_fp：用item['resume']['provider_changed']与effective_policy判断；只处理所属owner、effective_policy=auto且provider_changed=False的事件。保留关闭开关、200事件扫描上限与单条失败记录；不因KeyError中断整个扫描。
_resume_graph判断返回语义：只有checkpoint显示completed可作为“不重跑”已完成收据；invoke结果必须status=completed才算图恢复成功。failed/blocked/pending/缺status等返回ok=False、原事件保持open；不因invoke没抛异常就mark_resumed。
读取与续作必须使用同一个已解析路径的saver，compile_task_graph(checkpointer=saver)，不能读settings指定库后又从全局默认库续作。
stream-only恢复仅表示持久片段可取回，不能在文案/验收中称业务任务completed。
不迁移或补填旧事件owner，不改变checkpoint模型/版本，不自动重放业务副作用。持久lease/跨workerexactly-once及模型版本冻结不在此包实现，必须继续列为未完成。
