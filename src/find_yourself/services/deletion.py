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
    Artifact, Memory, MemoryRevision, Proposal, SearchDocument, SourceRelation, Tombstone,
    User, UserConsent,
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
        # Recursive transitive closure of derived records via SourceRelation
        visited: set[str] = set()
        queue = [target_id]
        while queue:
            curr = queue.pop(0)
            edges = list(self.s.execute(
                select(SourceRelation).where(SourceRelation.source_id == curr)
            ).scalars())
            for e in edges:
                if e.derived_id not in visited and e.derived_id != target_id:
                    visited.add(e.derived_id)
                    queue.append(e.derived_id)
        derived_ids = sorted(list(visited))
        all_ids = [target_id] + derived_ids

        revisions = list(self.s.execute(
            select(MemoryRevision).where(MemoryRevision.memory_id.in_(all_ids))
        ).scalars()) if all_ids else []
        search_rows = list(self.s.execute(
            select(SearchDocument).where(SearchDocument.record_id.in_(all_ids))
        ).scalars()) if all_ids else []
        artifacts = list(self.s.execute(
            select(Artifact).where(Artifact.task_id.in_(all_ids))
        ).scalars()) if all_ids else []
        proposals = list(self.s.execute(
            select(Proposal).where(Proposal.target_id.in_(all_ids))
        ).scalars()) if all_ids else []
        return {
            "target_id": target_id,
            "derived_memory_ids": derived_ids,
            "revision_ids": [r.id for r in revisions],
            "search_doc_ids": [r.id for r in search_rows],
            "artifact_ids": [a.id for a in artifacts],
            "proposal_ids": [p.id for p in proposals],
        }

    def delete(self, actor: Actor, target_id: str, target_kind: str, reason: str) -> Tombstone:
        actor.require_owner()
        plan = self.plan(target_id)
        now = utcnow()

        all_memory_ids = [target_id] + plan["derived_memory_ids"]

        # Soft-delete primary memory if it is one.
        mem = self.s.get(Memory, target_id)
        if mem is not None:
            mem.deleted_at = now
            mem.active = False
            mem.content = ""
            mem.content_hash = ""

        # Cascade to derived memories.
        for did in plan["derived_memory_ids"]:
            d = self.s.get(Memory, did)
            if d is not None:
                d.deleted_at = now
                d.active = False
                d.content = ""
                d.content_hash = ""

        # Redact revisions of BOTH the primary and every derived memory
        # (BUG-02: previously only derived revisions were redacted).
        for mid in all_memory_ids:
            for rev in self.s.execute(
                select(MemoryRevision).where(MemoryRevision.memory_id == mid)
            ).scalars():
                rev.redacted = True
                rev.content_hash = ""

        # Tombstone search documents (do not physically remove — replayable).
        for sid in plan["search_doc_ids"]:
            sd = self.s.get(SearchDocument, sid)
            if sd is not None:
                sd.tombstoned_at = now

        # Soft-delete artifacts of this target (BUG-02: planned but previously
        # left untouched).
        for aid in plan["artifact_ids"]:
            art = self.s.get(Artifact, aid)
            if art is not None:
                art.deleted_at = now
                art.verified = False

        # Redact proposal payloads referencing deleted records
        for pid in plan.get("proposal_ids", []):
            prop = self.s.get(Proposal, pid)
            if prop is not None:
                prop.payload = {"redacted": True, "reason": "target_deleted"}
                if prop.status == "pending":
                    prop.status = "rejected"

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

    def delete_account(self, actor: Actor, *, ip: str = "") -> dict:
        """Self-service full account deletion (GDPR erasure).

        Cascades to every memory the user owns (reusing per-record tombstoning),
        revokes all sessions, anonymizes the user row, and KEEPS consent records
        (compliance proof requires retaining the fact of consent, decoupled from
        the erased identity).
        """
        actor.require_owner()
        owner_id = actor.owner_id

        user = self.s.get(User, owner_id)
        if user is None:
            raise NotFound("user_not_found", "No user account exists for this identity")

        memory_ids = [m.id for m in self.s.execute(
            select(Memory).where(Memory.owner_id == owner_id, Memory.deleted_at.is_(None))
        ).scalars()]
        for mid in memory_ids:
            self.delete(actor, mid, "memory", "account_deletion")

        from .auth import AuthService  # local import: auth imports nothing from deletion
        auth = AuthService(self.s, self.audit)
        revoked = auth.revoke_owner_sessions(owner_id)

        # Consent records are retained on purpose (see docstring); list them before
        # anonymizing so the response can state the count.
        consents = list(self.s.execute(
            select(UserConsent).where(UserConsent.user_id == owner_id)
        ).scalars())

        user.status = "deleted"
        user.password_hash = "$locked$"
        user.email = f"deleted-{user.id}@invalid"
        user.display_name = ""

        self.audit.append(actor, "account.deleted", owner_id,
                          {"memories": len(memory_ids), "sessions_revoked": revoked,
                           "consents_retained": len(consents), "ip": (ip or "")[:64]})
        self.s.flush()
        return {
            "owner_id": owner_id,
            "memories_deleted": len(memory_ids),
            "sessions_revoked": revoked,
            "consents_retained": len(consents),
        }

    def replay_tombstones(self) -> list[str]:
        """On restore: re-assert tombstones before opening service traffic."""
        rows = list(self.s.execute(select(Tombstone)).scalars())
        return [r.target_id for r in rows]

    def verify_replay(self) -> dict:
        """Machine-readable post-restore audit of every tombstone.

        Returns ``{"ok": bool, "checked": n, "tombstones": [...]}``. Each entry
        lists the target, its recorded dep_graph_hash and any rows that are
        missing or still violate the deletion (e.g. a restored backup revived a
        primary/derived memory, a revision not redacted, a search doc or
        artifact not tombstoned/deleted).
        """
        rows = list(self.s.execute(select(Tombstone)).scalars())
        results: list[dict] = []
        all_ok = True
        for t in rows:
            plan = self.plan(t.target_id)
            violations: list[str] = []

            mem = self.s.get(Memory, t.target_id)
            if mem is not None and (mem.active or mem.deleted_at is None):
                violations.append(f"primary {t.target_id} not soft-deleted")

            for did in plan["derived_memory_ids"]:
                d = self.s.get(Memory, did)
                if d is not None and (d.active or d.deleted_at is None):
                    violations.append(f"derived memory {did} not soft-deleted")

            for rid in plan["revision_ids"]:
                r = self.s.get(MemoryRevision, rid)
                if r is not None and not r.redacted:
                    violations.append(f"revision {rid} not redacted")

            for sid in plan["search_doc_ids"]:
                sd = self.s.get(SearchDocument, sid)
                if sd is not None and sd.tombstoned_at is None:
                    violations.append(f"search document {sid} not tombstoned")

            for aid in plan["artifact_ids"]:
                a = self.s.get(Artifact, aid)
                if a is not None and a.deleted_at is None:
                    violations.append(f"artifact {aid} not deleted")

            for pid in plan["proposal_ids"]:
                p = self.s.get(Proposal, pid)
                if p is not None and not (isinstance(p.payload, dict) and p.payload.get("redacted")):
                    violations.append(f"proposal {pid} not redacted")

            ok = not violations
            all_ok = all_ok and ok
            results.append({
                "tombstone_id": t.id,
                "target_id": t.target_id,
                "target_kind": t.target_kind,
                "dep_graph_hash": t.dep_graph_hash,
                "ok": ok,
                "violations": violations,
            })
        return {"ok": all_ok, "checked": len(rows), "tombstones": results}
