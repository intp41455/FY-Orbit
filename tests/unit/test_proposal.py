"""BUG-04/05/09: digest re-verification, owner-only decision, outbox single-consume, release binding."""

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, PermissionDenied
from find_yourself.services.outbox import OutboxService
from find_yourself.services.proposal import ProposalService


def _services(session):
    audit = AuditService(session)
    proposals = ProposalService(session, audit)
    outbox = OutboxService(session, audit)
    return audit, proposals, outbox


def test_agent_cannot_approve_its_own_proposal(session):
    _, proposals, _ = _services(session)
    agent = Actor.service("worker-1", "worker", ["work"])
    p = proposals.create(agent, operation="grant.add", payload={"x": 1},
                          reason="allow access", rollback="undo")
    with pytest.raises(PermissionDenied):
        proposals.decide(agent, p.id, p.digest, approve=True)


def test_digest_mismatch_rejected(session):
    _, proposals, _ = _services(session)
    owner = Actor.owner("owner-1")
    p = proposals.create(owner, operation="grant.add", payload={"x": 1},
                        reason="allow access", rollback="undo")
    with pytest.raises(Conflict):
        proposals.decide(owner, p.id, "0" * 64, approve=True)


def test_external_approval_enqueues_outbox_and_does_not_execute(session):
    audit, proposals, outbox = _services(session)
    owner = Actor.owner("owner-1")
    p = proposals.create(
        owner, operation="task.release",
        payload={"environment": "prod", "commit_sha": "a" * 40, "image_digest": "sha256:" + "b" * 64},
        reason="ship", rollback="rollback",
    )
    decided = proposals.decide(owner, p.id, p.digest, approve=True)
    assert decided.status == "approved_pending_execution"
    # Outbox has exactly one pending operation, claimed once.
    op = outbox.claim_next(Actor.service("release-1", "release"))
    assert op is not None and op.state == "claimed"
    second = outbox.claim_next(Actor.service("release-1", "release"))
    assert second is None  # single consumption
    outbox.succeed(Actor.service("release-1", "release"), op.id, external_id="gh-run-1")
    assert proposals.s.get(type(p), p.id).status == "executed"


def test_double_approval_only_succeeds_once(session):
    _, proposals, _ = _services(session)
    owner = Actor.owner("owner-1")
    p = proposals.create(owner, operation="memory.upsert", payload={"x": 1},
                          reason="add fact", rollback="undo")
    proposals.decide(owner, p.id, p.digest, approve=True)
    with pytest.raises(Conflict):
        proposals.decide(owner, p.id, p.digest, approve=True)


def test_expired_proposal_rejected(session):
    _, proposals, _ = _services(session)
    owner = Actor.owner("owner-1")
    p = proposals.create(owner, operation="memory.upsert", payload={"x": 1},
                          reason="add fact", rollback="undo", expires_in_minutes=1)
    # Force expiry.
    p.expires_at = utcnow_minus()
    session.flush()
    with pytest.raises(Conflict):
        proposals.decide(owner, p.id, p.digest, approve=True)


def utcnow_minus():
    from datetime import timedelta

    from find_yourself.db.types import utcnow
    return utcnow() - timedelta(minutes=5)
