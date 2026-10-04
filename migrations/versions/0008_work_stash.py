"""Alembic migration 0008: work-stash table (工单 P1-04).

Creates one table:

- ``work_stashes`` — user working-record staging area (content + metadata,
  owned by ``owner_id``). Persisted so staged records survive backend
  restarts; restored/listed/cleared via ``/api/stash``.

No existing table is touched. Idempotency: the CREATE is guarded by
``sa.inspect(bind)`` — an existing table is skipped — following the
0005/0006/0007 idiom. Downgrade drops the table only if present (equally
idempotent).

Revision ID: 0008_work_stash
Revises: 0007_prompt_templates
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_work_stash"
down_revision: str | None = "0007_prompt_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "work_stashes"


def _existing_tables(insp: sa.Inspector) -> set[str]:
    return set(insp.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE not in _existing_tables(insp):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_type", sa.String(length=100), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_work_stashes")),
        )
        insp2 = sa.inspect(bind)
        idx = "ix_work_stashes_owner_created"
        if idx not in {i["name"] for i in insp2.get_indexes(TABLE)}:
            op.create_index(op.f(idx), TABLE, ["owner_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE in _existing_tables(insp):
        op.drop_table(TABLE)
