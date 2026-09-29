"""Real-Postgres tests for the PostgresCorePorts adapter.

Covers: concurrent outbox claim (single winner, repeat = already_done),
digest/target-version rejection in verify_approval_permission, and budget
cancel propagation. All rows use unique pg-ports-fixture ids and are cleaned up.
"""
import asyncio
import os

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from find_yourself.db.models import (
    BudgetLedger, BudgetReservation, Memory, Operation, Proposal, Task,
)
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.proposal import ProposalService
from find_yourself.services.pg_ports import PostgresCorePorts

PG_URL = os.environ.get("FY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL.startswith("postgresql"), reason="requires real Postgres")
OWNER = Actor.owner("pg-ports", csrf_token="")


@pytest.fixture
def ports():
    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)
    created = {"ids": []}
    try:
        yield PostgresCorePorts(sm, effect_dir=".runtime/effects")
    finally:
        s = sm()
        try:
            for kind, i in created["ids"]:
                model = {"task": Task, "mem": Memory, "prop": Proposal, "op": Operation,
                         "res": BudgetReservation}[kind]
                s.execute(delete(model).where(model.id == i))
            s.commit()
        finally:
            s.close()
        eng.dispose()


def _mk_task(s, tag):
    t = Task(id=f"pg-ports-task-{tag}", owner_id="pg-ports", goal="g", domain="personal",
             status="running", deadline=utcnow(), idempotency_key=f"idem-{tag}",
             created_at=utcnow(), version=1)
    s.add(t); s.flush()
    return t


async def test_outbox_claim_single_winner(ports):
    tag = os.urandom(4).hex()
    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)
    s = sm()
    try:
        t = _mk_task(s, tag)
        mem = Memory(id=f"pg-ports-mem-{tag}", owner_id="pg-ports", domain="personal",
                     category="fact", content="x", content_hash="h" + tag,
                     active=True, hypothesis_status="fact",
                     created_at=utcnow(), version=1)
        s.add(mem); s.flush()
        svc = ProposalService(s, AuditService(s))
        p = svc.create(OWNER, operation="task.merge", payload={"a": 1}, reason="r",
                       rollback="", target_id=mem.id, expected_version=1)
        svc.decide(OWNER, p.id, p.digest, approve=True)  # -> approved_pending_execution + op
        s.commit()
        pid = p.id; mid = mem.id; tid = t.id
    finally:
        s.close(); eng.dispose()

    results = await asyncio.gather(*[ports.claim_outbox_operation(pid, "k") for _ in range(8)])
    winners = [r for r in results if r["claimed"]]
    assert len(winners) == 1, f"expected single winner, got {len(winners)}"
    assert all(r["code"] == "already_done" for r in results if not r["claimed"])

    # cleanup
    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)
    s2 = sm()
    op = s2.execute(select(Operation).where(Operation.proposal_id == pid)).scalar_one()
    s2.execute(delete(Operation).where(Operation.id == op.id))
    s2.execute(delete(Proposal).where(Proposal.id == pid))
    s2.execute(delete(Memory).where(Memory.id == mid))
    s2.execute(delete(Task).where(Task.id == tid))
    s2.commit(); s2.close(); eng.dispose()


async def test_verify_digest_and_version_reject(ports):
    tag = os.urandom(4).hex()
    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)
    s = sm()
    try:
        mem = Memory(id=f"pg-ports-mem-{tag}", owner_id="pg-ports", domain="personal",
                     category="fact", content="x", content_hash="h" + tag,
                     active=True, hypothesis_status="fact",
                     created_at=utcnow(), version=1)
        s.add(mem); s.flush()
        svc = ProposalService(s, AuditService(s))
        p = svc.create(OWNER, operation="memory.upsert", payload={"a": 1}, reason="r",
                       rollback="", target_id=mem.id, expected_version=1)
        s.commit()
        pid = p.id; good_digest = p.digest; mid = mem.id
    finally:
        s.close(); eng.dispose()

    try:
        v = await ports.verify_approval_permission(pid, "baddigest", 1)
        assert v["allowed"] is False and v["code"] == "digest_mismatch"
        eng = create_engine(PG_URL, future=True)
        s3 = sessionmaker(bind=eng, future=True)()
        s3.get(Memory, mid).version = 2; s3.commit(); s3.close(); eng.dispose()
        v2 = await ports.verify_approval_permission(pid, good_digest, 1)
        assert v2["allowed"] is False and v2["code"] == "stale_version"
    finally:
        eng = create_engine(PG_URL, future=True)
        s4 = sessionmaker(bind=eng, future=True)()
        s4.execute(delete(Proposal).where(Proposal.id == pid))
        s4.execute(delete(Memory).where(Memory.id == mid))
        s4.commit(); s4.close(); eng.dispose()


async def test_budget_cancel_propagation(ports):
    tag = os.urandom(4).hex()
    eng = create_engine(PG_URL, future=True)
    sm = sessionmaker(bind=eng, future=True)
    s = sm()
    try:
        _mk_task(s, tag)
        s.commit()
        tid = f"pg-ports-task-{tag}"
    finally:
        s.close(); eng.dispose()

    try:
        r = await ports.reserve_budget(tid, 1, 0.10, "task")
        assert r["reserved"] is True
        await ports.task_cancel(tid, 1, "user stopped")
    finally:
        eng = create_engine(PG_URL, future=True)
        s5 = sessionmaker(bind=eng, future=True)()
        states = [x.state for x in s5.execute(
            select(BudgetReservation).where(BudgetReservation.task_id == tid)).scalars()]
        s5.execute(delete(BudgetLedger).where(BudgetLedger.task_id == tid))
        s5.execute(delete(BudgetReservation).where(BudgetReservation.task_id == tid))
        s5.execute(delete(Task).where(Task.id == tid))
        s5.commit(); s5.close(); eng.dispose()
    assert states and all(st == "cancelled" for st in states), states
