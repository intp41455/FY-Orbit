"""Alembic migration 0010: retire in-DB audit anchors (工单 R39).

The ``audit_anchors`` table is retained for historical rows but is no longer
written or read by the application. Audit anchors now live in an independent
retention store outside the primary database
(``find_yourself.services.anchor_store``), as required by FROZEN_CONTRACT §3.1
("独立锚点") and §9 ("独立锚点必须写入与主库不同的保留位置").

Why: ``AuditService.anchor()`` used to insert into this table and
``AuditService.verify()`` used to read the anchor back out of it. Both sides of
the comparison therefore lived under a single write grant, so an attacker with
full primary-DB write access could rewrite the chain *and* its anchor and still
pass verification — the anchor contributed no tamper evidence at all.

upgrade():
- Adds ``audit_anchors.migrated_to_external`` (Boolean, NOT NULL, default false)
  and inserts one marker row recording the retirement.

Idempotency: the column add is guarded by ``sa.inspect(bind)`` and the marker
row is keyed on a fixed ``evidence`` marker, following the 0005-0009 idiom. A
missing table is skipped rather than raising. downgrade() drops the column only
if present (equally idempotent); it deliberately does **not** delete the marker
row, because once the column is gone nothing would identify it any more and
silent data loss is worse than a leftover row in a retired table.

Revision ID: 0010_audit_anchor_external
Revises: 0009_session_state
"""

from collections.abc import Sequence
from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision: str = "0010_audit_anchor_external"
down_revision: str | None = "0009_session_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "audit_anchors"
COLUMN = "migrated_to_external"
MARKER_ID = "r39-audit-anchor-retired"
#: Key used inside the marker row's ``evidence`` JSON to identify it. Mirrors
#: ``anchor_store.RETIREMENT_EVIDENCE_KEY`` so both sides share one vocabulary.
MARKER_EVIDENCE_KEY = "r39_retirement"

_EVIDENCE = {
    MARKER_EVIDENCE_KEY: True,
    "note": (
        "Table retired by migration 0010 (R39). Anchors are no longer written to or "
        "read from the primary database; the AuditService write path into this table "
        "has been removed."
    ),
    "reason": (
        "anchor() wrote here and verify() read the anchor back from the same primary "
        "DB, so a single write grant could rewrite both the chain and its anchor and "
        "verification still passed - the anchor provided no tamper evidence."
    ),
    "anchor_location": "external store outside primary DB (see find_yourself.services.anchor_store)",
    "new_threat_model": (
        "Rewriting both the chain and its anchor now requires two separate write "
        "permissions (primary DB + anchor store). This raises the bar; it is not "
        "absolute tamper-proofing."
    ),
    "caveat": (
        "With FileAnchorStore co-located on the same host, a principal holding "
        "host-level write access (root, or the DB service account) can still rewrite "
        "both. Production deployments needing a real second trust boundary must use "
        "WORM/object-lock storage, a separate retention account, or an independent "
        "instance (S3AnchorStore)."
    ),
    "contains_private_text": False,
    "head_hash_note": "64 zeros = empty-chain sentinel, not a real observed chain head",
}


#: Lightweight Core table used for the marker row. Going through Core (rather
#: than raw ``sa.text``) lets the JSON column type handle serialisation on every
#: dialect — raw text binding cannot bind a dict parameter.
_anchors = sa.table(
    TABLE,
    sa.column("id", sa.String(64)),
    sa.column("seq", sa.Integer),
    sa.column("storage", sa.String(120)),
    sa.column("head_hash", sa.String(64)),
    sa.column("evidence", sa.JSON),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column(COLUMN, sa.Boolean),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table() -> bool:
    return TABLE in set(_inspector().get_table_names())


def _has_column() -> bool:
    return COLUMN in {c["name"] for c in _inspector().get_columns(TABLE)}


def _marker_exists(conn) -> bool:
    """True if the retirement marker row is already present."""
    rows = conn.execute(
        sa.select(_anchors.c.id, _anchors.c.evidence).where(_anchors.c.id == MARKER_ID)
    ).fetchall()
    for _id, evidence in rows:
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except ValueError:
                continue
        if isinstance(evidence, dict) and evidence.get(MARKER_EVIDENCE_KEY):
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    # Fresh databases may not have the table yet (Base.metadata.create_all is
    # never used in production). Skip rather than fail the whole chain.
    if not _has_table():
        return

    if not _has_column():
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if not _marker_exists(bind):
        bind.execute(
            _anchors.insert().values(
                id=MARKER_ID,
                seq=0,
                storage="external:find_yourself.services.anchor_store",
                head_hash="0" * 64,
                evidence=dict(_EVIDENCE),
                created_at=datetime.now(timezone.utc),
                **{COLUMN: True},
            )
        )


def downgrade() -> None:
    if not _has_table():
        return
    # The marker row is intentionally left in place: without this column there is
    # no way to identify it, and the table is retired anyway.
    if _has_column():
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column(COLUMN)
