"""Alembic migration 0036: Feature flags table (批次 F / §10.5).

Creates `feature_flags(name PK, enabled bool, updated_at, updated_by)`.
Supports short-TTL in-memory cache and fail-closed dynamic feature toggles.

Revision ID: 0036_feature_flags
Revises: 0035_cabin_life_build_state
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0036_feature_flags"
down_revision: str | None = "0035_cabin_life_build_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "feature_flags"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("name", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("'1970-01-01 00:00:00'")),
        sa.Column("updated_by", sa.String(length=128), nullable=False, server_default=sa.text("'system'")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(TABLE):
        return

    op.drop_table(TABLE)
