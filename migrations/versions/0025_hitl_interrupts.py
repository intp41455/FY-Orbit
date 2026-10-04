"""Alembic migration 0025: Human-in-the-loop 执行中断表（需求 12）。

引入 ``hitl_interrupts`` —— 一行记录「某次执行停在某个检查点，正在等人决策」：

* ``execution_id`` 是自由 id（task / agent-team run / orchestrator lease 皆可），
  **刻意不加外键**：HITL 挂在多个互不共父表的执行面上，挂到 ``tasks`` 会让
  agent / lease 两种场景变成悬挂行。
* ``status`` 由 CHECK 限定为 pending / approved / rejected / cancelled / expired。
* ``ck_hitl_decided_shape`` 把「等待中 ↔ 已决策」变成**表级不变量**：pending 行
  不许带 decision，已决策行必须带。即使将来有人漏写分支，半截决策也落不了盘。
* ``options`` 存归一化后的 ``[{"value","label"}]``；「decision 必须属于 options」
  这条约束在服务层做——SQLite 与 Postgres 无法可移植地在 CHECK 里读JSON 数组。

Idempotency: 建表由 ``sa.inspect(bind)`` 守卫，沿用 0005-0024 的写法。

Revision ID: 0025_hitl_interrupts
Revises: 0024_hub_connections
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_hitl_interrupts"
down_revision: str | None = "0024_hub_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("hitl_interrupts"):
        op.create_table(
            "hitl_interrupts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), index=True),
            sa.Column("execution_id", sa.String(length=200), index=True),
            sa.Column("checkpoint", sa.String(length=200)),
            sa.Column("context", sa.JSON(), nullable=False),
            sa.Column("options", sa.JSON(), nullable=False),
            sa.Column("decision", sa.String(length=64), nullable=True),
            sa.Column("resolution", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("decided_by", sa.String(length=200), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "status IN ('pending','approved','rejected','cancelled','expired')",
                name="ck_hitl_status",
            ),
            sa.CheckConstraint(
                "((status = 'pending' AND decision IS NULL) OR "
                "(status <> 'pending' AND decision IS NOT NULL))",
                name="ck_hitl_decided_shape",
            ),
            sa.CheckConstraint(
                "(status <> 'pending' AND decided_at IS NOT NULL) OR "
                "(status = 'pending' AND decided_at IS NULL)",
                name="ck_hitl_decided_at_shape",
            ),
            sa.CheckConstraint("version >= 1", name="ck_hitl_version_positive"),
        )
        op.create_index(
            "ix_hitl_execution_status", "hitl_interrupts", ["execution_id", "status"]
        )
        op.create_index(
            "ix_hitl_owner_status", "hitl_interrupts", ["owner_id", "status"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("hitl_interrupts"):
        op.drop_index("ix_hitl_owner_status", table_name="hitl_interrupts")
        op.drop_index("ix_hitl_execution_status", table_name="hitl_interrupts")
        op.drop_table("hitl_interrupts")
