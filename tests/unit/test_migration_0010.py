"""Test Alembic migration 0010: retiring in-DB audit anchors (R39).

Verifies:
1. upgrade() adds ``migrated_to_external`` (Boolean, NOT NULL, default false)
   and inserts the retirement marker row.
2. Repeated upgrade() is idempotent: no duplicate column, no duplicate marker.
3. upgrade() on a database without ``audit_anchors`` is a no-op, not an error.
4. downgrade() drops the column and keeps the marker row (dropping it would be
   silent data loss with no way to identify it afterwards).
"""

import importlib
from datetime import datetime, timezone
from pathlib import Path
import sys

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa

_proj_root = str(Path(__file__).resolve().parents[2])
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

m0010 = importlib.import_module("migrations.versions.0010_audit_anchor_external")

TABLE = m0010.TABLE
COLUMN = m0010.COLUMN

LEGACY_DDL = """
CREATE TABLE audit_anchors (
    id VARCHAR(64) PRIMARY KEY,
    seq INTEGER NOT NULL,
    storage VARCHAR(120) NOT NULL,
    head_hash VARCHAR(64) NOT NULL,
    evidence JSON NOT NULL,
    created_at DATETIME NOT NULL
)
"""


def _bind(conn):
    ctx = MigrationContext.configure(conn)
    ops = Operations(ctx)
    m0010.op = ops
    return ops


def _anchors_view():
    """Core view of the table *including* the column migration 0010 adds."""
    return sa.table(
        TABLE,
        sa.column("id", sa.String(64)),
        sa.column("seq", sa.Integer),
        sa.column("storage", sa.String(120)),
        sa.column("head_hash", sa.String(64)),
        sa.column("evidence", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column(COLUMN, sa.Boolean),
    )


def _make_conn(with_table: bool = True):
    engine = sa.create_engine("sqlite:///:memory:")
    conn = engine.connect()
    if with_table:
        conn.execute(sa.text(LEGACY_DDL))
    _bind(conn)
    return engine, conn


def test_down_revision_matches_0009_actual_revision():
    """Guard against guessing the parent from the filename."""
    m0009 = importlib.import_module("migrations.versions.0009_session_state")
    assert m0009.revision == "0009_session_state"
    assert m0010.down_revision == m0009.revision


def test_upgrade_adds_column_and_marker_row():
    engine, conn = _make_conn()
    try:
        m0010.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
        assert COLUMN in cols

        view = _anchors_view()
        rows = conn.execute(
            sa.select(view.c.id, view.c.seq, view.c.head_hash, view.c.evidence, view.c[COLUMN])
        ).fetchall()
        assert len(rows) == 1
        marker_id, seq, head_hash, evidence, flag = rows[0]
        assert marker_id == m0010.MARKER_ID
        assert seq == 0
        assert head_hash == "0" * 64
        assert flag in (1, True)
        # Evidence must state the retirement, not a vague promise.
        assert isinstance(evidence, dict)
        assert evidence[m0010.MARKER_EVIDENCE_KEY] is True
        assert "no longer written" in evidence["note"]
        assert "not absolute tamper-proofing" in evidence["new_threat_model"]
        assert "WORM" in evidence["caveat"]
    finally:
        conn.close()
        engine.dispose()


def test_upgrade_is_idempotent():
    engine, conn = _make_conn()
    try:
        m0010.upgrade()
        m0010.upgrade()
        m0010.upgrade()
        rows = conn.execute(sa.text(f"SELECT id FROM {TABLE}")).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()
        engine.dispose()


def test_upgrade_skips_when_table_absent():
    """A fresh DB may not have the table yet; that must not break the chain."""
    engine, conn = _make_conn(with_table=False)
    try:
        m0010.upgrade()  # must not raise
        m0010.downgrade()  # must not raise either
    finally:
        conn.close()
        engine.dispose()


def test_downgrade_drops_column_and_keeps_marker():
    engine, conn = _make_conn()
    try:
        m0010.upgrade()
        m0010.downgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns(TABLE)}
        assert COLUMN not in cols
        rows = conn.execute(sa.text(f"SELECT id FROM {TABLE}")).fetchall()
        assert len(rows) == 1, "marker row must survive downgrade (no way to identify it otherwise)"
    finally:
        conn.close()
        engine.dispose()


def test_new_column_defaults_false_for_legacy_rows():
    """Existing rows must come back as False, not NULL (NOT NULL column)."""
    engine, conn = _make_conn()
    try:
        # Insert via Core so the JSON column type serialises the dict; raw
        # sa.text() cannot bind a dict parameter on SQLite.
        legacy = sa.table(
            TABLE,
            sa.column("id", sa.String(64)),
            sa.column("seq", sa.Integer),
            sa.column("storage", sa.String(120)),
            sa.column("head_hash", sa.String(64)),
            sa.column("evidence", sa.JSON),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        conn.execute(
            legacy.insert().values(
                id="legacy-1", seq=7, storage="local-backup",
                head_hash="a" * 64, evidence={},
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        m0010.upgrade()
        row = conn.execute(sa.text(f"SELECT {COLUMN} FROM {TABLE} WHERE id='legacy-1'")).one()
        assert row[0] in (0, False)
    finally:
        conn.close()
        engine.dispose()
