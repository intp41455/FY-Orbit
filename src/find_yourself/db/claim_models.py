"""P5 · 共享任务板数据表（A-Agent运行时-11 自主任务认领）。

一张表落在主库——**跨进程、重启后仍在**（绝不允许内存态，与 WorkStash P1-04 /
中断台账同款规矩）：多个 agent 各自一个进程时，只有 DB 层的原子裁决才对
「谁认领了这条任务」有真保护力。

* ``task_claims`` —— 任务板行：``pending → claimed → done|failed``；
  ``claimed`` 行带 ``lease_expires_at`` 租约，持有者崩溃后由
  ``TaskBoard.reclaim_expired`` 放回 ``pending``。

留痕分工：本表的 ``state`` 是**可查询的所有权状态**；每次发布/认领/续租/结单/
回收同时往 ``AuditService`` 哈希链挂帧（``claim.published`` / ``claim.claimed``
/ ``claim.renewed`` / ``claim.completed`` / ``claim.reclaimed_expired``），
可事后反查「这条任务是谁、什么时候拿走的、为什么又回到板上」。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class TaskClaim(Base):
    """共享任务板的一行（A-Agent运行时-11）。

    所有权由 ``state`` + ``claimed_by`` 共同表达；``attempt`` 记录被认领次数
    （便于发现「反复认领、反复失败」的坏任务）。
    """

    __tablename__ = "task_claims"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 任务载荷（执行所需的一切上下文）。JSON 列——跨库一致。
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 能力标签（可选）：认领时可只挑与自己能力相符的任务。
    capability: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # 优先级 1（最高）~ 9（最低），与 UnifiedScheduler 的 PRIORITY 语义对齐。
    priority: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=5)
    # pending / claimed / done / failed
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # 所有权持有者（owner_id 或 service_id）
    claimed_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    claimed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: 租约到期时刻——过期即可被 reclaim_expired 放回板上（持有者崩溃兜底）。
    lease_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    last_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        # 查板热路径：按 state 过滤 + 优先级/时间排序。
        Index("ix_task_claims_board", "state", "priority", "created_at"),
        # 过期回收热路径：state + 租约到期。
        Index("ix_task_claims_lease", "state", "lease_expires_at"),
        Index("ix_task_claims_owner_created", "owner_id", "created_at"),
    )
