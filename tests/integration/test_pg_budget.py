"""G2 T07: concurrent child-task reservations must not exceed the root tree cap.

8 threads each reserve 0.10 USD for a distinct child of one root task. The
per-task cap (0.50 USD) is aggregated over the *whole tree*, so at most 5
reservations may succeed; the rest must fail with a budget Conflict even under
real Postgres concurrency. Requires a real Postgres DSN.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from find_yourself.db.models import BudgetLedger, BudgetReservation, Task
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetService
from find_yourself.services.errors import Conflict

PG_URL = os.environ.get("FY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="real Postgres required")

OWNER = Actor.owner("t07", csrf_token="")
TAG = "pg-budget-t07"


def test_concurrent_children_respect_tree_cap():
    root_id = f"{TAG}-root-{os.urandom(3).hex()}"
    child_ids = [f"{TAG}-child-{os.urandom(3).hex()}-{i}" for i in range(8)]
    deadline = utcnow()

    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)

    s = sm()
    try:
        s.add(Task(id=root_id, owner_id="t07", goal="root", domain="personal",
                   status="running", deadline=deadline, idempotency_key=f"idem-{root_id}",
                   created_at=utcnow(), version=1))
        for cid in child_ids:
            s.add(Task(id=cid, owner_id="t07", goal="child", domain="personal",
                       parent_task_id=root_id, root_task_id=root_id, depth=1,
                       status="queued", deadline=deadline, idempotency_key=f"idem-{cid}",
                       created_at=utcnow(), version=1))
        s.commit()
    finally:
        s.close()

    barrier = threading.Barrier(8)
    successes, failures = [], []

    def worker(cid):
        e = create_engine(PG_URL, future=True)
        S = sessionmaker(bind=e, future=True)
        ss = S()
        try:
            barrier.wait()
            bs = BudgetService(ss, AuditService(ss))
            r = bs.reserve(OWNER, task_id=cid, amount="0.10",
                           idempotency_key=f"res-{cid}", scope="task")
            ss.commit()
            successes.append(r.id)
        except Conflict:
            ss.rollback()
            failures.append(cid)
        except Exception:
            ss.rollback()
            raise
        finally:
            ss.close(); e.dispose()

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, child_ids))

    # Assertions.
    assert len(successes) <= 5, f"tree cap breached: {len(successes)} reservations succeeded"
    assert len(successes) + len(failures) == 8
    assert len(failures) >= 3

    s2 = sm()
    try:
        total = s2.execute(
            select(func.coalesce(func.sum(BudgetReservation.amount), 0))
            .where(BudgetReservation.task_id.in_(child_ids + [root_id]),
                   BudgetReservation.state.in_(["reserved", "unknown"]))
        ).scalar()
        assert float(total) <= 0.5 + 1e-9, f"tree reserved sum {total} > 0.50"

        # monthly ledger consistent.
        ledger_sum = s2.execute(
            select(func.coalesce(func.sum(BudgetLedger.delta), 0))
            .where(BudgetLedger.task_id.in_(child_ids + [root_id]),
                   BudgetLedger.reason == "reserve")
        ).scalar()
        assert abs(float(ledger_sum) - 0.10 * len(successes)) < 1e-9
    finally:
        # cleanup (children first, then root)
        s2.execute(delete(BudgetLedger).where(BudgetLedger.task_id.in_(child_ids + [root_id])))
        s2.execute(delete(BudgetReservation).where(BudgetReservation.task_id.in_(child_ids + [root_id])))
        s2.execute(delete(Task).where(Task.id.in_(child_ids)))
        s2.execute(delete(Task).where(Task.id == root_id))
        s2.commit(); s2.close(); eng.dispose()
