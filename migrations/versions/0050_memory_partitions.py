"""Alembic migration 0050: 记忆团队/成员分区列（三 tier 之外的归属轴）。

memories 新增：
* ``team_id``（nullable + index）：空 = 个人记忆；非空 = 团队共享记忆。
* ``agent_role``（nullable）：空 = 全队可见；非空 = 该成员私有。

与 tier（short/medium/long 生命周期轴）正交：一条记忆可以是
"team 共享 + long 久" 或 "某成员私有 + short 暂存"。
纯加列（nullable，无 CHECK），SQLite/Postgres 通用；inspect 护栏保证幂等。

Revision ID: 0050_memory_partitions
Revises: 0049_kanban_team
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_memory_partitions"
down_revision: str | None = "0049_kanban_team"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "memories" not in insp.get_table_names():
        return
    columns = [c["name"] for c in insp.get_columns("memories")]
    if "team_id" not in columns:
        op.add_column("memories", sa.Column("team_id", sa.String(length=64), nullable=True))
    if "agent_role" not in columns:
        op.add_column("memories", sa.Column("agent_role", sa.String(length=64), nullable=True))
    indexes = [i["name"] for i in insp.get_indexes("memories")]
    if "ix_memories_team_id" not in indexes:
        op.create_index("ix_memories_team_id", "memories", ["team_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "memories" not in insp.get_table_names():
        return
    columns = [c["name"] for c in insp.get_columns("memories")]
    indexes = [i["name"] for i in insp.get_indexes("memories")]
    if "ix_memories_team_id" in indexes:
        op.drop_index("ix_memories_team_id", table_name="memories")
    if "agent_role" in columns:
        op.drop_column("memories", "agent_role")
    if "team_id" in columns:
        op.drop_column("memories", "team_id")
