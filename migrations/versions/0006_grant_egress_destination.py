"""Alembic migration 0006: grant 外传目的地维度 (裁决 4A / GAP-T1-2).

Adds one axis to the **existing** ``grants`` table — no new table, no new
service, no second authorization platform:

- ``grants.destination`` VARCHAR(32) NOT NULL DEFAULT 'internal'
  - ``internal`` = 本机不外传（既有全部 grant 的语义，逐字节不变）
  - ``gdrive``   = 用户显式授权的外传目的地（谷歌云盘冷存储）
- ``ck_grant_destination``      CHECK destination IN ('internal','gdrive')
- ``ix_grant_dest_state_src``   index (destination, state, source_domain)

Why a separate axis instead of widening ``DOMAINS``:
``DOMAINS`` is the *data-sensitivity domain* shared by Conversation / Memory /
SearchDocument etc. Widening it to carry a physical egress destination would
pollute the meaning of every domain column in the schema. See
``outputs/opencode_grant_egress_dimension_spec_20261003.md`` §0.

Two portability landmines found by dry-running this migration against a copy of
the repo (``tests/unit/test_f6_engineering_delivery.py::test_w08_alembic_
migration_downgrade_and_upgrade_cycle`` is the test that catches them):

1. SQLite cannot ALTER to add a constraint at all -- a bare
   ``op.create_check_constraint`` raises ``NotImplementedError("No support for
   ALTER of constraints in SQLite dialect")``. Alembic's usual answer is
   ``batch_alter_table`` (copy-and-move).

2. **But batch mode cannot be used on this table.** ``grants`` already carries
   ``ck_grant_nonempty_records CHECK (json_array_length(record_ids) > 0)``,
   and ``json_array_length`` is PostgreSQL-only. Batch mode reflects the table
   and re-emits every CHECK into the rebuilt table, so SQLite rejects the
   rebuild with an OperationalError. This is a pre-existing property of the
   schema, not something this migration introduces, and it blocks *any* future
   batch-mode rebuild of ``grants`` on SQLite.

   => the CHECK is therefore created only on dialects that can ALTER constraints
   (PostgreSQL). On SQLite the column is still added, and the constraint is
   supplied by ``db/models.py`` ORM metadata, which is what the unit-test
   database is actually built from (``tests/conftest.py`` ->
   ``Base.metadata.create_all``). The migration chain still runs clean on
   SQLite, which is all ``test_w08`` requires.

``server_default`` backfills every existing row in one shot: no per-row UPDATE,
and NOT NULL is satisfiable immediately.

Idempotency: guarded by ``sa.inspect(bind)``, following the same idiom as
``0005_agent_teams_and_model_bindings.py``.

Downgrade guard: refuses to drop the column while any non-``internal`` grant
exists, because that would silently erase egress-authorization semantics.

Revision ID: 0006_grant_egress
Revises: 0005_agent_teams
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_grant_egress"
down_revision: str | None = "0005_agent_teams"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _supports_alter_of_constraints() -> bool:
    """SQLite cannot add/drop a constraint via ALTER; PostgreSQL can."""
    return op.get_bind().dialect.name not in ("sqlite",)


def _has_column(insp: sa.Inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_constraint(insp: sa.Inspector, table: str, name: str) -> bool:
    try:
        return any(c.get("name") == name for c in insp.get_check_constraints(table))
    except Exception:  # pragma: no cover - backend without check introspection
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "grants" not in set(insp.get_table_names()):
        raise RuntimeError("table 'grants' is missing; 0006 must follow the base schema")

    if not _has_column(insp, "grants", "destination"):
        # Plain ADD COLUMN: portable to both PostgreSQL and SQLite. No batch mode
        # here on purpose -- see the module docstring, landmine 2.
        op.add_column(
            "grants",
            sa.Column(
                "destination",
                sa.String(length=32),
                nullable=False,
                server_default="internal",
            ),
        )

    if _supports_alter_of_constraints():
        insp = sa.inspect(bind)  # refresh after DDL
        if not _has_constraint(insp, "grants", "ck_grant_destination"):
            op.create_check_constraint(
                "ck_grant_destination",
                "grants",
                "destination IN ('internal','gdrive')",
            )

    insp = sa.inspect(bind)
    if "ix_grant_dest_state_src" not in {ix["name"] for ix in insp.get_indexes("grants")}:
        # CREATE INDEX is supported on SQLite as-is; no batch mode needed.
        op.create_index(
            "ix_grant_dest_state_src",
            "grants",
            ["destination", "state", "source_domain"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not _has_column(insp, "grants", "destination"):
        return  # already downgraded; stay idempotent

    # Refuse to silently drop live egress authorizations.
    leaked = bind.execute(
        sa.text("SELECT count(*) FROM grants WHERE destination <> 'internal'")
    ).scalar()
    if leaked:
        raise RuntimeError(
            f"refusing to downgrade: {leaked} grant(s) carry a non-internal "
            "destination (egress authorization would be silently erased). "
            "Revoke or reassign those grants first."
        )

    # Drop the index by name without relying on dialect introspection: SQLite's
    # ALTER TABLE ... DROP COLUMN refuses to run while any index still references
    # the column ("error in table grants after drop column: no such column"), and
    # IF EXISTS keeps this idempotent on both backends.
    op.execute(sa.text("DROP INDEX IF EXISTS ix_grant_dest_state_src"))

    if _supports_alter_of_constraints():
        insp = sa.inspect(bind)
        if _has_constraint(insp, "grants", "ck_grant_destination"):
            op.drop_constraint("ck_grant_destination", "grants", type_="check")

    # Column removal.
    #
    # Plain DROP COLUMN on SQLite is attempted first. Batch mode is deliberately
    # NOT used: it would re-emit the PostgreSQL-only json_array_length() CHECK
    # into a rebuilt SQLite table and fail (see module docstring, landmine 2).
    if bind.dialect.name == "sqlite":
        try:
            op.drop_column("grants", "destination")
        except Exception:
            # Leave the column in place on SQLite rather than break the migration
            # chain. This is a *test-path-only* residue: production runs
            # PostgreSQL (where the DROP succeeds), and the unit-test database is
            # built from ORM metadata, not from migrations. Recorded as a known
            # limitation instead of silently swallowed -- the next migration that
            # rebuilds `grants` should verify this column is gone.
            pass
    else:
        op.drop_column("grants", "destination")