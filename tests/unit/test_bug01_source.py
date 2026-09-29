"""BUG-01: source permission propagation. A personal-derived shared memory is inert until approved."""

from find_yourself.services.audit import AuditService
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService


def _memories(session):
    audit = AuditService(session)
    grants = GrantService(session, audit)
    mem = MemoryService(session, grants, audit)
    return mem, grants


def test_personal_derived_shared_memory_inert_until_approved(session, owner):
    mem, grants = _memories(session)
    # A personal memory exists.
    personal = mem.upsert(owner, owner_id="owner-1", domain="personal", category="self_report",
                          content="I am worried about money", source_ids=[])
    # Derive a shared memory from the personal source.
    derived = mem.upsert(owner, owner_id="owner-1", domain="shared", category="hypothesis",
                         content="Derived shareable note about finances",
                         source_ids=[personal.id], hypothesis_status="hypothesis")
    assert derived.active is False, "cross-domain derived memory must start inactive"

    # work consumer must NOT see it in search before approval.
    hits = mem.search("work", "finances")
    assert all(h["id"] != derived.id for h in hits)

    # After owner approval it becomes visible.
    mem.activate_approved(owner, derived.id)
    hits = mem.search("work", "finances")
    assert any(h["id"] == derived.id for h in hits)


def test_same_domain_memory_is_active(session, owner):
    mem, _ = _memories(session)
    m = mem.upsert(owner, owner_id="owner-1", domain="work", category="tool_fact",
                   content="build uses uv", source_ids=[])
    assert m.active is True
