"""BUG-06: atomic budget reservation, caps, settle/release and cancel propagation."""

from decimal import Decimal

import pytest

from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetService
from find_yourself.services.errors import Conflict


def _task(session, owner_id="owner-1"):
    t = Task(id="task-1", owner_id=owner_id, goal="do thing", deadline=utcnow(), idempotency_key="k1")
    session.add(t)
    session.flush()
    return t


def test_reserve_enforces_per_task_cap(session, owner):
    audit = AuditService(session)
    b = BudgetService(session, audit)
    _task(session)
    b.reserve(owner, task_id="task-1", amount="0.30", idempotency_key="r1")
    with pytest.raises(Conflict):
        b.reserve(owner, task_id="task-1", amount="0.30", idempotency_key="r2")  # would exceed 0.50


def test_settle_and_release(session, owner):
    audit = AuditService(session)
    b = BudgetService(session, audit)
    _task(session)
    r = b.reserve(owner, task_id="task-1", amount="0.10", idempotency_key="r1")
    b.settle(owner, r.id, "0.08")
    assert r.state == "settled"
    r2 = b.reserve(owner, task_id="task-1", amount="0.20", idempotency_key="r2")
    b.release(owner, r2.id)
    assert r2.state == "released"


def test_cancel_propagates_and_releases(session, owner):
    audit = AuditService(session)
    b = BudgetService(session, audit)
    _task(session)
    child = Task(id="task-2", owner_id="owner-1", parent_task_id="task-1", root_task_id="task-1",
                 goal="child", deadline=utcnow(), idempotency_key="k2")
    session.add(child)
    session.flush()
    b.reserve(owner, task_id="task-1", amount="0.10", idempotency_key="r1")
    b.reserve(owner, task_id="task-2", amount="0.10", idempotency_key="r2")
    released = b.cancel_task(owner, "task-1")
    assert released >= 2
    assert session.get(Task, "task-2").status == "cancelled"
