"""Alembic migration 0044: P5 共享任务板 —— task_claims 表.

一张新表（ORM 同构注册在 ``db/claim_models.py``）：

* ``task_claims`` —— 共享任务板（A-Agent运行时-11 自主任务认领）：
  ``pending → claimed → done|failed`` 状态机，``claimed`` 带 ``lease_expires_at``
  租约（持有者崩溃后回收放回板上）。所有权由条件 UPDATE + RETURNING 原子裁决。

⚠️ 编号申领：本迁移编号由主控分配。若主控发号与本文件 ``revision`` 不一致，
以主控发号为准（改 ``revision`` 与 ``down_revision``，不改表结构）。

Revision ID: 0044_p5_task_claims
Revises: 0043_claw_preferences_participation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_p5_task_claims"
down_revision: str | None = "0043_claw_preferences_participation"
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
