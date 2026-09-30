"""Test Alembic migration 0003: adding raw_speaker column to source_segments.

Verifies:
1. Migration cleanly adds nullable raw_speaker column if missing.
2. Conservative backfill rule:
   - unconfirmed speakers (e.g. 'Alice', 'Speaker 1') are backfilled into raw_speaker.
   - confirmed speakers ('self', 'third_party_candidate:...') remain NULL to prevent overwriting user decisions.
3. Multiple upgrades are idempotent and safe.
4. Downgrade cleanly removes the raw_speaker column.
"""

import importlib
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa
import pytest

m0003 = importlib.import_module("migrations.versions.0003_add_raw_speaker_to_source_segments")
upgrade = m0003.upgrade
downgrade = m0003.downgrade


def test_migration_0003_upgrade_downgrade_lifecycle():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        # 1. Create a legacy source_segments table matching 0002 without raw_speaker
        conn.execute(
            sa.text(
                """
                CREATE TABLE source_segments (
                    id VARCHAR(36) PRIMARY KEY,
                    import_id VARCHAR(36) NOT NULL,
                    segment_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    speaker VARCHAR(100),
                    char_count INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        # 2. Insert synthetic legacy records
        conn.execute(
            sa.text(
                """
                INSERT INTO source_segments (id, import_id, segment_index, text, speaker, char_count, created_at)
                VALUES
                    ('seg-1', 'imp-1', 0, 'Hello world', 'Speaker_A', 11, '2026-10-01 00:00:00'),
                    ('seg-2', 'imp-1', 1, 'My decision', 'self', 11, '2026-10-01 00:00:01'),
                    ('seg-3', 'imp-1', 2, 'Colleague advice', 'third_party_candidate:Bob', 16, '2026-10-01 00:00:02')
                """
            )
        )
        conn.commit()

        # Set up Alembic migration context
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)

        # Monkeypatch op in migration module
        orig_op = m0003.op
        m0003.op = op
        try:
            # 3. First upgrade
            upgrade()
            conn.commit()

            # Verify schema
            insp = sa.inspect(conn)
            cols = {c["name"]: c for c in insp.get_columns("source_segments")}
            assert "raw_speaker" in cols
            assert cols["raw_speaker"]["nullable"] is True

            # Verify conservative backfill
            rows = conn.execute(
                sa.text("SELECT id, speaker, raw_speaker FROM source_segments ORDER BY segment_index")
            ).mappings().all()

            assert rows[0]["id"] == "seg-1"
            assert rows[0]["speaker"] == "Speaker_A"
            assert rows[0]["raw_speaker"] == "Speaker_A"  # Unconfirmed speaker backfilled

            assert rows[1]["id"] == "seg-2"
            assert rows[1]["speaker"] == "self"
            assert rows[1]["raw_speaker"] is None  # Confirmed self preserved as NULL

            assert rows[2]["id"] == "seg-3"
            assert rows[2]["speaker"] == "third_party_candidate:Bob"
            assert rows[2]["raw_speaker"] is None  # Confirmed candidate preserved as NULL

            # 4. Second upgrade (idempotency check)
            upgrade()
            conn.commit()

            # 5. Downgrade check
            downgrade()
            conn.commit()

            insp_after_down = sa.inspect(conn)
            cols_after_down = {c["name"]: c for c in insp_after_down.get_columns("source_segments")}
            assert "raw_speaker" not in cols_after_down

            # Verify existing data intact after downgrade
            rows_after_down = conn.execute(
                sa.text("SELECT id, speaker FROM source_segments ORDER BY segment_index")
            ).mappings().all()
            assert len(rows_after_down) == 3
            assert rows_after_down[0]["speaker"] == "Speaker_A"

        finally:
            m0003.op = orig_op
