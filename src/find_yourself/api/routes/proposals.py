"""Proposals and owner decision (FROZEN_CONTRACT §5.3, §6).

* Any authenticated caller (owner or service identity) may create a proposal.
* ONLY the owner session may decide (approve/reject). A service identity that
  calls decision gets 403 (S05).
* Approval re-verifies the stored digest against both the stored content and the
  client-supplied digest; a payload/version/target/expiry change refuses (S06).
* The conditional state machine guarantees one decision wins (S08/S09).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ...db.models import Proposal
from ..deps import csrf_protected, get_actor, get_services, Services
from ..schemas import ProposalCreate, ProposalDecision
from ...services.actor import Actor
from ...services.errors import NotFound

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _serialize(p: Proposal) -> dict:
    return {
        "id": p.id, "operation": p.operation, "target_id": p.target_id,
        "expected_version": p.expected_version, "payload": p.payload,
        "reason": p.reason, "rollback": p.rollback, "digest": p.digest,
        "status": p.status,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "execution_id": p.execution_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "version": p.version,
    }


@router.post("")
async def create_proposal(body: ProposalCreate, actor: Actor = Depends(csrf_protected),
                          svc: Services = Depends(get_services)) -> dict:
    p = svc.proposals.create(
        actor, operation=body.operation, payload=body.payload, reason=body.reason,
        rollback=body.rollback, target_id=body.target_id,
        expected_version=body.expected_version, expires_in_minutes=body.expires_in_minutes,
    )
    svc.session.commit()
    return _serialize(p)


@router.get("")
async def list_proposals(actor: Actor = Depends(get_actor),
                         svc: Services = Depends(get_services)) -> list[dict]:
    rows = svc.session.execute(
        select(Proposal).order_by(Proposal.created_at.desc()).limit(100)
    ).scalars()
    return [_serialize(p) for p in rows]


@router.get("/{proposal_id}")
async def get_proposal(proposal_id: str, actor: Actor = Depends(get_actor),
                       svc: Services = Depends(get_services)) -> dict:
    p = svc.session.get(Proposal, proposal_id)
    if p is None:
        raise NotFound("proposal_not_found", "Proposal not found")
    return _serialize(p)


@router.post("/{proposal_id}/decision")
async def decide(proposal_id: str, body: ProposalDecision,
                 actor: Actor = Depends(csrf_protected),
                 svc: Services = Depends(get_services)) -> dict:
    # svc.proposals.decide enforces actor.require_owner() (S05) and digest
    # re-verification (S06) and the single-winner conditional update (S08).
    p = svc.proposals.decide(actor, proposal_id, body.digest, approve=(body.decision == "approve"))
    svc.session.commit()
    return _serialize(p)
