"""BUG-11: retrieval applies authorization predicate before ranking; no cross-domain leak."""

from datetime import timedelta

from find_yourself.db.types import utcnow
from find_yourself.services.audit import AuditService
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService


def _svc(session):
    audit = AuditService(session)
    grants = GrantService(session, audit)
    mem = MemoryService(session, grants, audit)
    return mem, grants


def test_work_cannot_read_personal_without_grant(session, owner):
    mem, grants = _svc(session)
    personal = mem.upsert(owner, owner_id="owner-1", domain="personal", category="self_report",
                          content="my secret salary number", source_ids=[])
    hits = mem.search("work", "salary")
    assert all(h["id"] != personal.id for h in hits)


def test_explicit_grant_opens_only_named_record(session, owner):
    mem, grants = _svc(session)
    p1 = mem.upsert(owner, owner_id="owner-1", domain="personal", category="self_report",
                    content="salary secret alpha", source_ids=[])
    p2 = mem.upsert(owner, owner_id="owner-1", domain="personal", category="self_report",
                    content="salary secret beta", source_ids=[])
    grants.create(owner, source_domain="personal", consumer_domain="work",
                  record_ids=[p1.id], expires_at=utcnow() + timedelta(days=5))
    hits = mem.search("work", "salary")
    ids = {h["id"] for h in hits}
    assert p1.id in ids
    assert p2.id not in ids
