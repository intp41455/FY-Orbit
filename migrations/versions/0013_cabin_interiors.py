"""Alembic migration 0013: cabin interior layouts (W1 数码小屋室内布置).

Introduces ``cabin_interiors`` — the per-owner, per-house-template furniture
arrangement backing the indoor scene:

* ``owner_id`` + ``house_id`` are unique, so switching the house template
  switches to that template's own saved arrangement instead of overwriting it.
* ``layout`` holds the validated furniture payload (16px grid cells, catalog
  whitelisted by ``services/cabin_interior.py``).
* ``version`` is the optimistic lock consumed by
  ``PUT /api/cabin/interiors/{house_id}``.

Idempotency: table creation is guarded by ``sa.inspect(bind)``, following the
0005-0012 idiom.

Revision ID: 0013_cabin_interiors
Revises: 0012_preview_sources
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013_cabin_interiors"
down_revision: str | None = "0012_preview_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("cabin_interiors"):
        op.create_table(
            "cabin_interiors",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("house_id", sa.String(length=32), nullable=False),
            sa.Column("layout", sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id", "house_id", name="uq_cabin_interior_owner_house"),
            sa.CheckConstraint("version >= 1", name="cabin_interior_version_positive"),
            sa.CheckConstraint("length(house_id) > 0", name="cabin_interior_house_nonempty"),
        )
        op.create_index("ix_cabin_interiors_owner", "cabin_interiors", ["owner_id"])
        op.create_index("ix_cabin_interiors_house", "cabin_interiors", ["house_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("cabin_interiors"):
        op.drop_table("cabin_interiors")
