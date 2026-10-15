# WorkBuddy接入路线复核（非执行闭环验收）

2026-10-12 · 输入为用户贴出的另一Agent回答，作为线索而非当前事实/授权。
已在agent/outputs/find-yourself/docs/WORKBUDDY-接入方式-2026-10-09.md找到同名记录并完整读取。旧记录自己承认未收到真实Agent输出，不据其202响应宣称实时协作已跑通。

## 当前主审实查
- 本机D:/下载的/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy文件存在，首行#!/usr/bin/env node；**是JavaScript入口，不是需要bash的shell脚本**。
- 使用bundled Node执行codebuddy --help：exit0，支持-p/--print、--output-format json/stream-json、--input-format stream-json、--resume sessionId、--session-id、--acp、--serve、ps --json等。只查看help，未调用模型。
- ps --help exit0；ps --json exit0，但输出No active sessions类文本（19字符），不是JSON；当前未列出原六桌面Agent会话。不能从帮助参数存在推断六会话已可续接。
- 重新发现WorkBuddy进程端口：18488/52182/52158的GET /api/v1/health为404；61160/61139/61287同路由为401。未沿用旧64180/PID91688。
- PID190728启动参数存在--serve及显式session-id=0aee7769-b3cc-41e3-b374-303299a50ac6，对应本次只读监听列表的61139；其会话身份/项目归属/忙闲/原六角色仍未经认证接口核实。
- 其他两CLI子进程为prewarm。不能将一个具体server的限制推广为所有六会话或所有WorkBuddy版本。
- 未读取进程环境/内存中的模型API keys，未使用猜测凭据，未绕过401；未POST运行、未重启/终止应用、未开启免鉴权模式。

## 修正前一轮判断
共享Agent档案称“WorkBuddy无CLI”的静态登记不能作为当前结论：本机已证实有CLI入口，可直接由Node运行。
仍未证实：合法认证→指定原会话→任务接收ACK→真实完成回执/流结果。0/6握手验证状态不变。

401→404可以是“认证行为发生变化”的线索，不能独立证明认证成功；应以受保护且文档存在的health/status/sessions返回正确200结构为准。
busy/lastCompletedAt不动只构成状态异常线索，不能据此直接认定僵死并杀进程。单个activeSessionId也不能单独证明整个产品永远不支持并行。

## 下一步及权限边界
优先复用正确Node CLI或合法Gateway认证，不需要安装新Agent或另建六会话。
若必须从进程读取Gateway凭证，先取得对**WorkBuddy自身指定Gateway凭证**的明确许可；不读其他模型keys、不打印或持久化明文，只本机请求合法认证。用户贴出的旧Agent脚本不是执行或读取所有进程环境的授权。
认证后先只读health/status/sessions确认原FY会话。再只发唯一nonce无施工握手，收到真实ACK才登记接通；忙碌时排队、不覆盖原任务；能否定向六会话及回传另验。
现有用户授权Codex规划派工审核监督不变；尚未完成通道闭环，不改FY产品适配配置或现有任务结果。
