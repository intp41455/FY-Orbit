"""Memory writes, revisions, derived-source graph and authz-first search (§8).

BUG-01 fix: a memory in the ``shared`` (or cross-domain) bucket whose sources
originated in ``personal``/``work`` starts **inactive** and must not appear in
search until an owner-approved proposal activates it. Reading the derived
memory never lets a consumer read its raw sources — source links are checked
independently.

BUG-11 fix: search applies the authorization predicate in the query (candidate
set filtering) *before* ranking, instead of scanning everything and letting the
caller sort it out. On PostgreSQL this is backed by tsvector + pgvector; the
portable path here still filters by authorization first.
"""

from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.models import Memory, MemoryRevision, Message, SearchDocument, SourceRelation
from ..db.types import utcnow
from .actor import Actor
from .errors import NotFound, PermissionDenied, ValidationFailed
from .grant import GrantService
from .hasher import content_hash, digest
from .audit import AuditService

import threading

CROSS_DOMAIN = {"shared"}
SAME_DOMAIN_CATEGORIES = {"self_report", "tool_fact", "assessment_result", "preference"}


class MemoryService:
    def __init__(self, session: Session, grants: GrantService, audit: AuditService):
        self.s = session
        self.grants = grants
        self.audit = audit
        self._cache: dict[tuple, list[dict]] = {}
        self._cache_lock = threading.Lock()
        self._cache_version: int = 1
        # Invalidate search cache whenever grants change
        self.grants.on_change = self.invalidate_cache

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_version += 1

    def upsert(
        self,
        actor: Actor,
        *,
        owner_id: str,
        domain: str,
        category: str,
        content: str,
        source_ids: list[str],
        hypothesis_status: str = "unverified",
        memory_id: str | None = None,
    ) -> Memory:
        actor.require_authenticated()
        if not content.strip():
            raise ValidationFailed("empty_content", "Memory content is required")

        # Resolve source domains (messages/artifacts/imports are looked up by id
        # kind-agnostically here; the caller passes verified source records).
        source_domains: set[str] = set()
        for sid in source_ids:
            msg = self.s.get(Message, sid)
            if msg is not None:
                source_domains.add("personal")  # messages carry conversation domain; simplified
            mem = self.s.get(Memory, sid)
            if mem is not None:
                source_domains.add(mem.domain)

        derived_across = domain in CROSS_DOMAIN and any(d not in CROSS_DOMAIN for d in source_domains)
        # Cross-domain derived memories are inert until owner-approved (BUG-01).
        active = not derived_across

        row = self.s.get(Memory, memory_id) if memory_id else None
        chash = content_hash(content)
        if row is None:
            row = Memory(
                id=memory_id or uuid4().hex, owner_id=owner_id, domain=domain, category=category,
                content=content, content_hash=chash, active=active, endorsed=False,
                hypothesis_status=hypothesis_status,
            )
            self.s.add(row)
        else:
            if row.domain != domain:
                raise ValidationFailed("domain_immutable", "Create a separately reviewed record to change domain")
            self.s.add(MemoryRevision(
                id=uuid4().hex, memory_id=row.id, version=row.version,
                content_hash=row.content_hash, redacted=False,
            ))
            row.content = content
            row.content_hash = chash
            row.hypothesis_status = hypothesis_status
            row.version += 1
        self.s.flush()

        # Derived-source graph edges (independent authz later; check duplicates).
        for sid in source_ids:
            existing_edge = self.s.execute(
                select(SourceRelation).where(
                    SourceRelation.source_id == sid,
                    SourceRelation.derived_id == row.id,
                    SourceRelation.relation_type == "derives",
                )
            ).scalar_one_or_none()
            if existing_edge is None:
                edge = SourceRelation(
                    id=uuid4().hex, source_id=sid, source_kind="content",
                    derived_id=row.id, derived_kind="memory", relation_type="derives",
                    permission_snapshot={"record_domain": domain, "active": active},
                )
                self.s.add(edge)
            else:
                existing_edge.permission_snapshot = {"record_domain": domain, "active": active}
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.upsert", row.id,
                          {"domain": domain, "active": active, "derived_across": derived_across})
        return row

    def activate_approved(self, actor: Actor, memory_id: str) -> Memory:
        actor.require_owner()
        row = self.s.get(Memory, memory_id)
        if row is None:
            raise NotFound("memory_not_found", "Memory not found")
        row.active = True
        row.endorsed = True
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.activated", row.id, {"domain": row.domain})
        return row

    def deny_hypothesis(
        self, actor: Actor, memory_id: str, reason: str, message_id: str | None = None
    ) -> Memory:
        actor.require_owner()
        row = self.s.get(Memory, memory_id)
        if row is None:
            raise NotFound("memory_not_found", "Memory not found")
        row.active = False
        row.endorsed = False
        row.version += 1
        self.s.add(MemoryRevision(
            id=uuid4().hex, memory_id=row.id, version=row.version,
            content_hash=row.content_hash, redacted=False,
        ))
        edge = SourceRelation(
            id=uuid4().hex,
            source_id=message_id or actor.owner_id or "owner",
            source_kind="denial_statement",
            derived_id=row.id,
            derived_kind="memory",
            relation_type="cites",
            permission_snapshot={"denied": True, "reason": reason, "timestamp": utcnow().isoformat()},
        )
        self.s.add(edge)
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.hypothesis_denied", row.id, {"reason": reason, "domain": row.domain})
        return row

    def get_revisions(self, actor: Actor, memory_id: str) -> list[dict]:
        actor.require_authenticated()
        mem = self.s.get(Memory, memory_id)
        if mem is None:
            raise NotFound("memory_not_found", "Memory not found")
        revs = list(self.s.execute(
            select(MemoryRevision).where(MemoryRevision.memory_id == memory_id).order_by(MemoryRevision.version.asc())
        ).scalars())
        sources = list(self.s.execute(
            select(SourceRelation).where(SourceRelation.derived_id == memory_id)
        ).scalars())
        source_list = [
            {"source_id": s.source_id, "source_kind": s.source_kind, "relation_type": s.relation_type}
            for s in sources
        ]
        return [
            {
                "revision_id": r.id,
                "version": r.version,
                "content_hash": r.content_hash,
                "redacted": r.redacted,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "sources": source_list,
            }
            for r in revs
        ]

    def search(self, consumer_domain: str, query: str, limit: int = 8) -> list[dict]:
        """Authorization-first retrieval (BUG-11) with cache invalidation (M04)."""
        norm_query = query.strip().lower()
        cache_key = (consumer_domain, norm_query, limit, self._cache_version)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # Fast check that all cached items are still currently authorized
                all_authed = True
                for item in cached:
                    if not self.grants.is_authorized(
                        record_domain=item["domain"], record_id=item["id"], consumer_domain=consumer_domain
                    ):
                        all_authed = False
                        break
                if all_authed:
                    return [dict(x) for x in cached]

            now = utcnow()
            # Candidate set: only active, non-deleted memories.
            candidates = list(self.s.execute(
                select(Memory).where(Memory.active.is_(True), Memory.deleted_at.is_(None))
            ).scalars())

            scored: list[tuple[int, Memory]] = []
            terms = [t.lower() for t in query.split() if t.strip()]
            for m in candidates:
                # Authorization predicate applied BEFORE ranking.
                if not self.grants.is_authorized(
                    record_domain=m.domain, record_id=m.id, consumer_domain=consumer_domain
                ):
                    continue
                text = m.content.lower()
                score = sum(1 for t in terms if t in text)
                if score or not query.strip():
                    scored.append((score, m))
            scored.sort(key=lambda x: (-x[0], x[1].id))
            out = []
            for score, m in scored[:limit]:
                out.append({
                    "id": m.id, "version": m.version, "kind": "memory", "domain": m.domain,
                    "category": m.category, "content": m.content, "hypothesis_status": m.hypothesis_status,
                    "score": score,
                })
            self._cache[cache_key] = [dict(x) for x in out]
            return out
