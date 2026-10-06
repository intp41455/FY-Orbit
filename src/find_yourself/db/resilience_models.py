"""T6 抗中断韧性数据表（中断台账 + 流式片段落盘）。

两张表都落在主库——**重启后仍在**，绝不允许内存态（WorkStash P1-04 同款规矩）：

* ``interruption_events`` —— 统一中断台账（T6-A/G7）：六类中断场景（S1–S6）
  每次中断一行，记录处置策略（auto/confirm/manual）、可续作指针
  （thread_id / message_id / stash_id）、供应商指纹（S5 换供应商检测）与
  恢复状态。恢复中心 API（``api/routes/recovery.py``）由此驱动。
* ``stream_segments``   —— 流式产出逐帧落盘（T6-D/G3）：``message_id + seq``
  唯一，存 SSE 帧原文；断流/断网后凭 Last-Event-ID 语义取回已产出片段，
  不重复、不丢失。

留痕分工：本模块的行是**可查询的状态**；每次中断与每次恢复同时往
``AuditService`` 哈希链挂帧（``interruption.recorded`` / ``interruption.resumed``），
可事后反查「哪次中断、丢了什么、怎么恢复的」。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class InterruptionEvent(Base):
    """一次已落盘的中断事件（T6-A 六类场景的统一台账行）。"""

    __tablename__ = "interruption_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 六类场景之一：rate_limited / stream_broken / network_lost / process_killed
    # / provider_reset / agent_misuse / unknown（runtime/interruption.py 枚举）
    interruption_class: Mapped[str] = mapped_column(String(40), nullable=False)
    task_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 可续作指针：LangGraph 持久检查点线程 / 流式 message_id / 自动暂存行
    thread_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    message_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    stash_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 处置策略：auto（扫描到即可自动续作）/ confirm（恢复前必须用户确认）/ manual
    resume_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    # 中断时的供应商指纹（model_provider/model_name/base_url 摘要）——
    # 恢复时不一致即视为 S5 换供应商，强制 confirm（红线 6）
    provider_fp: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # open / resumed / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    last_resume_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    resumed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("ix_interruption_status_created", "status", "created_at"),
        Index("ix_interruption_owner_created", "owner_id", "created_at"),
    )


class StreamSegment(Base):
    """一帧已落盘的流式产出（T6-D）：``(message_id, seq)`` 唯一。"""

    __tablename__ = "stream_segments"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # SSE 帧原文（含 event/data 行）。存原文保证取回即与在线流逐字节一致。
    frame: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        sa.UniqueConstraint("message_id", "seq", name="uq_stream_segments_msg_seq"),
        Index("ix_stream_segments_msg_seq", "message_id", "seq"),
    )
