"""Personal-space butler agent HTTP surface (数码小屋管家接线, P2).

Endpoints:

* ``GET  /api/butler/status``   — ``{"model_configured": bool}``; the frontend
  consults this before offering real-model dialogue.
* ``POST /api/butler/dialogue`` — one butler line via :class:`ModelGateway`
  (owner session + CSRF, like every mutating owner route).

Honesty contract: without a configured provider the dialogue endpoint answers
``503 model_not_configured``; it never returns a fabricated "model" line. The
frontend falls back to its local pre-generated pool labelled 「预生成台词池」.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..deps import csrf_protected, get_actor, get_services, get_settings, Services
from ...services.actor import Actor
from ...services.butler import ButlerService

router = APIRouter(prefix="/api/butler", tags=["butler"])


class ButlerDialogueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(min_length=1, max_length=16)
    personality: str = Field(min_length=1, max_length=32)
    context: dict = Field(default_factory=dict)


def _service(actor_dep_services: Services, settings) -> ButlerService:
    return ButlerService(settings=settings, budget=actor_dep_services.budget)


@router.get("/status")
async def butler_status(
    actor: Actor = Depends(get_actor),
    settings = Depends(get_settings),
    svc: Services = Depends(get_services),
) -> dict:
    butler = _service(svc, settings)
    return {"model_configured": butler.model_configured()}


@router.post("/dialogue")
async def butler_dialogue(
    body: ButlerDialogueBody,
    actor: Actor = Depends(csrf_protected),
    settings = Depends(get_settings),
    svc: Services = Depends(get_services),
) -> dict:
    butler = _service(svc, settings)
    return butler.dialogue(
        actor, speaker=body.speaker, personality=body.personality, context=body.context
    )
