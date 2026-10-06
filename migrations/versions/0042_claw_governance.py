"""Alembic migration 0042: Claw 治理域 —— 三层把关裁决 + 冲突登记 + 事实基线库.

三张新表（ORM 同构注册在 ``db/claw_models.py``）：

* ``claw_gate_decisions`` —— 三层把关每层一行的裁决留痕（A-Claw架构-01）
* ``claw_conflicts``     —— 六类冲突登记 + 四级升级状态（架构-05/06、增强-01）
* ``claw_fact_baseline`` —— 全局事实基线库（机制-02，owner+fact_key 唯一）

Revision ID: 0042_claw_governance
Revises: 0041_kanban_board
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_claw_governance"
down_revision: str | None = "0041_kanban_board"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("claw_gate_decisions"):
        op.create_table(
            "claw_gate_decisions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("task_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("agent_role", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("layer", sa.String(length=32), nullable=False),
            sa.Column("verdict", sa.String(length=16), nullable=False),
            sa.Column("findings", sa.JSON(), nullable=False),
            sa.Column("fact_keys_checked", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_claw_gate_task_created", "claw_gate_decisions",
                        ["task_id", "created_at"])

    if not insp.has_table("claw_conflicts"):
        op.create_table(
            "claw_conflicts",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("conflict_class", sa.String(length=40), nullable=False),
            sa.Column("parties", sa.JSON(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_claw_conflict_status_created", "claw_conflicts",
                        ["status", "created_at"])

    if not insp.has_table("claw_fact_baseline"):
        op.create_table(
            "claw_fact_baseline",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("fact_key", sa.String(length=200), nullable=False),
            sa.Column("fact_value", sa.Text(), nullable=False),
            sa.Column("verified_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("source_task_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "fact_key", name="uq_claw_fact_owner_key"),
        )


def downgrade() -> None:
    op.drop_table("claw_fact_baseline")
    op.drop_index("ix_claw_conflict_status_created", table_name="claw_conflicts")
    op.drop_table("claw_conflicts")
    op.drop_index("ix_claw_gate_task_created", table_name="claw_gate_decisions")
    op.drop_table("claw_gate_decisions")
