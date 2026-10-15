# 既有六Agent统一接入：当前通道核验

2026-10-12 · 用户已明确授权本Codex作为FY项目规划、派工、审核和监督中心，直接对接原六Agent；用户只与本中心沟通。本授权不等于任意新建六Agent、自动扩大施工范围或替换原会话。

## 本轮实查，而非根据注册文档推断
- Codex应用list_threads可调用，但当前列出的任务未匹配本轮原六个外部Agent会话；没有向旧FY审查会话误发消息。
- 共享tasks-cli.py是按平台的文件队列，AGENTS枚举无zcode；文档称桌面端启动时认领。无已证明的原六会话即时推送/唤醒闭环。
- 五V70 run的部分baseline无platform/session_id字段；run_id不自动等于可调用session_id。
- 现有进程OpenCode.exe/WorkBuddy.exe/ZCode.exe/opencode2api.exe均已只读发现；不启动或停止它们。
- OpenCode.exe持有127.0.0.1:56255；尝试官方形状/global/health、/doc、/session/status均HTTP401。说明需合法认证；尚未确认其完整协议或取得会话列表。未尝试绕过认证、猜密码或发模型请求。
- opencode2api.exe持有8080/8081：模型接口候选，不等于会话派工接口；未调用或改变，尤其不当原Agent会话用。
- WorkBuddy有本机监听端口，协议/具体会话未核；未假定有OpenCode API。ZCode进程已见，但此范围未发现其进程持有监听端口，不能据此断言它完全无接口。
- 用户说已开权限后，分别尝试unified cua.getState及computer-use技能要求的node_repl @oai/sky初始化，均失败：failed to write kernel assets，系统找不到指定路径(os error 3)。没有完成应用窗口选择/截图/输入，没有自动重启。该错误不是证明用户拒绝权限。

## 事实结论
**当前0/6具体会话派工握手被本轮验证。** 共享写盘、程序在运行、端口在监听、HTTP401都不能当作“消息已送达/Agent已接收”。未创建替代Agent或假装已通知。
MCP仅当服务实际暴露定向发任务/回执工具时可用；通用memory_save/kb_search不自动成为消息推送。
HTTP/CLI需能定位原session并合法认证；模型生成API不替代Agent会话API。CUA可作支持环境下的UI替代通道，当前初始化失败暂不可用。
官方API参考：[OpenCode Server](https://opencode.ai/docs/server/)（2026-10-12读取）；其健康/会话/异步消息接口说明不证明本机同名桌面应用已兼容。

## 最小接入门：一次登记，而不是再分发整套任务
原T1~T6各需登记role、platform、原session_id/可识别会话、接口base_url或现成CLI入口、认证来源引用（不写密钥）、现有run与输出路径。
先派无施工握手：带唯一nonce，要求原会话返回role/session/run/nonce及当前任务状态。接收回执后才分配独占工作，不发送完整V70总包给每位协调者。
无法直连的一组标not_connected/blocked，不在工具日志中标sent/acknowledged。用户不承担日常转述；若平台当前只支持手动消息，应明确其还未满足免人工中转目标。

## 角色与范围
本中心只规划、分工、审查、监督及通道诊断，不在本授权下代替执行者写产品功能。
ZCode原收口run仍是候选总账单写者，不负责取代Codex做最终产品决策；其余Agent交域分片，不产生第二份总协调。
当前V70目标及不施工门不变。接入握手不授权模型/接口安装、付费、端口/安全权限变更或重启应用；若需这些变化先说明具体项目与风险。
