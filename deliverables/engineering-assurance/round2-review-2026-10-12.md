# 第二轮接收、有限复核与阶段裁决

日期2026-10-12；起点99bac7c；指定cont-20261012-035438-xiaotengzi。不是全包验收，不是最终优化方案。

## 1. 先检查原全量，不重复启动
原session95676结果：4153passed/7failed/54skipped/116warnings，1111.02s；JUnit4214项、7failure、0error。原四失败不再出现。保存full-resume-20261012.log/.xml。
新7失败中6项已独立复现：导出代码子进程UTF8输出被Windows GBK读取线程解码报错，stdout/stderr变None；另1项桌面测试调用裸powershell，在裁剪PATH下WinError2。

改动：test_runtime_library.py、test_project_scaffold.py指定子进程PYTHONIOENCODING和读取encoding=utf8；test_desktop_window_lifecycle.py使用绝对WindowsPowerShell/taskkill路径，新增不打开窗口的路径回归。
红测：子进程6failed/30passed；路径契约NameError。绿测：相关37passed/2warnings，ruff三文件passed。**没有重新测试实际Edge窗口退出流程**；故不能把路径回归当成桌面E2E或全树绿。
T6同范围独立重跑206passed/2warnings/25.51s；仅测试断言范围，不是真实外部IMA、企业规模、全撤权或安全生产验收。

## 2. 第二轮定位与只读机械检查
T1/T2/T3/T5/T6共5组第二轮交接已定位（T1文件名HANDOFF-r2.md，不能假定统一HANDOFF.md）。T4暂仅定位第一轮，已向用户询问第二轮路径，**未重派任务**。
自写review_round2_receipts.py只读取交付，不执行外部脚本；各final文件16/10/26/8/42件，JSON/JSONL解析0错误；T2新增45条snapshot SHA全部match；T1提供的10规格各3条sample_path，共30样例归一化SHA全部match。收据round2-receipt-review-20261012.json。
以上不是全部源文件逐行复核或975条需求语义归并；也未执行会覆盖freeze/重写外部run的脚本。

## 3. 阶段裁决与边界

### 基线：分用途定锚，不将四条线强行合并
- 当前修复候选为本repo-sync/FY-Orbit审计分支；此轮前99bac7c，后续测试补丁需记录新commit。
- d57d4ca与99bac7c的src/migrations/web/desktop/find_yourself_dsl差异为空，本轮产品功能结论仍锚d57源码；测试结果锚各自实际测试树，不能把新结果倒记到公开main。
- 5055327是已保存公开快照，当前公开main须发布前再核验；af2f038本地main与27dd801另一树仅作版本/差异来源，不自动成为本轮施工基线。
- 不执行合并、推送或历史改写；“当前满足”必须逐条带commit与环境，而不是只写最新。

### T1：可以接收增量，不可标全目录验收
- 规格样例支持“换行符/BOM差异非内容分歧”；原大范围另一版本风险应降级，但30样例不是全部副本。
- 暂不统一源文件编码；保留raw SHA与normalized SHA两种身份，审计时不改来源。
- FYR-E-027仍unresolved；54/55定位补齐不能报55/55完成。
- 975条为机械抽取doc_claim，不能直接新增975功能或并入379条；须语义归并、保留来源和历史ID，由原整理Agent按冻结规则处理，主审裁决冲突。
- 92份独立内容仍未读。先按可能影响验收/许可/安全/业务目标分类，不以字节大小排序；HTML内嵌viewer代码不是同等新增需求。coverage的排除重算可只读既有inventory，不必再次遍历全盘；不得把缓存记scanned而声称读取。

### T2/T3：版本与业务证据接收，不背书竞争胜出
- T2的Coze国际正文/套餐/许可与FastGPT私有逐功能规格仍有限；保留blocked/unknown。同题基准未实测，不声称全面超越。
- T3的用户目标、引用语义、公网快照需要按记录类别验收；线上旧宣传与本地新主页是两对象，不将本地修订算已上线。

### T5：使用新收据替代用户估计
- 外部cloud-facts-r2.json记录Ubuntu22.04、2核、2GB RAM、40GB SSD、3Mbps、到期2026-11-08T00:07:04Z（北京08:07）；主审读取收据但未独立登录云机。
- 不以“>=4GB建议”宣布所有2GB部署不可能；它只约束特定多组件方案。先预算低并发最小部署与已有API复用，不默认升级付费套餐，不一次启动五组件。
- 默认导出11月1日前、恢复演练11月5日前是计划节点，未创建提醒或实施；安全门、路径选择、端口策略和真实E2E仍开放。

### T6：删文档与全域删除传播不是同一验收项
已读ingest.py delete_document及test_knowledge_ingest.py L162起断言，且206范围实跑通过。
CONF-T6-001的verified只可用于测试证明的文档/chunks删除与审计、跨owner拒绝；**不支持向量、图谱、缓存、长期记忆的完整删除/撤权传播已完成**。这是证据作用域裁决，不是关闭整项实现缺口。
chat_orchestration.py工具调用点未检查本次绑定白名单，源码已复核；实际攻击尚未演练，列高优先级待修，不据静态缺陷宣称利用已成功。
IMA两处注释相反不能选“已核对”作为真值；独立官方/实际接口证据待补。共享规则新增“IMA skill已实测”属于外部安装记录，不证明FY当前连接器已接通。
公开main凭据片段清理、轮换/历史改写属单独安全处置，不输出令牌，不擅自操作。

## 4. 后续门
先完成桌面生命周期隔离复验及完整后端回归；核对T4 R2位置；按风险验收来源与剩余冲突，再形成精确施工单（owner/MCP与凭证、工具白名单、真实usage/status、RAG撤权传播）。
配额守卫仍是已有budget/resilience/checkpoint扩展提案；不另建Agent或中心服务，不未经设计批准施工。
全目录总账补齐仍按用户分工交原整理Agent，本主审不代替其重新汇总全目录。尚未发布、部署、承诺生产级或定稿竞品覆盖。
