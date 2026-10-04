"""Alembic migration 0026: 需求10 — 短期/中期/长期三层记忆.

Adds the retention-lifecycle dimension to ``memories`` and the evidence table
that makes the short->medium promotion gate enforceable.

Why columns on ``memories`` rather than a new tier table
-------------------------------------------------------
The tier is a property of an existing memory record, not a separate entity: it
has the same owner, domain, authorization predicate, revision history, derived-
source graph and deletion closure that ``memories`` already has. A separate
``memory_tiers`` table would have had to duplicate the tenant key and the
soft-delete state, and every read would need a join — while the two rows could
drift apart (a tier pointing at a deleted memory, or vice versa). Extending the
existing table keeps one row = one memory, which is also what the deletion and
export closures already assume.

The tier was deliberately NOT folded into the existing ``category`` column.
``category`` says *what kind of fact* a record is (``preference``,
``tool_fact``, ``hypothesis``); the tier says *how long it must survive*. Those
are independent axes — a record is routinely both a ``preference`` and
``short``-lived — and overloading one column with both would make
``category='preference'`` mean different things depending on the tier, breaking
every existing category-based query. (``category`` having no CHECK whitelist
made overloading *possible*; it did not make it *correct*.)

What is added
-------------
* ``tier`` — ``short`` | ``medium`` | ``long``, CHECK-constrained, backfilled to
  ``medium``. ``medium`` is the read-equivalent legacy behaviour: cross-session,
  no hard deadline, no automatic expiry, so no existing row changes what a
  caller sees.
* ``session_id`` / ``tier_expires_at`` — the session scope and eviction deadline
  for the short tier.
* ``session_count`` / ``reinforcement_count`` / ``last_used_at`` /
  ``tier_changed_at`` — the evidence the promotion gates read and the clock the
  medium decay rule reads.
* ``memory_tier_sessions`` — which sessions actually touched a record. A count
  alone cannot express *which* sessions, and without that the "carried across
  sessions" gate would be trusting a caller to self-report novelty. The unique
  constraint makes a session count once.

Idempotency: guarded by ``sa.inspect(bind)``, following the 0005-0025 idiom.
SQLite cannot ``ALTER`` in a CHECK constraint, so the column+constraint work
goes through ``batch_alter_table`` (copy-and-move), which also backfills
existing rows from ``server_default``.

Revision ID: 0026_memory_tiers
Revises: 0025_hitl_interrupts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_memory_tiers"
down_revision: str | None = "0025_hitl_interrupts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIERS = ("short", "medium", "long")
_TIER_IN = "tier IN ('short', 'medium', 'long')"


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def _has_index(bind, table: str, index: str) -> bool:
    return any(i["name"] == index for i in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("memories"):
        # Base table absent (fresh DB built from 0001 by other means) — nothing
        # to extend. Not an error: matches the 0010 "no-op on absent table" rule.
        return

    if not _has_column(bind, "memories", "tier"):
        # ``medium`` backfills every existing row, which is exactly the
        # pre-tier single-scale behaviour: no deadline, no session scope, no
        # automatic expiry.
        with op.batch_alter_table("memories") as bt:
            bt.add_column(
                sa.Column("tier", sa.String(length=8), nullable=False, server_default="medium")
            )
            bt.add_column(sa.Column("session_id", sa.String(length=64), nullable=True))
            bt.add_column(
                sa.Column("tier_expires_at", sa.DateTime(timezone=True), nullable=True)
            )
            bt.add_column(
                sa.Column("session_count", sa.Integer(), nullable=False, server_default="0")
            )
            bt.add_column(
                sa.Column("reinforcement_count", sa.Integer(), nullable=False, server_default="0")
            )
            bt.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
            bt.add_column(sa.Column("tier_changed_at", sa.DateTime(timezone=True), nullable=True))
            bt.create_check_constraint("ck_mem_tier", _TIER_IN)
            # A short record must carry a deadline, otherwise "evicted when the
            # session ends" is unenforceable and short records are immortal.
            # ``session_id`` is deliberately NOT required: a record demoted from
            # medium has no originating session and inventing one would fabricate
            # provenance.
            bt.create_check_constraint(
                "ck_mem_short_has_deadline", "tier != 'short' OR tier_expires_at IS NOT NULL"
            )
            # The profile tier must never carry a clock, so no sweep can evict it.
            bt.create_check_constraint(
                "ck_mem_long_never_expires", "tier != 'long' OR tier_expires_at IS NULL"
            )
            bt.create_check_constraint(
                "ck_mem_counters_nonneg", "session_count >= 0 AND reinforcement_count >= 0"
            )

    # The index is guarded independently of the columns. On a *fresh* database
    # migration 0001 already ran ``Base.metadata.create_all``, so ``memories``
    # arrives with the tier columns and the block above is correctly skipped —
    # but create_all knows nothing about this index, so guarding it with the
    # columns would leave it permanently missing on every fresh install.
    if not _has_index(bind, "memories", "ix_memories_owner_tier"):
        op.create_index("ix_memories_owner_tier", "memories", ["owner_id", "tier"])

    if not insp.has_table("memory_tier_sessions"):
        op.create_table(
            "memory_tier_sessions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("memory_id", sa.String(length=64), nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            # A session counts once, so one chatty session cannot manufacture
            # cross-session carry-over evidence by repeating itself.
            sa.UniqueConstraint("memory_id", "session_id", name="uq_mem_tier_session"),
            # Named to match the ``Base.metadata`` naming convention
            # (``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``) so a
            # fresh create_all database and a migrated one agree on the name.
            sa.ForeignKeyConstraint(
                ["memory_id"], ["memories.id"],
                name="fk_memory_tier_sessions_memory_id_memories",
            ),
        )
    if not _has_index(bind, "memory_tier_sessions", "ix_memory_tier_sessions_memory_id"):
        op.create_index(
            "ix_memory_tier_sessions_memory_id", "memory_tier_sessions", ["memory_id"]
        )
    if not _has_index(bind, "memory_tier_sessions", "ix_memory_tier_sessions_session_id"):
        op.create_index(
            "ix_memory_tier_sessions_session_id", "memory_tier_sessions", ["session_id"]
        )


def downgrade() -> None:
    """Drop the tier machinery, keeping the memory rows themselves.

    Memories are NOT deleted here. The content, owner, authorization state and
    revision history all predate this migration; removing the retention axis
    must not destroy user data, only the ability to reason about its lifetime.
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("memory_tier_sessions"):
        op.drop_index("ix_memory_tier_sessions_session_id", table_name="memory_tier_sessions")
        op.drop_index("ix_memory_tier_sessions_memory_id", table_name="memory_tier_sessions")
        op.drop_table("memory_tier_sessions")

    if insp.has_table("memories") and _has_column(bind, "memories", "tier"):
        op.drop_index("ix_memories_owner_tier", table_name="memories")
        # The CHECKs must be dropped *inside* the same batch, before the columns.
        # SQLite's copy-and-move strategy reflects the existing constraints onto
        # the temp table; a CHECK referencing an already-dropped column would
        # recreate the temp table with a dangling reference and fail.
        with op.batch_alter_table("memories") as bt:
            for name in (
                "ck_mem_tier",
                "ck_mem_short_has_deadline",
                "ck_mem_long_never_expires",
                "ck_mem_counters_nonneg",
            ):
                bt.drop_constraint(name, type_="check")
            for col in (
                "tier_changed_at",
                "last_used_at",
                "reinforcement_count",
                "session_count",
                "tier_expires_at",
                "session_id",
                "tier",
            ):
                bt.drop_column(col)
