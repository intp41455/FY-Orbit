"""Alembic migration 0046: P5 共享任务板 —— task_claims 表.

一张新表（ORM 同构注册在 ``db/claim_models.py``）：

* ``task_claims`` —— 共享任务板（A-Agent运行时-11 自主任务认领）：
  ``pending → claimed → done|failed`` 状态机，``claimed`` 带 ``lease_expires_at``
  租约（持有者崩溃后回收放回板上）。所有权由条件 UPDATE + RETURNING 原子裁决。

编号申领（主控 2026-10-07 裁定）：本文件原为 ``0044_p5_task_claims``，因与
P4 的 0044 撞车，主控裁决 **P4=0044 / P9=0045 / P5=0046**，故本迁移改为 0046
并挂到 ``0045_review_notes`` 之后（P9 的 revision 标识符**不带** ``p9_`` 前缀）。

Revision ID: 0046_p5_task_claims
Revises: 0045_review_notes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_p5_task_claims"
down_revision: str | None = "0045_review_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("task_claims"):
        op.create_table(
            "task_claims",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("capability", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("state", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("claimed_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_task_claims_board", "task_claims",
            ["state", "priority", "created_at"],
        )
        op.create_index(
            "ix_task_claims_lease", "task_claims",
            ["state", "lease_expires_at"],
        )
        op.create_index(
            "ix_task_claims_owner_created", "task_claims",
            ["owner_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("task_claims"):
        op.drop_index("ix_task_claims_owner_created", table_name="task_claims")
        op.drop_index("ix_task_claims_lease", table_name="task_claims")
        op.drop_index("ix_task_claims_board", table_name="task_claims")
        op.drop_table("task_claims")
