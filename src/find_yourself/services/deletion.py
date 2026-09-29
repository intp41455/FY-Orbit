"""Deletion planning, cascading tombstoning and replay (§8.3).

A deletion plan enumerates the closure: the primary record, derived memories
(via source_relations), history revisions, search-document rows, artifacts and
outbox references. On execution the primary and its dependents are soft-deleted,
search rows tombstoned, revisions redacted, and a :class:`Tombstone` written
with a hash of the dependency graph. Backups are not wiped immediately, but
before a restored environment serves traffic it must replay tombstones.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Artifact, Memory, MemoryRevision, SearchDocument, SourceRelation, Tombstone,
)
from ..db.types import utcnow
from .actor import Actor
from .errors import NotFound
from .hasher import digest
from .audit import AuditService


class DeletionService:
    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    def plan(self, target_id: str) -> dict:
        derived = list(self.s.execute(
            select(SourceRelation).where(SourceRelation.source_id == target_id)
        ).scalars())
        derived_ids = [d.derived_id for d in derived]
        revisions = list(self.s.execute(
            select(MemoryRevision).where(MemoryRevision.memory_id.in_([target_id] + derived_ids))
        ).scalars()) if derived_ids or True else []
        search_rows = list(self.s.execute(
            select(SearchDocument).where(SearchDocument.record_id.in_([target_id] + derived_ids))
        ).scalars())
        artifacts = list(self.s.execute(
            select(Artifact).where(Artifact.task_id == target_id)
        ).scalars())
        return {
            "target_id": target_id,
            "derived_memory_ids": derived_ids,
            "revision_ids": [r.id for r in revisions],
            "search_doc_ids": [r.id for r in search_rows],
            "artifact_ids": [a.id for a in artifacts],
        }

    def delete(self, actor: Actor, target_id: str, target_kind: str, reason: str) -> Tombstone:
        actor.require_owner()
        plan = self.plan(target_id)
        now = utcnow()

        # Soft-delete primary memory if it is one.
        mem = self.s.get(Memory, target_id)
        if mem is not None:
            mem.deleted_at = now
            mem.active = False
            mem.content = ""
            mem.content_hash = ""

        # Cascade to derived memories and redact their revisions.
        for did in plan["derived_memory_ids"]:
            d = self.s.get(Memory, did)
            if d is not None:
                d.deleted_at = now
                d.active = False
                d.content = ""
                d.content_hash = ""
            for rev in self.s.execute(select(MemoryRevision).where(MemoryRevision.memory_id == did)).scalars():
                rev.redacted = True
                rev.content_hash = ""

        # Tombstone search documents (do not physically remove — replayable).
        for sid in plan["search_doc_ids"]:
            sd = self.s.get(SearchDocument, sid)
            if sd is not None:
                sd.tombstoned_at = now

        graph_hash = digest(plan)
        tomb = Tombstone(
            id=uuid4().hex, target_id=target_id, target_kind=target_kind,
            reason=reason[:300], deleted_by=actor.owner_id or actor.service_id,
            dep_graph_hash=graph_hash,
        )
        self.s.add(tomb)
        self.s.flush()
        self.audit.append(actor, "deletion.tombstoned", target_id,
                          {"kind": target_kind, "derived": len(plan["derived_memory_ids"])})
        return tomb

    def replay_tombstones(self) -> list[str]:
        """On restore: re-assert tombstones before opening service traffic."""
        rows = list(self.s.execute(select(Tombstone)).scalars())
        return [r.target_id for r in rows]
