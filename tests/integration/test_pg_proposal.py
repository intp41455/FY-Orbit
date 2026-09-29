"""Real-Postgres integration tests for S07 and S09.

These tests MUST run against a real PostgreSQL (FY_DATABASE_URL pointed at
pgvector/pgvector:pg16). They are skipped under the SQLite unit suite.

- S07: payload unchanged but the target's version has moved on -> approval is
  rejected; approving against the correct version succeeds.
- S09: many threads approve the same proposal concurrently; exactly one wins
  and the rest get a conflict; the outbox produces exactly one operation.
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import sessionmaker

from find_yourself.db import models  # noqa: F401
from find_yourself.db.models import AuditEvent, Memory, Operation, Proposal
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict
from find_yourself.services.proposal import ProposalService

PG_URL = os.environ.get("FY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"),
    reason="real Postgres required (set FY_DATABASE_URL=postgresql+psycopg://...)",
)


@pytest.fixture()
def pg_session():
    eng = create_engine(PG_URL, future=True, pool_size=20, max_overflow=10)
    sm = sessionmaker(bind=eng, future=True)
    s = sm()
    created = {"proposals": [], "operations": [], "memories": [], "audit": []}
    try:
        yield s, sm, created
    finally:
        # best-effort cleanup of only the rows this test created
        try:
            if created["operations"]:
                s.execute(delete(Operation).where(Operation.id.in_(created["operations"])))
            if created["proposals"]:
                s.execute(delete(Proposal).where(Proposal.id.in_(created["proposals"])))
            if created["memories"]:
                s.execute(delete(Memory).where(Memory.id.in_(created["memories"])))
            if created["audit"]:
                s.execute(delete(AuditEvent).where(AuditEvent.id.in_(created["audit"])))
            s.commit()
        except Exception:
            s.rollback()
        s.close()
        eng.dispose()


def _mem_version(s, memory_id):
    row = s.execute(text("SELECT version FROM memories WHERE id=:i"), {"i": memory_id}).scalar_one()
    return row


def test_s07_target_version_mismatch_rejects(pg_session):
    s, sm, created = pg_session
    owner = Actor.owner("owner-s07", csrf_token="")

    # Target memory at version 1.
    m = Memory(
        id="mem-s07-" + os.urandom(4).hex(), owner_id="owner-s07", domain="personal",
        category="fact", content="v1", content_hash="h" + "0" * 63, active=True,
        hypothesis_status="fact", created_at=utcnow(), version=1,
    )
    s.add(m)
    s.flush()
    created["memories"].append(m.id)

    def lookup(mid):
        return _mem_version(s, mid)

    audit = AuditService(s)
    svc = ProposalService(s, audit, target_version_lookup=lookup)

    # 1) Correct version (1) -> approval succeeds.
    p1 = svc.create(
        owner, operation="memory.upsert", payload={"x": 1}, reason="upsert v1",
        rollback="undo", target_id=m.id, expected_version=1,
    )
    created["proposals"].append(p1.id)
    decided1 = svc.decide(owner, p1.id, p1.digest, approve=True)
    assert decided1.status == "executed"

    # 2) Target moves to version 2; payload unchanged but expected_version=1 -> reject.
    s.execute(text("UPDATE memories SET version=2 WHERE id=:i"), {"i": m.id})
    s.flush()
    p2 = svc.create(
        owner, operation="memory.upsert", payload={"x": 1}, reason="upsert v2 stale",
        rollback="undo", target_id=m.id, expected_version=1,
    )
    created["proposals"].append(p2.id)
    with pytest.raises(Conflict) as ei:
        svc.decide(owner, p2.id, p2.digest, approve=True)
    assert ei.value.code == "target_version_changed"

    # 3) Correct version (2) now succeeds.
    p3 = svc.create(
        owner, operation="memory.upsert", payload={"x": 1}, reason="upsert v2 ok",
        rollback="undo", target_id=m.id, expected_version=2,
    )
    created["proposals"].append(p3.id)
    decided3 = svc.decide(owner, p3.id, p3.digest, approve=True)
    assert decided3.status == "executed"


def test_s09_concurrent_approvals_single_winner(pg_session):
    s, sm, created = pg_session
    owner = Actor.owner("owner-s09", csrf_token="")
    audit = AuditService(s)
    svc = ProposalService(s, audit)

    p = svc.create(
        owner, operation="task.release",
        payload={"environment": "prod", "commit_sha": "a" * 40, "image_digest": "sha256:" + "b" * 64},
        reason="ship", rollback="rollback",
    )
    s.commit()
    created["proposals"].append(p.id)
    pid, digest = p.id, p.digest

    N = 12
    results = {"ok": [], "conflict": []}
    barrier = threading.Barrier(N)

    def worker():
        # Each thread gets its OWN connection/session.
        ts = sm()
        try:
            ta = AuditService(ts)
            tsvc = ProposalService(ts, ta)
            owner_t = Actor.owner("owner-s09", csrf_token="")
            barrier.wait()  # maximise simultaneous contention
            tsvc.decide(owner_t, pid, digest, approve=True)
            ts.commit()
            results["ok"].append(threading.get_ident())
        except Conflict:
            ts.rollback()
            results["conflict"].append(threading.get_ident())
        except Exception:
            ts.rollback()
            raise
        finally:
            ts.close()

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Exactly one decision won; the rest were rejected as already-decided.
    assert len(results["ok"]) == 1, f"expected 1 winner, got {results}"
    assert len(results["conflict"]) == N - 1

    # The proposal ended in exactly one external state, and the outbox has
    # exactly one operation (no duplicate execution).
    final = s.execute(text("SELECT status FROM proposals WHERE id=:i"), {"i": pid}).scalar_one()
    assert final == "approved_pending_execution"
    op_count = s.execute(
        text("SELECT count(*) FROM operations WHERE proposal_id=:i"), {"i": pid}
    ).scalar_one()
    assert op_count == 1
