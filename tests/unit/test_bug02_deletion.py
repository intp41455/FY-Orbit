"""BUG-02: deletion cascades to derived memories, revisions and writes a tombstone."""

from find_yourself.db.models import Memory, MemoryRevision, Tombstone
from find_yourself.services.audit import AuditService
from find_yourself.services.deletion import DeletionService
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService


def test_deletion_cascades_and_tombstones(session, owner):
    audit = AuditService(session)
    grants = GrantService(session, audit)
    mem = MemoryService(session, grants, audit)
    deleter = DeletionService(session, audit)

    src = mem.upsert(owner, owner_id="owner-1", domain="personal", category="self_report",
                     content="private source", source_ids=[])
    derived = mem.upsert(owner, owner_id="owner-1", domain="personal", category="hypothesis",
                         content="derived thought", source_ids=[src.id])

    tomb = deleter.delete(owner, src.id, "memory", "owner requested deletion")
    assert isinstance(tomb, Tombstone)

    session.expire_all()
    src_row = session.get(Memory, src.id)
    derived_row = session.get(Memory, derived.id)
    assert src_row.deleted_at is not None and src_row.active is False
    assert derived_row.deleted_at is not None and derived_row.active is False

    # Tombstone replay lists the deleted id.
    assert src.id in deleter.replay_tombstones()
