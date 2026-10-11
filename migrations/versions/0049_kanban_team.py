"""Alembic migration 0049: 看板团队归属列（中央共享板 + per-agent 子视图）。

tasks 新增：
* ``team_id``（nullable + index）：空 = 个人任务；非空 = 归属团队。
* ``assignee_role``（nullable）：空 = 全队可见；非空 = 该成员个人分板。

纯加列（nullable，无 CHECK、无 server_default），SQLite/Postgres 通用；
新建库由 0001 ``create_all`` 建全表，此处 inspect 护栏保证幂等。

Revision ID: 0049_kanban_team
Revises: 0048_canvas_autosaves
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_kanban_team"
down_revision: str | None = "0048_canvas_autosaves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "tasks" not in insp.get_table_names():
        return
    columns = [c["name"] for c in insp.get_columns("tasks")]
    if "team_id" not in columns:
        op.add_column("tasks", sa.Column("team_id", sa.String(length=64), nullable=True))
    if "assignee_role" not in columns:
        op.add_column("tasks", sa.Column("assignee_role", sa.String(length=64), nullable=True))
    # 索引独立护栏（新建库上 create_all 已建，勿重复）。
    indexes = [i["name"] for i in insp.get_indexes("tasks")]
    if "ix_tasks_team_id" not in indexes:
        op.create_index("ix_tasks_team_id", "tasks", ["team_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "tasks" not in insp.get_table_names():
        return
    columns = [c["name"] for c in insp.get_columns("tasks")]
    indexes = [i["name"] for i in insp.get_indexes("tasks")]
    if "ix_tasks_team_id" in indexes:
        op.drop_index("ix_tasks_team_id", table_name="tasks")
    if "assignee_role" in columns:
        op.drop_column("tasks", "assignee_role")
    if "team_id" in columns:
        op.drop_column("tasks", "team_id")
