"""Real-Postgres integration for G8 deletion / tombstone replay.

Skipped unless FY_DATABASE_URL points at Postgres. Builds a small graph
(parent memory -> derived memory), revisions, search documents and an artifact,
runs DeletionService.delete, asserts the full cascade, then exercises
verify_replay() on a restored-state basis.
"""

import os

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import sessionmaker

from find_yourself.db import models  # noqa: F401
from find_yourself.db.models import (
    Artifact,
    AuditEvent,
    Memory,
    MemoryRevision,
    SearchDocument,
    SourceRelation,
    Task,
    Tombstone,
)
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.deletion import DeletionService

PG_URL = os.environ.get("FY_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"),
    reason="real Postgres required",
)


@pytest.fixture()
def pg():
    eng = create_engine(PG_URL, future=True, pool_pre_ping=True)
    sm = sessionmaker(bind=eng, future=True)
    s = sm()
    ids = {"memories": [], "revisions": [], "relations": [], "search": [],
           "artifacts": [], "tombstones": [], "audit": [], "tasks": []}
    try:
        yield s, ids
    finally:
        try:
            if ids["audit"]:
                s.execute(delete(AuditEvent).where(AuditEvent.id.in_(ids["audit"])))
            if ids["relations"]:
                s.execute(delete(SourceRelation).where(SourceRelation.id.in_(ids["relations"])))
            if ids["revisions"]:
                s.execute(delete(MemoryRevision).where(MemoryRevision.id.in_(ids["revisions"])))
            if ids["search"]:
                s.execute(delete(SearchDocument).where(SearchDocument.id.in_(ids["search"])))
            if ids["artifacts"]:
                s.execute(delete(Artifact).where(Artifact.id.in_(ids["artifacts"])))
            if ids["tombstones"]:
                s.execute(delete(Tombstone).where(Tombstone.id.in_(ids["tombstones"])))
            if ids.get("tasks"):
                s.execute(delete(Task).where(Task.id.in_(ids["tasks"])))
            if ids["memories"]:
                s.execute(delete(Memory).where(Memory.id.in_(ids["memories"])))
            s.commit()
        except Exception:
            s.rollback()
        s.close()
        eng.dispose()


def _mem(s, mid, owner, active=True):
    m = Memory(
        id=mid, owner_id=owner, domain="personal", category="fact",
        content="secret-" + mid, content_hash="h" + mid, active=active,
        hypothesis_status="fact", created_at=utcnow(), version=1,
    )
    s.add(m)
    return m


def test_g8_delete_cascade_and_verify_replay(pg):
    s, ids = pg
    owner = Actor.owner("owner-g8", csrf_token="")
    audit = AuditService(s)
    deleter = DeletionService(s, audit)

    parent_id = "mem-parent-" + os.urandom(4).hex()
    derived_id = "mem-derived-" + os.urandom(4).hex()

    # Graph: parent -> derived.
    _mem(s, parent_id, "owner-g8")
    _mem(s, derived_id, "owner-g8")
    rel = SourceRelation(
        id="rel-" + os.urandom(4).hex(), source_id=parent_id, source_kind="memory",
        derived_id=derived_id, derived_kind="memory", relation_type="derives",
        permission_snapshot={}, version=1,
    )
    s.add(rel)

    # Revisions for BOTH primary and derived.
    rev_p = MemoryRevision(id="revp-" + os.urandom(4).hex(), memory_id=parent_id,
                           version=1, content_hash="rp" + parent_id, created_at=utcnow())
    rev_d = MemoryRevision(id="revd-" + os.urandom(4).hex(), memory_id=derived_id,
                           version=1, content_hash="rd" + derived_id, created_at=utcnow())
    s.add_all([rev_p, rev_d])

    # Search documents for both.
    sd_p = SearchDocument(id="sdp-" + os.urandom(4).hex(), record_id=parent_id,
                          record_kind="memory", domain="personal",
                          content_hash="shp" + parent_id, version=1)
    sd_d = SearchDocument(id="sdd-" + os.urandom(4).hex(), record_id=derived_id,
                          record_kind="memory", domain="personal",
                          content_hash="shd" + derived_id, version=1)
    s.add_all([sd_p, sd_d])

    # Artifact attached to a real Task (artifact.task_id FK -> tasks.id).
    task_id = "task-" + os.urandom(4).hex()
    task = Task(
        id=task_id, owner_id="owner-g8", goal="do thing", domain="personal",
        status="completed", deadline=utcnow(), idempotency_key="idem-" + task_id,
        created_at=utcnow(), version=1,
    )
    s.add(task)
    s.flush()
    art = Artifact(id="art-" + os.urandom(4).hex(), task_id=task_id, domain="personal",
                   sha256="a" * 64, size=10, media_type="text/plain", verified=True,
                   created_at=utcnow())
    s.add(art)
    s.flush()

    ids["memories"] += [parent_id, derived_id]
    ids["revisions"] += [rev_p.id, rev_d.id]
    ids["relations"] += [rel.id]
    ids["search"] += [sd_p.id, sd_d.id]
    ids["artifacts"] += [art.id]
    ids["tasks"] = [task_id]

    # Act 1: delete the memory graph.
    tomb = deleter.delete(owner, parent_id, "memory", "owner requested")
    s.commit()
    ids["tombstones"] += [tomb.id]

    # Primary + derived soft-deleted and content cleared.
    p = s.get(Memory, parent_id)
    d = s.get(Memory, derived_id)
    assert p.active is False and p.deleted_at is not None and p.content == "" and p.content_hash == ""
    assert d.active is False and d.deleted_at is not None and d.content == "" and d.content_hash == ""

    # BOTH primary and derived revisions redacted (BUG-02 closure).
    rp = s.get(MemoryRevision, rev_p.id)
    rd = s.get(MemoryRevision, rev_d.id)
    assert rp.redacted is True and rp.content_hash == ""
    assert rd.redacted is True and rd.content_hash == ""

    # Search documents tombstoned.
    assert s.get(SearchDocument, sd_p.id).tombstoned_at is not None
    assert s.get(SearchDocument, sd_d.id).tombstoned_at is not None

    # Act 2: delete the task -> its artifact is soft-deleted (BUG-02 closure).
    tomb_task = deleter.delete(owner, task_id, "task", "owner requested")
    s.commit()
    ids["tombstones"] += [tomb_task.id]
    assert s.get(Artifact, art.id).deleted_at is not None

    # Tombstone carries a graph hash and an audit event exists.
    assert tomb.dep_graph_hash and len(tomb.dep_graph_hash) == 64
    audit_rows = s.execute(
        select(AuditEvent).where(AuditEvent.action == "deletion.tombstoned")
    ).scalars().all()
    assert any(e.target == parent_id for e in audit_rows)
    ids["audit"] += [e.id for e in audit_rows]

    # verify_replay reports ok=True for this tombstone.
    report = deleter.verify_replay()
    assert report["ok"] is True
    mine = [t for t in report["tombstones"] if t["tombstone_id"] == tomb.id]
    assert len(mine) == 1 and mine[0]["ok"] is True and mine[0]["violations"] == []

    # Simulate a restored backup that revived the primary: replay must flag it.
    s.execute(text("UPDATE memories SET active=true, deleted_at=NULL WHERE id=:i"), {"i": parent_id})
    s.commit()
    report2 = deleter.verify_replay()
    assert report2["ok"] is False
    mine2 = [t for t in report2["tombstones"] if t["tombstone_id"] == tomb.id]
    assert mine2 and mine2[0]["ok"] is False
    assert any("primary" in v for v in mine2[0]["violations"])


def test_pg_multilevel_derived_cascade(pg):
    s, ids = pg
    owner = Actor.owner("owner-g8-multi", csrf_token="")
    audit = AuditService(s)
    deleter = DeletionService(s, audit)

    m1 = "mem1-" + os.urandom(4).hex()
    m2 = "mem2-" + os.urandom(4).hex()
    m3 = "mem3-" + os.urandom(4).hex()

    _mem(s, m1, "owner-g8-multi")
    _mem(s, m2, "owner-g8-multi")
    _mem(s, m3, "owner-g8-multi")

    # M1 -> M2 -> M3
    r1 = SourceRelation(
        id="rel1-" + os.urandom(4).hex(), source_id=m1, source_kind="memory",
        derived_id=m2, derived_kind="memory", relation_type="derives",
        permission_snapshot={}, version=1,
    )
    r2 = SourceRelation(
        id="rel2-" + os.urandom(4).hex(), source_id=m2, source_kind="memory",
        derived_id=m3, derived_kind="memory", relation_type="derives",
        permission_snapshot={}, version=1,
    )
    s.add_all([r1, r2])
    s.flush()

    ids["memories"] += [m1, m2, m3]
    ids["relations"] += [r1.id, r2.id]

    plan = deleter.plan(m1)
    assert m2 in plan["derived_memory_ids"]
    assert m3 in plan["derived_memory_ids"]

    tomb = deleter.delete(owner, m1, "memory", "multilevel test")
    s.commit()
    ids["tombstones"].append(tomb.id)

    # Assert all three are soft-deleted
    for mid in (m1, m2, m3):
        row = s.get(Memory, mid)
        assert row.active is False
        assert row.deleted_at is not None
        assert row.content == ""

