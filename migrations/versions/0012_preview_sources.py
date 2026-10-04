"""Alembic migration 0012: unified preview source registry (P1-A).

Introduces ``preview_sources`` — the P1-A 实时预览窗 registry that binds a
workspace to previewable sources:

* ``static`` sources point at a workspace-relative html/md file; content is
  served read-only behind ``CSP: sandbox`` (absolute paths are never stored).
* ``process`` sources reference an existing isolated preview session
  (``preview_sessions``) and only record its loopback access address.

Idempotency: table creation is guarded by ``sa.inspect(bind)``, following the
0005-0011 idiom.

Revision ID: 0012_preview_sources
Revises: 0011_users_and_consents
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012_preview_sources"
down_revision: str | None = "0011_users_and_consents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("preview_sources"):
        op.create_table(
            "preview_sources",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspace_manifests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="static"),
            sa.Column("rel_path", sa.String(length=1000), nullable=False, server_default=""),
            sa.Column("media_type", sa.String(length=100), nullable=False, server_default="text/html"),
            sa.Column("preview_session_id", sa.String(length=64), nullable=True),
            sa.Column("version", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("kind IN ('static', 'process')", name="psrc_kind"),
            sa.CheckConstraint("state IN ('active', 'offline')", name="psrc_state"),
        )
        op.create_index("ix_preview_sources_workspace", "preview_sources", ["workspace_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("preview_sources"):
        op.drop_table("preview_sources")
