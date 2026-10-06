"""P4 · 存档分叉数据表（A-存档回溯-04/05/06）。

一张表落在主库——**重启后仍在**（存档历史不可丢失，红线）：

* ``archive_forks`` —— 分叉记录：一次「从某存档点改参重跑」产生的分支，
  记录父 thread / 父 checkpoint / 新 thread / 参数覆盖 / 两侧结果快照。
  分支一旦建立**只写新行**，绝不回头改父点的任何数据（不覆盖原历史）。

留痕分工：本表的 ``state`` 是可查询的分支状态；每次分叉/弃用同时往
``AuditService`` 哈希链挂帧（``fork.created`` / ``fork.discarded``），
可事后反查「这条分支从哪个点分出来的、改了什么参数、为什么被弃用」。
"""

from datetime import datetime

from sqlalchemy import Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .types import ID, TZDateTime, utcnow


class ArchiveFork(Base):
    """一条存档分叉记录（A-存档回溯-04）。"""

    __tablename__ = "archive_forks"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 父分支（被回溯的原 thread）——分叉的起点
    source_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 被回溯的具体检查点 id；空串表示「该 thread 的最新检查点」
    source_checkpoint_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 新分支的 thread_id（重跑用）
    new_thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # 关联的文件系统侧快照（services/snapshot.py）与会话状态侧锚
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    session_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 改动的参数覆盖（键值对）——「改参重跑」改的就是这里
    overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: 两侧执行结果快照（供 compare 落档；也允许 compare 时即时传入覆盖）
    left_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    right_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # active / discarded
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    discard_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_archive_forks_source_created", "source_thread_id", "created_at"),
        Index("ix_archive_forks_new_thread", "new_thread_id"),
        Index("ix_archive_forks_owner_created", "owner_id", "created_at"),
    )
