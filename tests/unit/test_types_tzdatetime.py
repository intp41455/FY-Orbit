"""Regression: TZDateTime must return tz-aware UTC on read-back (BUG follow-up).

SQLite has no ``TIMESTAMP WITH TIME ZONE``; without ``process_result_value`` the
driver hands back naive datetimes, and later comparisons against
``datetime.now(timezone.utc)`` raise ``TypeError``. This test writes a record
with an expiry time in one session/transaction, commits, then reads it back in a
brand-new session and asserts the value is aware UTC and comparable.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from find_yourself.db import models  # noqa: F401  (register tables on Base)
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.proposal import ProposalService


def test_expires_at_read_back_is_tz_aware_utc(engine):
    # Session A: write + commit.
    SessionA = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sa = SessionA()
    sa.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
    audit = AuditService(sa)
    proposals = ProposalService(sa, audit)
    owner = Actor.owner("owner-1", csrf_token="")
    p = proposals.create(
        owner, operation="memory.upsert", payload={"x": 1},
        reason="add fact", rollback="undo", expires_in_minutes=60,
    )
    written_id = p.id
    written_expiry = p.expires_at
    sa.commit()
    sa.close()

    # Session B: brand new session, same engine.
    SessionB = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sb = SessionB()
    try:
        row = sb.query(models.Proposal).filter_by(id=written_id).one()
        exp = row.expires_at

        # 1) tzinfo must exist (the whole point of the fix).
        assert exp.tzinfo is not None, f"expires_at read back as naive: {exp!r}"
        # 2) normalised to UTC offset.
        assert exp.utcoffset() == timedelta(0)
        # 3) comparable against a fresh aware-UTC "now" without TypeError.
        now = utcnow()
        assert isinstance(now, datetime) and now.tzinfo is not None
        # Expiry set 60 min ahead of creation, so it should still be in the future.
        assert exp > now
        # And a past aware-UTC instant must compare lower than the stored value.
        assert exp > (now - timedelta(days=1))
        # 4) round-trip value matches what was written (within microsecond jitter).
        assert abs((exp - written_expiry).total_seconds()) < 1.0
    finally:
        sb.close()


def test_naive_input_normalised_on_write_and_aware_on_read(engine):
    SessionA = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sa = SessionA()
    audit = AuditService(sa)
    proposals = ProposalService(sa, audit)
    owner = Actor.owner("owner-1", csrf_token="")
    p = proposals.create(
        owner, operation="memory.upsert", payload={"x": 1},
        reason="add fact", rollback="undo",
    )
    # Simulate a caller passing a naive datetime (defensive).
    naive = datetime(2030, 1, 1, 12, 0, 0)  # no tzinfo
    p.expires_at = naive
    sa.commit()
    pid = p.id
    sa.close()

    SessionB = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sb = SessionB()
    try:
        exp = sb.query(models.Proposal).filter_by(id=pid).one().expires_at
        assert exp.tzinfo is not None
        assert exp.utcoffset() == timedelta(0)
        # Treated as UTC on the way in, read back as UTC.
        assert exp == datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finally:
        sb.close()
