"""Alembic migration 0044: P4 存档分叉 —— archive_forks 表.

一张新表（ORM 同构注册在 ``db/fork_models.py``）：

* ``archive_forks`` —— 存档分叉记录（A-存档回溯-04/05/06）：
  一次「从某存档点改参重跑」产生的分支，记录父 thread / 父 checkpoint /
  新 thread / 参数覆盖 / 两侧结果快照 / 分支状态。分叉**只写新行**，
  绝不改动父点数据（不覆盖原历史）。

编号申领（主控 2026-10-07 裁定）：P4 保持 **0044**，但**文件名不带 ``_p4_`` 标记**
（主控要求迁移文件名只留编号 + 语义，不留包号）。本文件由
``0044_p4_archive_forks.py`` 改名为 ``0044_archive_forks.py``；``revision`` 同步
改为 ``0044_archive_forks``（主控裁定 P4=0044 / P9=0045 / P5=0046 线性串链）。

Revision ID: 0044_archive_forks
Revises: 0043_claw_preferences_participation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_archive_forks"
down_revision: str | None = "0043_claw_preferences_participation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("archive_forks"):
        op.create_table(
            "archive_forks",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("source_thread_id", sa.String(length=200), nullable=False),
            sa.Column("source_checkpoint_id", sa.String(length=200), nullable=False,
                      server_default=""),
            sa.Column("new_thread_id", sa.String(length=200), nullable=False),
            sa.Column("snapshot_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("session_key", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("overrides", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("left_result", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("right_result", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("label", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("discard_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_archive_forks_source_created", "archive_forks",
            ["source_thread_id", "created_at"],
        )
        op.create_index(
            "ix_archive_forks_new_thread", "archive_forks", ["new_thread_id"],
        )
        op.create_index(
            "ix_archive_forks_owner_created", "archive_forks",
            ["owner_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("archive_forks"):
        op.drop_index("ix_archive_forks_owner_created", table_name="archive_forks")
        op.drop_index("ix_archive_forks_new_thread", table_name="archive_forks")
        op.drop_index("ix_archive_forks_source_created", table_name="archive_forks")
        op.drop_table("archive_forks")
