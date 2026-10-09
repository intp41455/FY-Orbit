"""Model inference cold-start surface (FROZEN_CONTRACT §7, G4/R01).

This endpoint exists so the API explicitly reports ``MODEL_NOT_CONFIGURED`` when
no provider credentials are present. It never fabricates a model answer and never
contacts a paid provider. Real provider wiring is ``BLOCKED_EXTERNAL``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...config import Settings
from ...runtime.gateway import ModelGateway
from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_services, get_settings

router = APIRouter(prefix="/api/inference", tags=["inference"])


class CompleteRequest(BaseModel):
    model: str = "default"
    prompt: str
    task_id: str | None = None
    max_tokens: int = 1024


@router.post("/complete")
async def complete(body: CompleteRequest,
                   actor: Actor = Depends(csrf_protected),
                   svc: Services = Depends(get_services),
                   settings: Settings = Depends(get_settings)) -> dict:
    gw = ModelGateway(settings, svc.budget)
    # Reaches require_configured -> ModelNotConfigured (503) when unconfigured.
    result = gw.complete(actor, task_id=body.task_id or "adhoc", model=body.model,
                         prompt=body.prompt, max_tokens=body.max_tokens)
    return {"text": result.text, "usage": result.usage}
