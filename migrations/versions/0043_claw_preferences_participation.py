"""Alembic migration 0043: Claw 机制/参与 —— 决策偏好库 + 参与模式记忆.

两张新表（ORM 同构注册在 ``db/claw_models.py``）：

* ``claw_decision_preferences``  —— 决策偏好库（机制-04）：裁决/拍板/修改
  沉淀成可匹配的偏好，下次类似冲突自动套用（owner+pattern_key 唯一）。
* ``claw_participation_modes``   —— 参与模式记忆（参与-04）：全自动/关键
  节点/全程陪跑三档，选一次就记住（owner 唯一）。

Revision ID: 0043_claw_preferences_participation
Revises: 0042_claw_governance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_claw_preferences_participation"
down_revision: str | None = "0042_claw_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("claw_decision_preferences"):
        op.create_table(
            "claw_decision_preferences",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("pattern_key", sa.String(length=200), nullable=False),
            sa.Column("decision", sa.Text(), nullable=False),
            sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_task_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "pattern_key",
                                name="uq_claw_pref_owner_pattern"),
        )

    if not insp.has_table("claw_participation_modes"):
        op.create_table(
            "claw_participation_modes",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("mode", sa.String(length=20), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", name="uq_claw_participation_owner"),
        )


def downgrade() -> None:
    op.drop_table("claw_participation_modes")
    op.drop_table("claw_decision_preferences")
