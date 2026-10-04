"""Alembic migration 0009: session-state snapshots table (工单 P1-21).

Creates one table:

- ``session_state_snapshots`` — restorable session-state snapshots keyed by
  ``session_key`` (unique; upsert semantics). Payload is the JSON snapshot of
  a session's memory window + tool registry state + DSL run archive, saved
  via ``POST /api/session-state/snapshot`` and replayed via
  ``GET /api/session-state/restore`` after a backend restart.

No existing table is touched. Idempotency: the CREATE is guarded by
``sa.inspect(bind)`` — an existing table is skipped — following the
0005–0008 idiom. Downgrade drops the table only if present (equally
idempotent).

Revision ID: 0009_session_state
Revises: 0008_work_stash
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_session_state"
down_revision: str | None = "0008_work_stash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "session_state_snapshots"


def _existing_tables(insp: sa.Inspector) -> set[str]:
    return set(insp.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE not in _existing_tables(insp):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("session_key", sa.String(length=200), nullable=False),
            sa.Column("conversation_id", sa.String(length=64), nullable=True),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_session_state_snapshots")),
            sa.UniqueConstraint("session_key", name="uq_session_state_snapshots_session_key"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if TABLE in _existing_tables(insp):
        op.drop_table(TABLE)
