"""Assessment routes (FROZEN_CONTRACT §5.3, §11, U05/U06).

Server-side scoring. The catalog is synthetic and clearly labelled; missing
answers never yield a default result; results are bound to questionnaire
version and item-set hash. Official IPIP wiring is BLOCKED_EXTERNAL until source,
licence and Chinese translation are verified.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...adapters.assessments import scorer
from ...services.actor import Actor
from ..deps import csrf_protected, get_actor

router = APIRouter(prefix="/api/assessments", tags=["assessments"])


@router.get("/catalog")
async def catalog(actor: Actor = Depends(get_actor)) -> dict:
    return {"questionnaires": scorer.catalog()}


@router.post("/{assessment_id}/sessions")
async def start_session(assessment_id: str, actor: Actor = Depends(csrf_protected)) -> dict:
    s = scorer.start(assessment_id)
    return s.to_public()


@router.post("/sessions/{session_id}/answers")
async def record_answers(session_id: str, body: dict, actor: Actor = Depends(csrf_protected)) -> dict:
    s = scorer.record_answers(session_id, body.get("answers", {}))
    return s.to_public()


@router.post("/sessions/{session_id}/submit")
async def submit(session_id: str, actor: Actor = Depends(csrf_protected)) -> dict:
    s = scorer.submit(session_id)
    return s.to_public()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, actor: Actor = Depends(get_actor)) -> dict:
    return scorer.get(session_id).to_public()
