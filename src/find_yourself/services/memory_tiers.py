"""Three-tier memory retention policy (需求10).

Why this module exists
----------------------
``MemoryService`` already had a thick single-scale memory pipeline (writes,
revisions, hypothesis approval, derived-source graph, authz-first search). What
it lacked is a **lifecycle**: every record sat on one flat timeline forever. This
module holds the *policy* — which tier a new record lands in, when it is
promoted, when it is demoted, when it is evicted — separately from the *storage*
concerns in :mod:`find_yourself.services.memory`.

The three tiers and their retirement rules
-----------------------------------------
``short``  — **session-scoped working memory.** Lives for one conversation.
             Every short record carries both a ``session_id`` and a hard
             ``tier_expires_at`` deadline. It is *evicted* (soft-deleted) once
             the deadline passes: after that it is not returned by any read and
             :meth:`decay` reaps it. This is the only tier with a hard deadline.

``medium`` — **project-scoped memory.** Survives across sessions, so it is what
             "what we agreed on this project" looks like. It has no hard expiry
             but it **decays by disuse**: unused for
             :data:`MEDIUM_MAX_IDLE_DAYS` it is demoted back to short. It is the
             only tier that can be promoted *without* a human decision.

``long``   — **the durable user profile.** Reachable only via an explicit
             owner-endorsed promotion. No timer may ever evict or demote it:
             silently forgetting a stated user profile is data loss, not
             housekeeping. Only :meth:`MemoryService.revoke_profile` (owner-only)
             takes a long record down, and it lands in medium, never in the bin.

Promotion rules (the actual predicates)
--------------------------------------
short -> medium
    Requires **carry-over evidence**: the record must have been brought into at
    least :data:`SHORT_TO_MEDIUM_MIN_SESSIONS` distinct sessions
    (``session_count``) *or* been explicitly endorsed by the owner. A fact that
    only ever appeared in one conversation is not a project fact.

medium -> long
    Requires **all three**, and no automated path may satisfy all three:
    1. the record is ``fact``-grade (``hypothesis_status == "fact"``) — you
       cannot build a profile out of a guess;
    2. it has been reinforced at least :data:`MEDIUM_TO_LONG_MIN_REINFORCEMENTS`
       times (``reinforcement_count``);
    3. the **owner** called :meth:`MemoryService.promote` (an owner-authenticated
       actor). A service actor is structurally refused, so no background job can
       quietly promote its own guesses into the user profile.

Demotion rules
--------------
medium -> short
    Automatic on disuse (idle > :data:`MEDIUM_MAX_IDLE_DAYS`) via
    :meth:`MemoryService.decay`. The record is kept, not deleted — it can be
    promoted again if it comes back into use.

long -> medium
    Only via owner revocation, and only to medium. Never to short, never to
    deleted: a profile the user stated does not get thrown away by a policy
    sweep.

short -> (evicted)
    Automatic on deadline. This is the only path that soft-deletes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db.models import MEMORY_TIERS

#: How long a short-tier record survives without a deadline being extended.
SHORT_TTL = timedelta(hours=12)

#: Distinct sessions a short record must reach before it may be promoted.
SHORT_TO_MEDIUM_MIN_SESSIONS = 2

#: Reinforcements a medium record needs before it is *eligible* for long.
MEDIUM_TO_LONG_MIN_REINFORCEMENTS = 3

#: A medium record unused for this long is demoted back to short.
MEDIUM_MAX_IDLE_DAYS = 90

#: Ordering helper: index in this tuple == how far a record has climbed.
TIER_ORDER: dict[str, int] = {t: i for i, t in enumerate(MEMORY_TIERS)}


class MemoryTierPolicy:
    """Pure, side-effect-free tier decisions.

    Kept free of SQLAlchemy so the rules can be reasoned about (and tested)
    without a database. :class:`~find_yourself.services.memory.MemoryService`
    owns persistence and delegates every judgement here.
    """

    short = "short"
    medium = "medium"
    long = "long"

    @staticmethod
    def validate(tier: str) -> str:
        if tier not in MEMORY_TIERS:
            raise ValueError(
                f"unknown memory tier {tier!r}; expected one of {MEMORY_TIERS}"
            )
        return tier

    @staticmethod
    def is_valid(tier: str) -> bool:
        return tier in MEMORY_TIERS

    @classmethod
    def deadline_for(cls, tier: str, *, now: datetime, ttl: timedelta | None = None) -> datetime | None:
        """The eviction deadline a record of ``tier`` starts with.

        Only the short tier gets one. Medium decays by disuse and long never
        expires, so handing either a deadline would encode a promise the rest of
        this module does not keep.
        """
        cls.validate(tier)
        if tier != cls.short:
            return None
        return now + (ttl if ttl is not None else SHORT_TTL)

    @classmethod
    def is_evicted(cls, tier: str, expires_at: datetime | None, now: datetime) -> bool:
        """True when a record's short-tier deadline has passed.

        Only meaningful for the short tier; medium and long are never evicted by
        a clock, which is exactly why a record that somehow lost its tier
        (``NULL`` deadline) is *not* treated as evicted here.
        """
        if tier != cls.short:
            return False
        return expires_at is not None and expires_at <= now

    @classmethod
    def can_promote(
        cls,
        *,
        tier: str,
        hypothesis_status: str,
        session_count: int,
        reinforcement_count: int,
        endorsed: bool,
        actor_is_owner: bool,
        active: bool = True,
    ) -> tuple[bool, str]:
        """Decide whether ``tier -> tier+1`` is allowed.

        Returns ``(allowed, reason)``. ``reason`` is a stable machine-readable
        slug, never a sentence, so callers can branch on it and tests can assert
        on it. Refusals are always specific — "why not" is the whole point of a
        retention policy.
        """
        cls.validate(tier)
        if tier == cls.long:
            return False, "already_top_tier"
        if not active:
            # An inactive record (denied hypothesis, revoked grant) is not a
            # promotion candidate at all: promoting it would resurface content
            # the owner already rejected.
            return False, "record_inactive"
        if tier == cls.short:
            if session_count >= SHORT_TO_MEDIUM_MIN_SESSIONS:
                return True, "carried_across_sessions"
            if endorsed:
                return True, "owner_endorsed"
            return False, "insufficient_session_carryover"

        # medium -> long: the profile gate.
        if not actor_is_owner:
            # Structurally unreachable for background/service callers.
            return False, "owner_required_for_profile"
        if hypothesis_status != "fact":
            return False, "not_fact_grade"
        if reinforcement_count < MEDIUM_TO_LONG_MIN_REINFORCEMENTS:
            return False, "insufficient_reinforcement"
        return True, "owner_confirmed_profile"

    @classmethod
    def can_demote(cls, *, tier: str, actor_is_owner: bool) -> tuple[bool, str]:
        """Decide whether ``tier -> tier-1`` is allowed, and how far.

        Returns ``(allowed, reason)``. Long may only step down to medium and only
        for the owner; a timer-driven decay may not touch it at all (callers pass
        ``actor_is_owner=False`` for sweeps).
        """
        cls.validate(tier)
        if tier == cls.short:
            return False, "already_bottom_tier"
        if tier == cls.long:
            if not actor_is_owner:
                return False, "owner_required_to_revoke_profile"
            return True, "owner_revoked_profile"
        return True, "decayed_by_disuse"

    @classmethod
    def should_decay(
        cls, *, tier: str, last_used_at: datetime | None, now: datetime
    ) -> bool:
        """True when a medium record has gone ``MEDIUM_MAX_IDLE_DAYS`` unused.

        A record that has never been used (``last_used_at is None``) is judged
        from ``tier_changed_at``/creation by the caller, which passes that time
        in as ``last_used_at``; ``None`` here means "we have no clock at all",
        which we refuse to act on rather than guess.
        """
        if tier != cls.medium:
            return False
        if last_used_at is None:
            return False
        return (now - last_used_at) > timedelta(days=MEDIUM_MAX_IDLE_DAYS)


@dataclass(frozen=True)
class TierTransition:
    """The outcome of one promotion/demotion decision, for audit + API replies."""

    memory_id: str
    from_tier: str
    to_tier: str
    reason: str
    changed: bool


def tier_report(rows) -> dict[str, int]:
    """Count rows per tier. ``rows`` are ``Memory``-like (duck-typed on ``tier``)."""
    counts = {t: 0 for t in MEMORY_TIERS}
    for r in rows:
        if r.tier in counts:
            counts[r.tier] += 1
    return counts
