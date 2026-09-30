"""Alembic migration 0003: Add raw_speaker to source_segments.

Revision ID: 0003_add_raw_speaker
Revises: 0002_profiles_and_canvas
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_add_raw_speaker"
down_revision: str | None = "0002_profiles_and_canvas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    if "source_segments" in tables:
        columns = [c["name"] for c in insp.get_columns("source_segments")]
        if "raw_speaker" not in columns:
            op.add_column(
                "source_segments",
                sa.Column("raw_speaker", sa.String(length=100), nullable=True),
            )
            # Conservative backfill:
            # Only backfill where raw_speaker is NULL and speaker has NOT been confirmed/overridden
            # (i.e. speaker is not 'self' and does not start with 'third_party_candidate:').
            # Rows already confirmed as 'self' or 'third_party_candidate:...' cannot be safely
            # proven back to original raw label, so they remain NULL to prevent overwriting user decisions.
            op.execute(
                """
                UPDATE source_segments
                SET raw_speaker = speaker
                WHERE raw_speaker IS NULL
                  AND speaker IS NOT NULL
                  AND speaker != 'self'
                  AND speaker NOT LIKE 'third_party_candidate:%'
                """
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()
    if "source_segments" in tables:
        columns = [c["name"] for c in insp.get_columns("source_segments")]
        if "raw_speaker" in columns:
            op.drop_column("source_segments", "raw_speaker")
