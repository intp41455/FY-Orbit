"""Alembic migration 0007: prompt template library (行动项 #13 / 工单 P1-06).

Creates three tables:

- ``prompt_templates``   — unique ``name``, ``latest_version`` is the effective
  version pointer (rollback = pointer switch), ``scope`` CHECK-constrained to
  platform/workbench/game_tree, optimistic-lock ``version``.
- ``prompt_versions``    — append-only content snapshots, unique
  (prompt_id, version), ``content_hash`` SHA-256 of the exact content.
- ``prompt_render_logs`` — render audit trail: template name, version,
  ``variables_hash`` (sha256 of canonical variable JSON) and ``task_id`` —
  never the plaintext variables (privacy alignment R03).

No existing table is touched: the proposal CHECK widening for the new
``prompt.*`` operations lives in ``db/models.py`` ORM metadata (the source of
truth for the SQLite/dev path); production PostgreSQL widening of
``ck_prop_operation`` is a deliberate follow-up so this migration stays
scoped to new tables only.

Idempotency: every CREATE is guarded by ``sa.inspect(bind)`` — existing tables
are skipped — following the 0005/0006 idiom. Downgrade drops the tables only
if present (equally idempotent).

Revision ID: 0007_prompt_templates
Revises: 0006_grant_egress
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_prompt_templates"
down_revision: str | None = "0006_grant_egress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("prompt_templates", "prompt_versions", "prompt_render_logs")


def _existing_tables(insp: sa.Inspector) -> set[str]:
    return set(insp.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    present = _existing_tables(insp)

    if "prompt_templates" not in present:
        op.create_table(
            "prompt_templates",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("latest_version", sa.Integer(), nullable=False),
            sa.Column("variables_schema", sa.JSON(), nullable=False),
            sa.Column("owner", sa.String(length=200), nullable=False),
            sa.Column("scope", sa.String(length=20), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "scope IN ('platform','workbench','game_tree')",
                name="ck_prompt_tpl_scope",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_prompt_templates"),
        )
        op.create_index(
            "ux_prompt_templates_name", "prompt_templates", ["name"], unique=True
        )

    if "prompt_versions" not in present:
        op.create_table(
            "prompt_versions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column(
                "prompt_id",
                sa.String(length=64),
                sa.ForeignKey("prompt_templates.id", name="fk_prompt_versions_prompt_id_prompt_templates"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("variables_schema", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_prompt_versions"),
            sa.UniqueConstraint("prompt_id", "version", name="uq_prompt_version_no"),
        )

    if "prompt_render_logs" not in present:
        op.create_table(
            "prompt_render_logs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("template_name", sa.String(length=200), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("variables_hash", sa.String(length=64), nullable=False),
            sa.Column("scope", sa.String(length=20), nullable=True),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_prompt_render_logs"),
        )
        op.create_index(
            "ix_prompt_render_logs_template_name", "prompt_render_logs", ["template_name"]
        )
        op.create_index(
            "ix_prompt_render_logs_task_id", "prompt_render_logs", ["task_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    present = _existing_tables(insp)

    # Refuse to silently erase an audit trail that has real entries.
    if "prompt_render_logs" in present:
        leaked = bind.execute(
            sa.text("SELECT count(*) FROM prompt_render_logs")
        ).scalar()
        if leaked:
            raise RuntimeError(
                f"refusing to downgrade: {leaked} prompt render log(s) would be "
                "silently erased. Archive the audit trail first."
            )

    if "prompt_render_logs" in present:
        op.drop_index("ix_prompt_render_logs_task_id", table_name="prompt_render_logs")
        op.drop_index("ix_prompt_render_logs_template_name", table_name="prompt_render_logs")
        op.drop_table("prompt_render_logs")

    if "prompt_versions" in present:
        op.drop_table("prompt_versions")

    if "prompt_templates" in present:
        op.drop_index("ux_prompt_templates_name", table_name="prompt_templates")
        op.drop_table("prompt_templates")
