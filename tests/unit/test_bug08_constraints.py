"""BUG-08: explicit ORM constraints, no create_all-as-schema, DB-enforced invariants."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from find_yourself.db.models import Grant, Proposal, Task
from find_yourself.services.hasher import digest


def test_all_23_tables_exist(session):
    insp = inspect(session.get_bind())
    names = set(insp.get_table_names())
    expected = {
        "auth_sessions", "service_identities", "conversations", "messages", "tasks",
        "task_attempts", "proposals", "grants", "memories", "memory_revisions",
        "source_relations", "artifacts", "agents", "agent_leases", "skills",
        "skill_evaluations", "budget_reservations", "budget_ledger", "operations",
        "audit_events", "audit_anchors", "tombstones", "search_documents",
    }
    missing = expected - names
    assert not missing, f"missing tables: {missing}"


def test_grant_rejects_empty_record_ids(session, owner):
    from find_yourself.db.types import utcnow
    with pytest.raises(IntegrityError):
        g = Grant(
            id="g1", source_domain="personal", consumer_domain="work", record_ids=[],
            expires_at=utcnow().replace(), state="active", scope_hash="x",
        )
        session.add(g)
        session.flush()


def test_proposal_status_check_rejects_garbage(session):
    from find_yourself.db.types import utcnow
    with pytest.raises(IntegrityError):
        p = Proposal(
            id="p1", operation="memory.upsert", payload={}, reason="r" * 5,
            rollback="rb" * 5, digest="ab" * 32, status="NOT_A_REAL_STATUS",
            expires_at=utcnow(),
        )
        session.add(p)
        session.flush()


def test_idempotency_unique_on_task(session):
    from find_yourself.db.types import utcnow
    common = dict(owner_id="owner-1", goal="g", deadline=utcnow(), idempotency_key="same-key")
    session.add(Task(id="t1", **common))
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(Task(id="t2", **common))
        session.flush()


def test_digest_is_canonical_and_stable():
    a = digest({"b": 1, "a": [2, 3]})
    b = digest({"a": [2, 3], "b": 1})
    assert a == b  # sorted keys
