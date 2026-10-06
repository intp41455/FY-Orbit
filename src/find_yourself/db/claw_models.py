"""Claw 治理域数据表（A-Claw架构-01/05 + 机制-02 + 增强-01）。

* ``claw_gate_decisions``  —— 三层把关裁决记录（自审/交叉验证/独立质检
  每层一行：verdict + findings），架构-01「层层把关」的留痕底座。
* ``claw_conflicts``       —— 冲突登记（六类冲突命名 + 四级升级状态机），
  架构-05/-06 与增强-01 的台账。
* ``claw_fact_baseline``   —— 全局事实基线库（机制-02）：任何 Agent 验证过
  的结论/确认的数据立刻同步进来，所有其他 Agent 实时读，杜绝各说各话；
  ``owner_id+fact_key`` 唯一（upsert 语义）。

留痕分工：本模块的行是可查询状态；每次裁决/冲突/事实同步同时往
``AuditService`` 哈希链挂帧（``claw.*`` 动作族），可事后反查。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class ClawGateDecision(Base):
    """一层把关的一次裁决。"""

    __tablename__ = "claw_gate_decisions"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    task_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    agent_role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # self_check / cross_validation / independent_qa
    layer: Mapped[str] = mapped_column(String(32), nullable=False)
    # pass / revise / reject / escalate
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    # GateFinding 列表：[{rule, message, severity}]
    findings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 本层引用过的事实基线键（对齐检查可追溯）
    fact_keys_checked: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_claw_gate_task_created", "task_id", "created_at"),
    )


class ClawConflictRecord(Base):
    """一次已登记的冲突（六类之一），带四级升级状态。"""

    __tablename__ = "claw_conflicts"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # jurisdiction / boundary / self_contradiction / persona / data_inconsistency / role_overreach
    conflict_class: Mapped[str] = mapped_column(String(40), nullable=False)
    parties: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 升级层级 1 自修复 / 2 协商·交叉验证 / 3 人工裁决 / 4 全局指令修正
    level: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    # open / escalated / resolved
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("ix_claw_conflict_status_created", "status", "created_at"),
    )


class ClawFactBaseline(Base):
    """全局事实基线库一行（机制-02）：key 唯一、验证者可溯、实时可读。"""

    __tablename__ = "claw_fact_baseline"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    fact_key: Mapped[str] = mapped_column(String(200), nullable=False)
    fact_value: Mapped[Text] = mapped_column(Text, nullable=False)
    verified_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_task_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("owner_id", "fact_key", name="uq_claw_fact_owner_key"),
    )
