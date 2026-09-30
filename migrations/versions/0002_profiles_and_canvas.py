"""Alembic migration 0002: Profile (04) and Canvas (05) tables.

Revision ID: 0002_profiles_and_canvas
Revises: 0001_initial
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from find_yourself.db.base import Base
from find_yourself.db import models  # noqa: F401

revision: str = "0002_profiles_and_canvas"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Base.metadata.create_all only creates tables that don't already exist.
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    tables = [
        "canvas_events",
        "handoff_packets",
        "dispatch_records",
        "canvas_instances",
        "profile_feedback",
        "profile_revisions",
        "profile_runs",
        "profile_evidence",
        "source_segments",
        "profile_imports",
        "profile_subjects",
    ]
    for table in tables:
        if is_pg:
            op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        else:
            op.execute(f"DROP TABLE IF EXISTS {table}")
