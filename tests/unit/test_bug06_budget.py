"""BUG-06: atomic budget reservation, caps, settle/release and cancel propagation."""


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


def test_companion_reply_with_priced_gateway_reserves_budget(session, owner):
    """BUG #5: companion's synthetic task_id must not 404 inside budget.reserve.

    POST /reply with a configured priced model returned 404 task_not_found
    because ModelGateway.complete() reserves with task_id="companion:{conv}"
    and BudgetService looked up a tasks row companion never created
    (BudgetReservation.task_id is also a FK to tasks.id). The fix materialises
    a real budget-anchor Task row per conversation.
    """
    from decimal import Decimal

    from sqlalchemy import select

    from find_yourself.config import Settings
    from find_yourself.db.models import BudgetReservation, Conversation
    from find_yourself.runtime.gateway import CallResult, ModelGateway
    from find_yourself.services.companion import CompanionService

    class _PricedProvider:
        def complete(self, *, model, prompt, max_tokens=1024, timeout_seconds=30.0):
            return CallResult(
                text="我在听，请继续说。",
                usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
                settled_amount=Decimal("0.0002"),
                provider_request_id="p-1",
            )

    audit = AuditService(session)
    budget = BudgetService(session, audit)
    settings = Settings(
        session_secret="x" * 32, model_api_key="k", model_base_url="http://127.0.0.1:9")
    gateway = ModelGateway(settings, budget=budget, provider=_PricedProvider())
    companion = CompanionService(
        session, audit, settings=settings, budget=budget, model_gateway=gateway)
    conv = Conversation(
        id="conv-1", owner_id=owner.owner_id, title="t", domain="personal", mode="listen")
    session.add(conv)
    session.flush()

    resp = companion.respond(owner, conv, "今天去公园散步了")

    assert resp["role"] == "assistant"
    assert resp["generated_by"] == "model"
    # A real tasks row anchors the budget reservation (FK + reserve lookup).
    anchor = session.get(Task, "companion:conv-1")
    assert anchor is not None
    assert anchor.status not in {"cancelled", "completed", "failed"}
    row = session.execute(
        select(BudgetReservation).where(BudgetReservation.task_id == "companion:conv-1")
    ).scalars().one()
    assert row.scope == "inference"
    assert row.state in {"settled", "reserved"}
