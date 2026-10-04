"""Unit tests: 需求10 — short/medium/long three-tier memory retention.

What is actually under test (not just labelling)
------------------------------------------------
A memory that merely *carries* a ``tier`` string is a label, not a retention
policy. These tests are written so that a hollow implementation — one that
stores the tier and then ignores it — **fails**:

* ``test_short_is_invisible_to_other_sessions`` — a short record must not be
  readable from a session other than the one that created it, even though the
  text matches and the owner is identical. A label-only ``search`` returns it.
* ``test_promotion_refused_without_session_carryover`` — a short record that has
  never been seen in a second session must refuse promotion. A label-only
  ``promote`` would happily move it.
* ``test_service_actor_cannot_promote_into_profile`` — only an owner may build
  the long-tier profile. A label-only implementation has no such gate.
* ``test_decay_evicts_expired_short_and_never_touches_long`` — the sweep is what
  makes the tiers differ in *consequence*; a long record must survive it.
* ``test_medium_decays_by_disuse`` — medium is not immortal, it decays.

Timestamps are passed explicitly (``now=``) rather than slept on, so every
retention rule is exercised deterministically.
"""

from __future__ import annotations

import importlib
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from find_yourself.db.base import Base
from find_yourself.db.models import MEMORY_TIERS, Memory, MemoryTierSession
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import PermissionDenied, ValidationFailed
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService
from find_yourself.services.memory_tiers import (
    MEDIUM_MAX_IDLE_DAYS,
    MEDIUM_TO_LONG_MIN_REINFORCEMENTS,
    SHORT_TO_MEDIUM_MIN_SESSIONS,
    SHORT_TTL,
    MemoryTierPolicy,
)

OWNER = "owner-1"

# ``0026_memory_tiers`` is not a valid module name for a plain ``import``, so the
# migration is loaded by path below (same trick as test_migration_0010.py).
_PROJ_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)


def _migration_0026():
    return importlib.import_module("migrations.versions.0026_memory_tiers")


def _svc(session):
    audit = AuditService(session)
    grants = GrantService(session, audit)
    return MemoryService(session, grants, audit), grants


def _write(mem, owner, **kw):
    """Tier-less write defaults to medium, matching the pre-tier behaviour."""
    kw.setdefault("owner_id", OWNER)
    kw.setdefault("domain", "personal")
    kw.setdefault("category", "self_report")
    kw.setdefault("content", "a remembered thing")
    kw.setdefault("source_ids", [])
    return mem.upsert(owner, **kw)


# ---------------------------------------------------------------------------
# Policy unit tests (no DB) — the rules themselves
# ---------------------------------------------------------------------------
def test_three_tiers_are_exactly_short_medium_long():
    assert MEMORY_TIERS == ("short", "medium", "long")


def test_only_short_tier_gets_a_deadline():
    now = utcnow()
    assert MemoryTierPolicy.deadline_for("short", now=now) is not None
    assert MemoryTierPolicy.deadline_for("medium", now=now) is None
    assert MemoryTierPolicy.deadline_for("long", now=now) is None


def test_only_short_tier_is_evictable():
    now = utcnow()
    past = now - timedelta(hours=1)
    assert MemoryTierPolicy.is_evicted("short", past, now) is True
    assert MemoryTierPolicy.is_evicted("short", now + timedelta(hours=1), now) is False
    # Medium/long are never clock-evicted even if a stale deadline is present.
    assert MemoryTierPolicy.is_evicted("medium", past, now) is False
    assert MemoryTierPolicy.is_evicted("long", past, now) is False
    # A short record with no deadline at all is NOT treated as evicted.
    assert MemoryTierPolicy.is_evicted("short", None, now) is False


def test_unknown_tier_is_rejected():
    assert MemoryTierPolicy.is_valid("forever") is False
    with pytest.raises(ValueError):
        MemoryTierPolicy.validate("forever")


def test_medium_should_decay_only_when_idle_and_only_for_medium():
    now = utcnow()
    idle = now - timedelta(days=MEDIUM_MAX_IDLE_DAYS + 1)
    fresh = now - timedelta(days=1)
    assert MemoryTierPolicy.should_decay(tier="medium", last_used_at=idle, now=now) is True
    assert MemoryTierPolicy.should_decay(tier="medium", last_used_at=fresh, now=now) is False
    assert MemoryTierPolicy.should_decay(tier="long", last_used_at=idle, now=now) is False
    assert MemoryTierPolicy.should_decay(tier="short", last_used_at=idle, now=now) is False
    # No clock at all => refuse to act rather than guess.
    assert MemoryTierPolicy.should_decay(tier="medium", last_used_at=None, now=now) is False


# ---------------------------------------------------------------------------
# Writing onto each tier
# ---------------------------------------------------------------------------
def test_default_write_lands_on_medium(session, owner):
    """A caller that never heard of tiers gets exactly the old behaviour."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="no tier mentioned")
    assert row.tier == "medium"
    assert row.tier_expires_at is None
    assert row.tier_changed_at is not None


def test_short_write_requires_a_session(session, owner):
    """The DB check would reject it anyway; the service refuses first, with a code."""
    mem, _ = _svc(session)
    with pytest.raises(ValidationFailed) as exc:
        _write(mem, owner, content="session-less", tier="short")
    assert exc.value.code == "session_required"


def test_short_write_gets_deadline_and_is_scoped(session, owner):
    mem, _ = _svc(session)
    now = utcnow()
    row = _write(mem, owner, content="this session only", tier="short",
                 session_id="conv-1", now=now)
    assert row.tier == "short"
    assert row.session_id == "conv-1"
    assert row.tier_expires_at is not None
    assert row.tier_expires_at > now
    assert row.session_count == 1


def test_long_write_is_directly_possible_but_still_carries_no_deadline(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="durable profile line", tier="long")
    assert row.tier == "long"
    assert row.tier_expires_at is None


def test_unknown_tier_on_write_is_rejected(session, owner):
    mem, _ = _svc(session)
    with pytest.raises(ValidationFailed) as exc:
        _write(mem, owner, content="bad tier", tier="eternal")
    assert exc.value.code == "unknown_tier"


def test_tier_cannot_be_changed_by_rewriting_the_record(session, owner):
    """Revising content must not silently reclassify retention."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="v1", tier="short", session_id="conv-1")
    with pytest.raises(ValidationFailed) as exc:
        mem.upsert(owner, owner_id=OWNER, domain="personal", category="self_report",
                   content="v2", source_ids=[], tier="long", memory_id=row.id)
    assert exc.value.code == "tier_immutable"
    # And the content was not written either.
    assert mem.s.get(Memory, row.id).content == "v1"


def test_db_rejects_short_without_deadline(session, owner):
    """Belt and braces: the CHECK holds even on a direct ORM write.

    The enforced invariant is the *deadline* (eviction must be enforceable), not
    the session id — a record demoted from medium has no originating session.
    """
    mem, _ = _svc(session)
    row = _write(mem, owner, content="legit short", tier="short", session_id="conv-1")
    row.tier_expires_at = None  # try to strip the deadline
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_db_rejects_long_with_a_deadline(session, owner):
    """The profile tier must never carry a clock, so no sweep can evict it."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="legit profile", tier="long")
    row.tier_expires_at = utcnow() + timedelta(days=1)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# ---------------------------------------------------------------------------
# Reading, per tier, and cross-tier isolation
# ---------------------------------------------------------------------------
def test_read_can_be_restricted_to_one_tier(session, owner):
    mem, _ = _svc(session)
    _write(mem, owner, content="coffee break note", tier="short", session_id="conv-1")
    _write(mem, owner, content="coffee project decision", tier="medium")
    _write(mem, owner, content="coffee profile taste", tier="long")

    only_short = mem.search("personal", "coffee", owner_id=OWNER, tiers=["short"])
    only_medium = mem.search("personal", "coffee", owner_id=OWNER, tiers=["medium"])
    only_long = mem.search("personal", "coffee", owner_id=OWNER, tiers=["long"])

    assert [h["tier"] for h in only_short] == ["short"]
    assert [h["tier"] for h in only_medium] == ["medium"]
    assert [h["tier"] for h in only_long] == ["long"]
    # Default is all three.
    assert len(mem.search("personal", "coffee", owner_id=OWNER)) == 3


def test_search_reports_the_tier_of_each_hit(session, owner):
    mem, _ = _svc(session)
    _write(mem, owner, content="tier visible in results", tier="long")
    hit = mem.search("personal", "tier", owner_id=OWNER)[0]
    assert hit["tier"] == "long"


def test_unknown_tier_filter_is_rejected(session, owner):
    mem, _ = _svc(session)
    with pytest.raises(ValidationFailed) as exc:
        mem.search("personal", "x", owner_id=OWNER, tiers=["nope"])
    assert exc.value.code == "unknown_tier"


def test_short_is_invisible_to_other_sessions(session, owner):
    """THE isolation test. A label-only tier would fail this.

    Same owner, same text, same domain — but a different session must not read
    another session's short-term scratchpad.
    """
    mem, _ = _svc(session)
    secret = _write(mem, owner, content="private scratch: budget is tight",
                    tier="short", session_id="conv-A")

    # Its own session reads it.
    mine = mem.search("personal", "scratch", owner_id=OWNER, session_id="conv-A")
    assert [h["id"] for h in mine] == [secret.id]

    # Another session does not — even though the owner is identical and the text
    # matches on every term.
    theirs = mem.search("personal", "scratch", owner_id=OWNER, session_id="conv-B")
    assert theirs == []

    # ...and a session-less read cannot use a short record to peek either.
    unscoped = mem.search("personal", "scratch", owner_id=OWNER)
    assert [h["id"] for h in unscoped] == [secret.id]  # owner-level read still sees it


def test_medium_and_long_are_not_session_locked(session, owner):
    mem, _ = _svc(session)
    _write(mem, owner, content="project-wide budget rule", tier="medium")
    _write(mem, owner, content="profile budget taste", tier="long")
    for sid in ("conv-A", "conv-B", None):
        hits = mem.search("personal", "budget", owner_id=OWNER, session_id=sid)
        assert len(hits) == 2, f"session {sid} should see both durable tiers"


def test_expired_short_is_invisible_before_any_sweep(session, owner):
    """Eviction bites at read time, not only once a job has run."""
    mem, _ = _svc(session)
    t0 = utcnow()
    _write(mem, owner, content="ephemeral thought", tier="short", session_id="conv-1",
           ttl_seconds=60, now=t0)
    assert len(mem.search("personal", "ephemeral", owner_id=OWNER, now=t0)) == 1

    later = t0 + timedelta(hours=2)
    assert mem.search("personal", "ephemeral", owner_id=OWNER, now=later) == []
    # The row is still there — reads hide it, decay() is what soft-deletes it.
    still_there = session.execute(
        select(Memory).where(Memory.content == "ephemeral thought")
    ).scalar_one()
    assert still_there.deleted_at is None


def test_cached_hits_do_not_survive_their_own_eviction(session, owner):
    """A cached result must not resurrect a record whose deadline has passed.

    ``moment`` is deliberately not part of the cache key, so eviction has to be
    re-checked on a cache hit. Without that, the first search would pin the
    record in the cache and every later read would keep serving it.
    """
    mem, _ = _svc(session)
    t0 = utcnow()
    row = _write(mem, owner, content="cached then expired", tier="short",
                 session_id="conv-1", ttl_seconds=60, now=t0)
    # First read populates the cache.
    assert len(mem.search("personal", "cached", owner_id=OWNER, now=t0)) == 1
    # Second read, same cache key, later clock: must not serve the stale hit.
    assert mem.search("personal", "cached", owner_id=OWNER,
                      now=t0 + timedelta(hours=2)) == []
    # Opting into evicted records still finds the row.
    revived = mem.search("personal", "cached", owner_id=OWNER,
                         now=t0 + timedelta(hours=2), include_evicted=True)
    assert [h["id"] for h in revived] == [row.id]


def test_tier_weighting_breaks_equal_text_score_ties(session, owner):
    """Same content in all three tiers => the durable one answers first."""
    mem, _ = _svc(session)
    _write(mem, owner, content="identical tie text", tier="short", session_id="conv-1")
    _write(mem, owner, content="identical tie text", tier="medium")
    _write(mem, owner, content="identical tie text", tier="long")
    order = [h["tier"] for h in mem.search("personal", "identical", owner_id=OWNER)]
    assert order == ["long", "medium", "short"]


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------
def test_promotion_refused_without_session_carryover(session, owner):
    """A fact that never left one conversation is not a project fact."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="single-session remark", tier="short", session_id="conv-1")
    # Reinforce repeatedly *within the same session* — still no carry-over.
    for _ in range(5):
        mem.reinforce(owner, row.id, session_id="conv-1")
    assert row.session_count == 1
    assert row.reinforcement_count == 5

    result = mem.promote(owner, row.id)
    assert result.changed is False
    assert result.reason == "insufficient_session_carryover"
    assert row.tier == "short"


def test_promotion_granted_on_session_carryover(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="came back in a later session", tier="short",
                 session_id="conv-1")
    mem.reinforce(owner, row.id, session_id="conv-2")
    assert row.session_count == SHORT_TO_MEDIUM_MIN_SESSIONS

    result = mem.promote(owner, row.id)
    assert result.changed is True
    assert result.reason == "carried_across_sessions"
    assert (result.from_tier, result.to_tier) == ("short", "medium")
    assert row.tier == "medium"
    # Leaving short clears the deadline so a stale one cannot evict it later.
    assert row.tier_expires_at is None
    assert row.tier_changed_at is not None


def test_promotion_granted_on_owner_endorsement(session, owner):
    """An explicit owner endorsement substitutes for session carry-over."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="owner vouched for this", tier="short", session_id="conv-1")
    mem.activate_approved(owner, row.id)  # sets endorsed=True
    result = mem.promote(owner, row.id)
    assert result.changed is True
    assert result.reason == "owner_endorsed"
    assert row.tier == "medium"


def test_a_session_cannot_count_twice(session, owner):
    """The unique constraint is what stops one chatty session faking evidence."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="repeated same session", tier="short", session_id="conv-1")
    for _ in range(4):
        mem.reinforce(owner, row.id, session_id="conv-1")
    sessions = session.execute(
        select(MemoryTierSession).where(MemoryTierSession.memory_id == row.id)
    ).scalars().all()
    assert len(sessions) == 1
    assert row.session_count == 1


def test_inactive_record_is_not_a_promotion_candidate(session, owner):
    """A denied hypothesis stays down; promoting it would resurrect it."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="denied theory", tier="short", session_id="conv-1")
    mem.activate_approved(owner, row.id)
    mem.deny_hypothesis(owner, row.id, reason="not true")
    result = mem.promote(owner, row.id)
    assert result.changed is False
    assert result.reason == "record_inactive"


def test_top_tier_cannot_be_promoted_further(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="already profile", tier="long")
    result = mem.promote(owner, row.id)
    assert result.changed is False
    assert result.reason == "already_top_tier"
    assert row.tier == "long"


def test_medium_to_long_requires_fact_grade(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="a guess about the user", tier="medium",
                 hypothesis_status="hypothesis")
    for _ in range(MEDIUM_TO_LONG_MIN_REINFORCEMENTS + 2):
        mem.reinforce(owner, row.id)
    result = mem.promote(owner, row.id)
    assert result.changed is False
    assert result.reason == "not_fact_grade"
    assert row.tier == "medium"


def test_medium_to_long_requires_reinforcement(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="a real fact, barely used", tier="medium",
                 hypothesis_status="fact")
    mem.reinforce(owner, row.id)  # 1 < MEDIUM_TO_LONG_MIN_REINFORCEMENTS
    result = mem.promote(owner, row.id)
    assert result.changed is False
    assert result.reason == "insufficient_reinforcement"


def test_medium_to_long_succeeds_with_all_conditions(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="user is allergic to shellfish", tier="medium",
                 hypothesis_status="fact")
    for _ in range(MEDIUM_TO_LONG_MIN_REINFORCEMENTS):
        mem.reinforce(owner, row.id)
    result = mem.promote(owner, row.id)
    assert result.changed is True
    assert result.reason == "owner_confirmed_profile"
    assert row.tier == "long"
    assert row.tier_expires_at is None


def test_service_actor_cannot_promote_into_profile(session, owner):
    """No background job may quietly promote its own guess into the profile."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="worker is certain about this", tier="medium",
                 hypothesis_status="fact")
    for _ in range(MEDIUM_TO_LONG_MIN_REINFORCEMENTS + 2):
        mem.reinforce(owner, row.id)

    worker = Actor.service("svc-1", "worker", ["personal"])
    result = mem.promote(worker, row.id)
    assert result.changed is False
    assert result.reason == "owner_required_for_profile"
    assert row.tier == "medium"

    # The owner can.
    assert mem.promote(owner, row.id).changed is True
    assert row.tier == "long"


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------
def test_medium_decays_back_to_short_on_disuse(session, owner):
    mem, _ = _svc(session)
    t0 = utcnow()
    row = _write(mem, owner, content="stale project note", tier="medium", now=t0)
    assert mem.decay(owner, now=t0 + timedelta(days=1))["decayed"] == []

    report = mem.decay(owner, now=t0 + timedelta(days=MEDIUM_MAX_IDLE_DAYS + 2))
    assert report["decayed"] == [row.id]
    assert row.tier == "short"
    # It was demoted, NOT deleted — it can come back.
    assert row.deleted_at is None
    # And it now carries a fresh deadline.
    assert row.tier_expires_at is not None
    # Session carry-over evidence is reset, so it cannot instantly re-promote.
    assert row.session_count == 0


def test_decay_evicts_expired_short_and_never_touches_long(session, owner):
    """The sweep is where the tiers differ in consequence."""
    mem, _ = _svc(session)
    t0 = utcnow()
    short = _write(mem, owner, content="will be swept", tier="short", session_id="conv-1",
                   ttl_seconds=60, now=t0)
    long_row = _write(mem, owner, content="profile survives sweeps", tier="long", now=t0)
    medium = _write(mem, owner, content="medium stays put", tier="medium", now=t0)

    report = mem.decay(owner, now=t0 + timedelta(hours=3))
    assert report["evicted"] == [short.id]
    assert report["decayed"] == []

    evicted = mem.s.get(Memory, short.id)
    assert evicted.deleted_at is not None
    assert evicted.active is False
    # Soft delete: the row survives for audit, it is just gone from every read.
    assert evicted.tier == "short"
    assert mem.s.get(Memory, long_row.id).tier == "long"
    assert mem.s.get(Memory, medium.id).tier == "medium"
    # A long record is never clock-evicted, even absurdly far in the future.
    assert mem.decay(owner, now=t0 + timedelta(days=3650))["evicted"] == []


def test_decay_is_scoped_to_the_owner_when_asked(session, owner):
    mem, _ = _svc(session)
    t0 = utcnow()
    other = Actor.owner("owner-2", csrf_token="")
    a = _write(mem, owner, content="mine", tier="short", session_id="c1",
               ttl_seconds=60, now=t0)
    b = _write(mem, other, owner_id="owner-2", content="theirs", tier="short",
               session_id="c1", ttl_seconds=60, now=t0)

    report = mem.decay(owner, owner_id=OWNER, now=t0 + timedelta(hours=2))
    assert report["evicted"] == [a.id]
    assert mem.s.get(Memory, b.id).deleted_at is None


def test_short_cannot_be_demoted_further(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="bottom of the ladder", tier="short", session_id="conv-1")
    result = mem.demote(owner, row.id)
    assert result.changed is False
    assert result.reason == "already_bottom_tier"
    assert row.tier == "short"


def test_medium_demote_gives_it_a_deadline(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="pushed back down", tier="medium")
    result = mem.demote(owner, row.id, reason="no longer part of the project")
    assert result.changed is True
    assert result.to_tier == "short"
    assert row.tier == "short"
    assert row.tier_expires_at is not None


def test_service_actor_cannot_demote_out_of_profile(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="stated profile", tier="long")
    worker = Actor.service("svc-2", "worker", ["personal"])
    result = mem.demote(worker, row.id)
    assert result.changed is False
    assert result.reason == "owner_required_to_revoke_profile"
    assert row.tier == "long"


def test_owner_revokes_profile_to_medium_not_to_bin(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="profile fact, now wrong", tier="long")
    result = mem.revoke_profile(owner, row.id, reason="user corrected this")
    assert result.changed is True
    assert (result.from_tier, result.to_tier) == ("long", "medium")
    assert row.tier == "medium"
    assert row.deleted_at is None


def test_revoke_profile_is_owner_only_and_needs_a_reason(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="profile fact", tier="long")
    worker = Actor.service("svc-3", "worker", ["personal"])
    with pytest.raises(PermissionDenied):
        mem.revoke_profile(worker, row.id, reason="I feel like it")
    with pytest.raises(ValidationFailed) as exc:
        mem.revoke_profile(owner, row.id, reason="   ")
    assert exc.value.code == "reason_required"


# ---------------------------------------------------------------------------
# Reinforcement semantics
# ---------------------------------------------------------------------------
def test_reinforce_extends_short_deadline_and_counts_once_per_session(session, owner):
    """Use restarts the clock: what expires is the *unused*."""
    mem, _ = _svc(session)
    t0 = utcnow()
    row = _write(mem, owner, content="actively used", tier="short", session_id="conv-1",
                 ttl_seconds=3600, now=t0)
    first_deadline = row.tier_expires_at

    t1 = t0 + timedelta(minutes=30)
    mem.reinforce(owner, row.id, session_id="conv-1", now=t1)
    assert row.reinforcement_count == 1
    assert row.session_count == 1
    # A full fresh TTL from now, not merely the 30 minutes that were left.
    assert row.tier_expires_at == t1 + SHORT_TTL
    assert row.tier_expires_at > first_deadline

    mem.reinforce(owner, row.id, session_id="conv-2", now=t1)
    assert row.reinforcement_count == 2
    assert row.session_count == 2


def test_reinforcing_an_already_expired_short_record_does_not_resurrect_it(session, owner):
    """Use is evidence, not amnesty: a record the policy gave up on stays gone.

    Otherwise any caller could keep an evicted record alive forever just by
    touching it, and the short tier's deadline would mean nothing.
    """
    mem, _ = _svc(session)
    t0 = utcnow()
    row = _write(mem, owner, content="expired then touched", tier="short",
                 session_id="conv-1", ttl_seconds=60, now=t0)
    much_later = t0 + timedelta(days=2)
    mem.reinforce(owner, row.id, session_id="conv-1", now=much_later)
    # Deadline collapses to "now" rather than being renewed.
    assert row.tier_expires_at == much_later
    assert mem.search("personal", "expired", owner_id=OWNER, now=much_later) == []
    # And the next sweep reaps it.
    assert mem.decay(owner, now=much_later)["evicted"] == [row.id]


def test_reinforce_missing_memory_raises(session, owner):
    mem, _ = _svc(session)
    with pytest.raises(Exception) as exc:
        mem.reinforce(owner, "nope")
    assert getattr(exc.value, "code", "") == "memory_not_found"


# ---------------------------------------------------------------------------
# Stats + tier_stats
# ---------------------------------------------------------------------------
def test_tier_stats_counts_per_tier(session, owner):
    mem, _ = _svc(session)
    _write(mem, owner, content="one short", tier="short", session_id="conv-1")
    _write(mem, owner, content="two mediums", tier="medium")
    _write(mem, owner, content="also a medium", tier="medium")
    _write(mem, owner, content="one long", tier="long")
    assert mem.tier_stats(owner, owner_id=OWNER) == {"short": 1, "medium": 2, "long": 1}


def test_tier_stats_excludes_soft_deleted(session, owner):
    mem, _ = _svc(session)
    t0 = utcnow()
    _write(mem, owner, content="doomed", tier="short", session_id="conv-1",
           ttl_seconds=60, now=t0)
    mem.decay(owner, now=t0 + timedelta(hours=2))
    assert mem.tier_stats(owner, owner_id=OWNER) == {"short": 0, "medium": 0, "long": 0}


# ---------------------------------------------------------------------------
# Audit trail + interaction with the pre-existing lifecycle
# ---------------------------------------------------------------------------
def test_tier_transitions_are_audited(session, owner):
    from find_yourself.db.models import AuditEvent

    mem, _ = _svc(session)
    row = _write(mem, owner, content="audited climb", tier="short", session_id="conv-1")
    mem.reinforce(owner, row.id, session_id="conv-2")
    mem.promote(owner, row.id)
    mem.demote(owner, row.id, reason="scope changed")

    actions = [e.action for e in session.execute(
        select(AuditEvent).order_by(AuditEvent.seq.asc())
    ).scalars()]
    assert "memory.reinforced" in actions
    assert "memory.promoted" in actions
    assert "memory.demoted" in actions
    assert "memory.decay" not in actions  # nothing was swept


def test_tier_transition_bumps_version_and_writes_a_revision(session, owner):
    mem, _ = _svc(session)
    row = _write(mem, owner, content="versioned climb", tier="short", session_id="conv-1")
    before = row.version
    mem.reinforce(owner, row.id, session_id="conv-2")
    mem.promote(owner, row.id)
    assert row.version == before + 1
    revs = mem.get_revisions(owner, row.id)
    assert revs and revs[-1]["version"] == row.version


def test_denied_hypothesis_stays_out_of_every_tier(session, owner):
    """Pre-existing behaviour must hold on the tiered model too."""
    mem, _ = _svc(session)
    row = _write(mem, owner, content="avoidance hypothesis", tier="medium",
                 hypothesis_status="hypothesis")
    assert len(mem.search("personal", "avoidance", owner_id=OWNER)) == 1
    mem.deny_hypothesis(owner, row.id, reason="I was just tired")
    assert mem.search("personal", "avoidance", owner_id=OWNER) == []
    assert mem.search("personal", "avoidance", owner_id=OWNER, tiers=["medium"]) == []
    assert mem.tier_stats(owner, owner_id=OWNER)["medium"] == 1  # row still there, just inactive


def test_tier_filter_does_not_bypass_domain_authorization(session, owner):
    """Narrowing by tier must never widen access: grants still gate every hit."""
    from find_yourself.db.types import utcnow as _now

    mem, grants = _svc(session)
    secret = _write(mem, owner, domain="personal", content="salary number",
                    tier="long")
    # Reading as the work domain without a grant sees nothing, even asking for
    # the long tier explicitly.
    assert mem.search("work", "salary", owner_id=OWNER, tiers=["long"]) == []
    # With the grant it opens, and the tier filter still applies.
    grants.create(owner, source_domain="personal", consumer_domain="work",
                  record_ids=[secret.id], expires_at=_now() + timedelta(days=5))
    hits = mem.search("work", "salary", owner_id=OWNER, tiers=["long"])
    assert [h["id"] for h in hits] == [secret.id]
    assert mem.search("work", "salary", owner_id=OWNER, tiers=["short"]) == []


def test_owner_id_still_mandatory_on_tiered_search(session, owner):
    mem, _ = _svc(session)
    with pytest.raises(ValidationFailed) as exc:
        mem.search("personal", "x", owner_id="", tiers=["long"])
    assert exc.value.code == "owner_required"


def test_tier_filter_does_not_collide_in_the_cache(session, owner):
    """A cached all-tier result must not be served for a tier-filtered query."""
    mem, _ = _svc(session)
    _write(mem, owner, content="cache probe", tier="short", session_id="conv-1")
    _write(mem, owner, content="cache probe", tier="long")

    everything = mem.search("personal", "cache", owner_id=OWNER)
    assert len(everything) == 2
    only_long = mem.search("personal", "cache", owner_id=OWNER, tiers=["long"])
    assert [h["tier"] for h in only_long] == ["long"]
    # And the reverse order, to catch a cache key that ignores the filter.
    only_short = mem.search("personal", "cache", owner_id=OWNER, tiers=["short"])
    assert [h["tier"] for h in only_short] == ["short"]


def test_session_scoped_cache_does_not_leak_between_sessions(session, owner):
    mem, _ = _svc(session)
    _write(mem, owner, content="scoped cache probe", tier="short", session_id="conv-A")
    assert len(mem.search("personal", "scoped", owner_id=OWNER, session_id="conv-A")) == 1
    assert mem.search("personal", "scoped", owner_id=OWNER, session_id="conv-B") == []


# ---------------------------------------------------------------------------
# Migration 0026 — the schema the tests above run against
# ---------------------------------------------------------------------------
_LEGACY_MEMORIES_DDL = """
CREATE TABLE memories (
    id VARCHAR(64) PRIMARY KEY,
    owner_id VARCHAR(200), domain VARCHAR(16), category VARCHAR(32),
    content TEXT, content_hash VARCHAR(64), active BOOLEAN, endorsed BOOLEAN,
    hypothesis_status VARCHAR(16), created_at DATETIME, deleted_at DATETIME,
    version INTEGER
)
"""


def _snapshot(conn):
    insp = sa.inspect(conn)
    return {
        "cols": sorted(c["name"] for c in insp.get_columns("memories")),
        "mem_idx": sorted(i["name"] for i in insp.get_indexes("memories")),
        "session_cols": sorted(c["name"] for c in insp.get_columns("memory_tier_sessions")),
        "session_idx": sorted(i["name"] for i in insp.get_indexes("memory_tier_sessions")),
        "session_fk": sorted(f["name"] for f in insp.get_foreign_keys("memory_tier_sessions")),
    }


def _run_0026(conn, mod):
    mod.op = Operations(MigrationContext.configure(conn))
    mod.upgrade()


def test_migration_0026_backfills_legacy_rows_to_medium():
    """Existing rows must land on medium — the read-equivalent legacy behaviour."""
    mod = _migration_0026()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text(_LEGACY_MEMORIES_DDL))
        conn.execute(sa.text(
            "INSERT INTO memories (id,owner_id,domain,category,content,content_hash,"
            "active,endorsed,hypothesis_status,version) "
            "VALUES ('old1','o1','personal','preference','legacy','h',1,0,'unverified',1)"
        ))
        _run_0026(conn, mod)
        rows = conn.execute(sa.text(
            "SELECT id, tier, session_count, reinforcement_count, tier_expires_at FROM memories"
        )).fetchall()
    assert rows == [("old1", "medium", 0, 0, None)]


def test_migration_0026_is_idempotent():
    mod = _migration_0026()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text(_LEGACY_MEMORIES_DDL))
        _run_0026(conn, mod)
        first = _snapshot(conn)
        _run_0026(conn, mod)  # must not raise, must not duplicate
        assert _snapshot(conn) == first


def test_migration_0026_is_a_noop_without_the_memories_table():
    """A DB missing the base table must not error (matches the 0010 rule)."""
    mod = _migration_0026()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        _run_0026(conn, mod)  # must not raise
        assert not sa.inspect(conn).has_table("memories")


def test_migration_0026_enforces_every_check_on_a_legacy_db():
    """The constraints must bite on a migrated database, not just via create_all."""
    mod = _migration_0026()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text(_LEGACY_MEMORIES_DDL))
        _run_0026(conn, mod)
        # A real row to mutate, so each violation below actually reaches the CHECK.
        conn.execute(sa.text(
            "INSERT INTO memories (id,owner_id,domain,category,content,content_hash,"
            "active,endorsed,hypothesis_status,version) "
            "VALUES ('m1','o1','personal','preference','row','h',1,0,'unverified',1)"
        ))
        for sql in (
            "INSERT INTO memories (id,tier,version) VALUES ('b1','eternal',1)",  # bad tier
            "UPDATE memories SET tier='short', tier_expires_at=NULL WHERE id='m1'",  # no deadline
            "UPDATE memories SET tier='long', tier_expires_at='2027-01-01' WHERE id='m1'",  # long+clock
            "UPDATE memories SET session_count=-1 WHERE id='m1'",  # negative counter
        ):
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(sa.text(sql))
        # ...and a legitimate short row is accepted.
        conn.execute(sa.text(
            "INSERT INTO memories (id,tier,tier_expires_at,session_id,version) "
            "VALUES ('ok1','short','2027-01-01','s1',1)"
        ))
        # A session counts once.
        conn.execute(sa.text(
            "INSERT INTO memory_tier_sessions (id,memory_id,session_id,first_seen_at) "
            "VALUES ('e1','ok1','s1','2026-01-01')"
        ))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text(
                "INSERT INTO memory_tier_sessions (id,memory_id,session_id,first_seen_at) "
                "VALUES ('e2','ok1','s1','2026-01-02')"
            ))


def test_fresh_create_all_db_and_migrated_db_agree():
    """A fresh install must end up with the same schema as a migrated one.

    Migration 0001 builds tables from ``Base.metadata.create_all``, so on a fresh
    database ``memories`` already carries the tier columns and 0026's column block
    is skipped. Anything 0026 owns but ``create_all`` does not (the composite
    index) must therefore be guarded independently — otherwise it would be
    missing forever on every fresh install. This test pins that invariant.
    """
    mod = _migration_0026()

    fresh = sa.create_engine("sqlite://")
    with fresh.begin() as conn:
        Base.metadata.create_all(bind=conn)   # what 0001 does
        _run_0026(conn, mod)
        fresh_snap = _snapshot(conn)

    legacy = sa.create_engine("sqlite://")
    with legacy.begin() as conn:
        conn.execute(sa.text(_LEGACY_MEMORIES_DDL))  # pre-0026 shape
        _run_0026(conn, mod)
        legacy_snap = _snapshot(conn)

    assert fresh_snap["cols"] == legacy_snap["cols"]
    assert fresh_snap["session_cols"] == legacy_snap["session_cols"]
    assert fresh_snap["session_idx"] == legacy_snap["session_idx"]
    assert fresh_snap["session_fk"] == legacy_snap["session_fk"]
    # The composite index must exist on BOTH paths.
    assert "ix_memories_owner_tier" in fresh_snap["mem_idx"]
    assert "ix_memories_owner_tier" in legacy_snap["mem_idx"]


def test_migration_0026_downgrade_keeps_the_memory_rows():
    """Downgrade removes the retention axis, never user data."""
    mod = _migration_0026()
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text(_LEGACY_MEMORIES_DDL))
        conn.execute(sa.text(
            "INSERT INTO memories (id,owner_id,domain,category,content,content_hash,"
            "active,endorsed,hypothesis_status,version) "
            "VALUES ('keepme','o1','personal','preference','legacy','h',1,0,'unverified',1)"
        ))
        _run_0026(conn, mod)
        mod.op = Operations(MigrationContext.configure(conn))
        mod.downgrade()
        insp = sa.inspect(conn)
        assert "tier" not in {c["name"] for c in insp.get_columns("memories")}
        assert insp.get_check_constraints("memories") == []
        assert not insp.has_table("memory_tier_sessions")
        # The memory itself survives, content and version intact.
        assert conn.execute(sa.text("SELECT content, version FROM memories")).fetchall() == [
            ("legacy", 1)
        ]
        # ...and the migration can be replayed afterwards.
        _run_0026(conn, mod)
        assert conn.execute(sa.text("SELECT tier FROM memories")).fetchall() == [("medium",)]


def test_orm_metadata_declares_the_tier_index():
    """create_all must produce the index, or fresh installs silently lack it."""
    idx = {i.name for i in Memory.__table__.indexes}
    assert "ix_memories_owner_tier" in idx
