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

需求10 (three-tier memory) lives here too, but the *policy* is factored out
into :mod:`find_yourself.services.memory_tiers` so the promotion/demotion rules
can be read and tested without a database. This module owns persistence and
delegates every tier judgement to :class:`MemoryTierPolicy`.

Backward compatibility: :meth:`MemoryService.upsert` and
:meth:`MemoryService.search` keep their original signatures and their original
behaviour. Tier arguments are keyword-only additions with defaults that
reproduce the pre-tier single-scale semantics exactly — an ``upsert`` without a
``tier`` writes a ``medium`` record, and a ``search`` without ``tiers`` returns
every tier exactly as before (minus already-evicted short records, which is the
point of the feature).
"""

from __future__ import annotations

import threading
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    MEMORY_TIERS,
    Memory,
    MemoryRevision,
    MemoryTierSession,
    Message,
    SourceRelation,
)
from ..db.types import utcnow
from .actor import Actor
from .audit import AuditService
from .errors import NotFound, ValidationFailed
from .grant import GrantService
from .hasher import content_hash
from .memory_tiers import (
    MEDIUM_MAX_IDLE_DAYS,
    SHORT_TTL,
    MemoryTierPolicy,
    TierTransition,
)

CROSS_DOMAIN = {"shared"}
SAME_DOMAIN_CATEGORIES = {"self_report", "tool_fact", "assessment_result", "preference"}

#: Tie-break weight when two records match the query equally well. A long
#: profile record is a better answer than a passing remark that shares a keyword,
#: so durability wins the tie. This only ever breaks ties — it never promotes a
#: worse text match above a better one.
TIER_RANK = {"long": 3, "medium": 2, "short": 1}


def timedelta_seconds(seconds: int):
    """``timedelta(seconds=n)`` with a non-negative guard."""
    if seconds < 0:
        raise ValidationFailed(
            "invalid_ttl", "Short-tier ttl_seconds must be >= 0", 422
        )
    return timedelta(seconds=seconds)


def _next_tier(tier: str) -> str | None:
    order = ["short", "medium", "long"]
    i = order.index(tier)
    return order[i + 1] if i + 1 < len(order) else None


def _prev_tier(tier: str) -> str | None:
    order = ["short", "medium", "long"]
    i = order.index(tier)
    return order[i - 1] if i > 0 else None


def _public_hit(hit: dict) -> dict:
    """Drop the internal cache bookkeeping key before a hit leaves the service."""
    return {k: v for k, v in hit.items() if k != "tier_expires_at"}


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
        tier: str = "medium",
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        now=None,
    ) -> Memory:
        """Write a memory, optionally onto a specific retention tier (需求10).

        The first seven parameters are the original contract and are unchanged.
        The tier arguments are additive keyword-only parameters whose defaults
        reproduce the pre-tier behaviour exactly:

        * ``tier`` defaults to ``"medium"`` — cross-session, no hard deadline,
          no automatic expiry. This is what a caller that has never heard of
          tiers gets, so every existing test and route is unaffected.
        * ``session_id`` scopes a short-tier record to the conversation that
          produced it. Required by the DB for ``tier="short"``; ignored (but
          kept for provenance) for the other tiers.
        * ``ttl_seconds`` overrides the default short-tier lifetime.

        Revising an existing record (``memory_id``) may **not** silently change
        its tier: the tier is a retention decision with its own audit trail, so
        moving it goes through :meth:`promote` / :meth:`demote` instead. Asking
        for a different tier on a revision is an explicit error rather than a
        quiet reclassification.
        """
        actor.require_authenticated()
        if not content.strip():
            raise ValidationFailed("empty_content", "Memory content is required")
        if not MemoryTierPolicy.is_valid(tier):
            raise ValidationFailed(
                "unknown_tier", f"Unknown memory tier {tier!r}; expected one of {list(MEMORY_TIERS)}"
            )
        moment = now or utcnow()

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
            if tier == MemoryTierPolicy.short and not session_id:
                raise ValidationFailed(
                    "session_required",
                    "A short-tier memory must name the session that scopes it",
                )
            ttl = timedelta_seconds(ttl_seconds) if ttl_seconds is not None else SHORT_TTL
            row = Memory(
                id=memory_id or uuid4().hex, owner_id=owner_id, domain=domain, category=category,
                content=content, content_hash=chash, active=active, endorsed=False,
                hypothesis_status=hypothesis_status,
                tier=tier,
                session_id=session_id,
                tier_expires_at=MemoryTierPolicy.deadline_for(tier, now=moment, ttl=ttl),
                tier_changed_at=moment,
                last_used_at=moment,
                session_count=1 if session_id else 0,
                reinforcement_count=0,
            )
            self.s.add(row)
            if session_id:
                # Register the originating session as evidence, so that
                # ``session_count`` means the same thing here as it does in
                # reinforce() and the two can never drift apart. Without this the
                # first reinforce() in the same session would count it twice.
                self.s.add(MemoryTierSession(
                    id=uuid4().hex, memory_id=row.id, session_id=session_id,
                    first_seen_at=moment,
                ))
        else:
            if row.domain != domain:
                raise ValidationFailed("domain_immutable", "Create a separately reviewed record to change domain")
            if row.tier != tier:
                raise ValidationFailed(
                    "tier_immutable",
                    "Retention tier is changed via promote()/demote(), not by rewriting the record",
                )
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
                          {"domain": domain, "active": active, "derived_across": derived_across,
                           "tier": row.tier, "session_id": row.session_id})
        return row

    # ------------------------------------------------------------------
    # 需求10 — three-tier retention
    # ------------------------------------------------------------------
    def promote(self, actor: Actor, memory_id: str, *, now=None) -> TierTransition:
        """Move a record up one tier, if — and only if — the gate for that step passes.

        Refusals do not raise: they come back as
        ``TierTransition(changed=False, reason=<slug>)`` so a caller (or the API
        layer) can explain *why* a promotion did not happen instead of guessing.
        The gate itself lives in :meth:`MemoryTierPolicy.can_promote`.
        """
        actor.require_authenticated()
        row = self.s.get(Memory, memory_id)
        if row is None:
            raise NotFound("memory_not_found", "Memory not found")
        moment = now or utcnow()
        allowed, reason = MemoryTierPolicy.can_promote(
            tier=row.tier,
            hypothesis_status=row.hypothesis_status,
            session_count=row.session_count or 0,
            reinforcement_count=row.reinforcement_count or 0,
            endorsed=bool(row.endorsed),
            actor_is_owner=actor.subject_type == "owner",
            active=bool(row.active),
        )
        target = _next_tier(row.tier)
        if not allowed or target is None:
            return TierTransition(row.id, row.tier, row.tier, reason, False)

        previous = row.tier
        row.tier = target
        row.tier_changed_at = moment
        row.last_used_at = moment
        if target == MemoryTierPolicy.short:
            row.tier_expires_at = MemoryTierPolicy.deadline_for(
                MemoryTierPolicy.short, now=moment
            )
        else:
            # Leaving short: the deadline has been served, drop it so a stale
            # one can never evict a medium/long record later.
            row.tier_expires_at = None
        row.version += 1
        self.s.add(MemoryRevision(
            id=uuid4().hex, memory_id=row.id, version=row.version,
            content_hash=row.content_hash, redacted=False,
        ))
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.promoted", row.id,
                          {"from": previous, "to": target, "reason": reason})
        return TierTransition(row.id, previous, target, reason, True)

    def demote(self, actor: Actor, memory_id: str, *, reason: str | None = None, now=None) -> TierTransition:
        """Move a record down one tier.

        Long -> medium is **owner-only**: a profile record the user stated is not
        something a service actor or a sweep may quietly downgrade. Whatever the
        cause, a long record lands in medium and never in the bin.
        """
        actor.require_authenticated()
        row = self.s.get(Memory, memory_id)
        if row is None:
            raise NotFound("memory_not_found", "Memory not found")
        moment = now or utcnow()
        allowed, why = MemoryTierPolicy.can_demote(
            tier=row.tier, actor_is_owner=actor.subject_type == "owner"
        )
        target = _prev_tier(row.tier)
        if not allowed or target is None:
            return TierTransition(row.id, row.tier, row.tier, why, False)

        previous = row.tier
        row.tier = target
        row.tier_changed_at = moment
        if target == MemoryTierPolicy.short:
            row.tier_expires_at = MemoryTierPolicy.deadline_for(
                MemoryTierPolicy.short, now=moment
            )
            # A record that fell back to session scope is no longer carried by
            # several sessions; keeping the old count would let it re-promote on
            # stale evidence.
            row.session_count = 1 if row.session_id else 0
        row.version += 1
        self.s.add(MemoryRevision(
            id=uuid4().hex, memory_id=row.id, version=row.version,
            content_hash=row.content_hash, redacted=False,
        ))
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.demoted", row.id,
                          {"from": previous, "to": target, "reason": why,
                           "caller_reason": reason or ""})
        return TierTransition(row.id, previous, target, why, True)

    def reinforce(self, actor: Actor, memory_id: str, *, session_id: str | None = None,
                  now=None) -> Memory:
        """Record that a memory was re-derived or explicitly reused.

        This is the *evidence* the promotion gates read. ``reinforcement_count``
        grows on every reuse; ``session_count`` grows only when a **new, distinct**
        session touches the record — the same session reinforcing twice is not
        carry-over, and neither is a session that already counted. Distinctness is
        enforced by a unique constraint on
        :class:`~find_yourself.db.models.MemoryTierSession`, not by trusting the
        caller to keep track.
        """
        actor.require_authenticated()
        row = self.s.get(Memory, memory_id)
        if row is None:
            raise NotFound("memory_not_found", "Memory not found")
        moment = now or utcnow()

        if session_id:
            already = self.s.execute(
                select(MemoryTierSession).where(
                    MemoryTierSession.memory_id == row.id,
                    MemoryTierSession.session_id == session_id,
                )
            ).scalar_one_or_none()
            if already is None:
                self.s.add(MemoryTierSession(
                    id=uuid4().hex, memory_id=row.id, session_id=session_id,
                    first_seen_at=moment,
                ))
                row.session_count = (row.session_count or 0) + 1
                row.session_id = session_id

        row.reinforcement_count = (row.reinforcement_count or 0) + 1
        row.last_used_at = moment
        # Using a short-tier record restarts its clock: what expires is the
        # *unused*, so an actively-used record must get a fresh full TTL rather
        # than the remainder of the old one.
        #
        # The exception is a record already past its deadline. Reinforcement is
        # evidence of use, not an amnesty for something the policy already gave
        # up on — such a record gets a deadline of exactly "now", so the next
        # decay() reaps it. Granting it a fresh TTL here would let any caller
        # resurrect an evicted record just by touching it.
        if row.tier == MemoryTierPolicy.short:
            if MemoryTierPolicy.is_evicted(row.tier, row.tier_expires_at, moment):
                row.tier_expires_at = moment
            else:
                row.tier_expires_at = MemoryTierPolicy.deadline_for(
                    MemoryTierPolicy.short, now=moment
                )
        self.s.flush()
        self.invalidate_cache()
        self.audit.append(actor, "memory.reinforced", row.id,
                          {"tier": row.tier, "session_id": session_id or "",
                           "session_count": row.session_count,
                           "reinforcement_count": row.reinforcement_count})
        return row

    def decay(self, actor: Actor, *, owner_id: str | None = None, now=None) -> dict:
        """Run the two automatic retention rules. Never touches the long tier.

        1. **Evict** short records whose deadline has passed (soft delete: the row
           survives for audit, but no read path returns it again).
        2. **Demote** medium records unused for more than
           :data:`MEDIUM_MAX_IDLE_DAYS` back to short.

        Returns a report dict so a scheduled job can log what it did.
        """
        actor.require_authenticated()
        moment = now or utcnow()
        stmt = select(Memory).where(
            Memory.active.is_(True), Memory.deleted_at.is_(None)
        )
        if owner_id:
            stmt = stmt.where(Memory.owner_id == owner_id)
        rows = list(self.s.execute(stmt).scalars())

        evicted: list[str] = []
        decayed: list[str] = []
        for row in rows:
            if MemoryTierPolicy.is_evicted(row.tier, row.tier_expires_at, moment):
                row.deleted_at = moment
                row.active = False
                row.version += 1
                evicted.append(row.id)
                continue
            if MemoryTierPolicy.should_decay(
                tier=row.tier,
                last_used_at=row.last_used_at or row.tier_changed_at or row.created_at,
                now=moment,
            ):
                row.tier = MemoryTierPolicy.short
                row.tier_changed_at = moment
                row.tier_expires_at = MemoryTierPolicy.deadline_for(
                    MemoryTierPolicy.short, now=moment
                )
                row.session_count = 1 if row.session_id else 0
                row.version += 1
                decayed.append(row.id)
        self.s.flush()
        if evicted or decayed:
            self.invalidate_cache()
            self.audit.append(actor, "memory.decay", None,
                              {"evicted": len(evicted), "decayed": len(decayed),
                               "idle_days": MEDIUM_MAX_IDLE_DAYS})
        return {"evicted": evicted, "decayed": decayed,
                "checked": len(rows), "at": moment.isoformat()}

    def revoke_profile(self, actor: Actor, memory_id: str, *, reason: str) -> TierTransition:
        """Owner-only: take a long profile record back down to medium.

        Separate from :meth:`demote` on purpose — this is the one destructive-
        looking operation on the profile tier, so it demands an explicit reason
        and refuses non-owner actors outright.
        """
        actor.require_owner()
        if not reason.strip():
            raise ValidationFailed("reason_required", "Revoking a profile memory requires a reason")
        return self.demote(actor, memory_id, reason=reason)

    def tier_stats(self, actor: Actor, *, owner_id: str) -> dict[str, int]:
        """Count this owner's live memories per tier."""
        actor.require_authenticated()
        counts = {t: 0 for t in MEMORY_TIERS}
        rows = self.s.execute(
            select(Memory).where(
                Memory.owner_id == owner_id,
                Memory.deleted_at.is_(None),
            )
        ).scalars()
        for row in rows:
            if row.tier in counts:
                counts[row.tier] += 1
        return counts

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

    def search(self, consumer_domain: str, query: str, limit: int = 8, *, owner_id: str,
               tiers: list[str] | tuple[str, ...] | None = None,
               session_id: str | None = None,
               include_evicted: bool = False,
               now=None) -> list[dict]:
        """Authorization-first retrieval (BUG-11) with cache invalidation (M04).

        ``owner_id`` is mandatory: memories are never searchable across users —
        domain grants authorize cross-domain visibility for the SAME owner, not
        cross-tenant access.

        需求10 adds three keyword-only arguments, all defaulting to the
        pre-tier behaviour:

        * ``tiers`` — restrict results to these retention tiers. ``None`` (the
          default) means "all tiers", so every pre-existing caller sees exactly
          what it saw before.
        * ``session_id`` — the reading session. Short-tier records are scoped to
          the session that produced them, so passing this **excludes short
          records belonging to other sessions**. This is the cross-tier
          isolation guarantee: a session cannot read another session's
          short-term scratchpad even though the text matches.
        * ``include_evicted`` — return short records whose deadline has passed.
          Defaults to ``False``: eviction takes effect at read time, not only
          when :meth:`decay` eventually runs, so an unswept expired record is
          already invisible.

        Ranking is tier-weighted: on an equal text score, a long profile record
        outranks a medium one, which outranks a session-scoped short one. A
        durable, repeatedly-confirmed fact is a better answer than a passing
        remark that happens to share a keyword.
        """
        if not owner_id:
            raise ValidationFailed("owner_required", "memory.search requires an owner_id")
        if tiers is not None:
            unknown = [t for t in tiers if not MemoryTierPolicy.is_valid(t)]
            if unknown:
                raise ValidationFailed(
                    "unknown_tier",
                    f"Unknown memory tier(s) {unknown!r}; expected from {list(MEMORY_TIERS)}",
                )
        norm_query = query.strip().lower()
        tier_key = tuple(tiers) if tiers is not None else None
        moment = now or utcnow()
        cache_key = (owner_id, consumer_domain, norm_query, limit, self._cache_version,
                     tier_key, session_id, include_evicted)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # Fast check that all cached items are still currently authorized.
                # Eviction is re-checked on every hit as well: a short record's
                # deadline can pass while its search result sits in the cache,
                # and ``moment`` is not part of the cache key (it must not be —
                # keying on time would make the cache useless). Serving a stale
                # hit here would resurrect an evicted record, so the deadline is
                # stored alongside the result and re-tested below.
                all_valid = True
                for item in cached:
                    if not self.grants.is_authorized(
                        record_domain=item["domain"], record_id=item["id"], consumer_domain=consumer_domain
                    ):
                        all_valid = False
                        break
                    if not include_evicted and MemoryTierPolicy.is_evicted(
                        item["tier"], item.get("tier_expires_at"), moment
                    ):
                        all_valid = False
                        break
                if all_valid:
                    return [_public_hit(x) for x in cached]

            # Candidate set: only this owner's active, non-deleted memories.
            candidates = list(self.s.execute(
                select(Memory).where(Memory.active.is_(True), Memory.deleted_at.is_(None),
                                     Memory.owner_id == owner_id)
            ).scalars())

            scored: list[tuple[int, int, str, Memory]] = []
            terms = [t.lower() for t in query.split() if t.strip()]
            for m in candidates:
                # Authorization predicate applied BEFORE ranking.
                if not self.grants.is_authorized(
                    record_domain=m.domain, record_id=m.id, consumer_domain=consumer_domain
                ):
                    continue
                if tier_key is not None and m.tier not in tier_key:
                    continue
                if not include_evicted and MemoryTierPolicy.is_evicted(
                    m.tier, m.tier_expires_at, moment
                ):
                    continue
                # Session scoping: a short record is private to its own session.
                if (session_id is not None and m.tier == MemoryTierPolicy.short
                        and m.session_id != session_id):
                    continue
                text = m.content.lower()
                score = sum(1 for t in terms if t in text)
                if score or not query.strip():
                    scored.append((score, TIER_RANK.get(m.tier, 0), m.id, m))
            # Text score first, then tier weight, then id for a stable order.
            scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
            out = []
            for score, _rank, _mid, m in scored[:limit]:
                out.append({
                    "id": m.id, "version": m.version, "kind": "memory", "domain": m.domain,
                    "category": m.category, "content": m.content, "hypothesis_status": m.hypothesis_status,
                    "score": score, "tier": m.tier,
                    # Internal only — stripped before the caller sees it.
                    "tier_expires_at": m.tier_expires_at,
                })
            self._cache[cache_key] = [dict(x) for x in out]
            return [_public_hit(x) for x in out]
