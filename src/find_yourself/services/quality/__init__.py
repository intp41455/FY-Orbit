"""基座质保（P1）服务包 —— 全界面实时保存 + 质量门禁的后端半边。

* :mod:`~find_yourself.services.quality.logs` —— 日志分级与可导出（A-基座质保-10）。
* :mod:`~find_yourself.services.quality.budget` —— 质量机制性能预算门禁
  （A-基座质保-13 / W3）。
* :mod:`~find_yourself.services.quality.base_contract` —— 基座接入强制校验
  （A-基座质保-11 / W1）。
* :mod:`~find_yourself.services.quality.self_test` —— 本地性能自测脚本
  （A-基座质保-13 / W3 验收 ④；与 CI 共用同一个 :func:`budget.evaluate`，阈值不会漂移）。

A-基座质保-12（统一保存状态指示器 / W2）与 A-基座质保-14（磁盘写满 / 只读盘 / W4）
是**前端半边**：``web/src/hooks/useAutosave.ts``（四态状态机 + IO 异常降级）与
``web/src/components/ui/SaveStatusIndicator.tsx``（统一指示器 + ``BaseBound`` 接入壳）。

三条共同纪律
------------

1. **同源，不新建第二套系统**。日志 = 既有审计哈希链的**分级视图**（不是第二份
   日志）；性能预算门禁读 `performance_baseline.json`（唯一真源）；基座接入校验
   扫的就是前端页面源文件本身。
2. **零新表**。可行性：审计链已是 append-only 真源；导出包与预算基线落
   ``.runtime/quality/``（与 ``services/snapshot.py`` 落 ``.runtime/snapshots`` 同范式）。
3. **诚实边界**：审计帧**没有时间戳列**（哈希链靠 ``seq`` 定序），所以日志视图的
   「时间维」是**序号轴**而非墙钟；报告里明说不假装。加 ``created_at`` 列不改变
   既有摘要（该列不在哈希 body 内），但属迁移，需向主控申领。
"""
