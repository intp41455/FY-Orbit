"""Alembic migration 0021: W8 account tiers (guest status + membership plan).

Two additive changes to ``users``, both needed by the account-layer split
(游客 / 注册 / 会员):

* ``status`` gains the value ``guest``. A guest is a **real local user row**
  (``email = guest-<uuid>@local``, status ``guest``) that owns data exactly like
  any other tenant — that is what lets a guest later "upgrade in place" and keep
  everything it produced. The DB ``CHECK`` on ``status`` is rebuilt to include it.
* ``plan`` (``free`` / ``pro``) is the **membership slot**. v1 wires **no payment
  channel**: every row is created with ``free`` and nothing in the product flow
  upgrades it. The column exists so the tier is representable end to end, and the
  API/UI state this honestly (「付费通道未开通」) rather than faking a purchase.

Portability (the landmine documented in 0006's docstring, hit again here):

* SQLite cannot add or drop a constraint through plain ``ALTER`` —
  ``op.create_check_constraint`` raises
  ``NotImplementedError("No support for ALTER of constraints in SQLite dialect")``.
  0006 solved this by *skipping* the CHECK on SQLite and leaning on ORM metadata
  (which builds the unit-test DB via ``create_all``). That shortcut is **not good
  enough here**: a real already-migrated SQLite desktop database would keep the
  old ``status IN ('active','deleted')`` CHECK and every guest insert would be
  rejected at the database layer. The guest tier must be enforced for real on
  both engines, so this migration uses ``batch_alter_table`` (copy-and-move).
* Batch mode is safe on ``users`` specifically: its only CHECK constraints are
  plain string literals. That is exactly what blocks batch mode on ``grants``
  (``ck_grant_nonempty_records`` uses PostgreSQL-only ``json_array_length``) —
  see 0006 landmine 2.
* Constraint surgery uses ``batch_alter_table(..., copy_from=<explicit Table>)``
  rather than reflected batch mode. Reflected batch mode re-applies a naming
  convention to the names it is handed — its built-in default for CHECK is
  ``ck_%(table_name)s_%(constraint_name)s``, the same shape as this repo's
  ``db/base.py`` convention — and ``naming_convention={}`` cannot suppress it
  because an empty dict is falsy at alembic/operations/batch.py:148. Passing a
  complete ``copy_from`` table skips reflection altogether (``reflected=False``),
  so the CHECK names below are used verbatim on the way in and matched verbatim
  on the way out. Those names are convention-expanded (``ck_users_*``) so a
  migrated database agrees with one built by ``create_all`` from ORM metadata.

``server_default`` backfills every existing row in one shot: no per-row UPDATE,
and NOT NULL is satisfiable immediately.

Idempotency: guarded by ``sa.inspect(bind)``, following the 0005-0020 idiom, so
re-running against an already-migrated database is a no-op.

Revision ID: 0021_guest_account_tiers
Revises: 0020_avatar_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0021_guest_account_tiers"
down_revision: str | None = "0020_avatar_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_WITH_GUEST = "status IN ('active', 'deleted', 'guest')"
STATUS_BASE = "status IN ('active', 'deleted')"
PLAN_VALUES = "plan IN ('free', 'pro')"
#: Convention-expanded names (``ck_%(table_name)s_%(constraint_name)s``), i.e.
#: what ``db/models.py`` produces via ``create_all``.
STATUS_CHECK = "ck_users_ck_user_status"
PLAN_CHECK = "ck_users_ck_user_plan"


def _has_column(insp: sa.Inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def _check_named(bind, column: str) -> str | None:
    """Return the name of the live CHECK on ``users`` guarding ``column``.

    Identity is resolved by inspecting the CHECK *text* rather than trusting a
    fixed name, because a database created by ``create_all`` (ORM metadata,
    convention applied) and one created by migration 0011 (literal name) carry
    different names for the same constraint.
    """
    insp = sa.inspect(bind)
    for c in insp.get_check_constraints("users"):
        text = (c.get("sqltext") or "").replace(" ", "").lower()
        if text.startswith(f"{column}in(") and c.get("name"):
            return str(c["name"])
    return None


def _accepts(bind, column: str, value: str) -> bool:
    """True when the live CHECK already permits ``value`` for ``column``."""
    insp = sa.inspect(bind)
    for c in insp.get_check_constraints("users"):
        text = (c.get("sqltext") or "").replace(" ", "").lower()
        if text.startswith(f"{column}in("):
            return f"'{value}'" in text
    return False


def _users_table(*, with_plan: bool, status_values: str) -> sa.Table:
    """Declare the post-migration shape of ``users`` for ``copy_from``.

    Mirrors ``db/models.py::User`` exactly (same column types, lengths and
    nullability) so a copy-and-move rebuild cannot silently reshape the table.
    ``status_values``/``plan`` presence is the only difference between the
    upgrade and downgrade targets.
    """
    md = sa.MetaData()
    cols = [
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]
    if with_plan:
        cols.append(
            sa.Column("plan", sa.String(length=16), nullable=False, server_default="free")
        )
    checks = [sa.CheckConstraint(status_values, name=STATUS_CHECK)]
    if with_plan:
        checks.append(sa.CheckConstraint(PLAN_VALUES, name=PLAN_CHECK))
    return sa.Table("users", md, *cols, *checks)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("users"):
        # Built by create_all from ORM metadata (unit tests) — already correct.
        return

    has_plan = _has_column(insp, "users", "plan")
    if not has_plan:
        # Plain ADD COLUMN: portable to both engines, no batch mode needed.
        op.add_column(
            "users",
            sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
        )

    status_ok = _accepts(bind, "status", "guest")
    plan_ok = _check_named(bind, "plan") is not None
    if status_ok and plan_ok:
        return

    # copy_from => reflected=False, so the CHECK names are verbatim in and out.
    with op.batch_alter_table(
        "users",
        copy_from=_users_table(with_plan=True, status_values=STATUS_WITH_GUEST),
        recreate="always",
    ):
        pass


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("users"):
        return

    # Guests must be promoted (or their data removed) before the value can go.
    op.execute("UPDATE users SET status = 'active' WHERE status = 'guest'")

    with op.batch_alter_table(
        "users",
        copy_from=_users_table(with_plan=False, status_values=STATUS_BASE),
        recreate="always",
    ):
        pass
