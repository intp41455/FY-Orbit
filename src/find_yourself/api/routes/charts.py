"""REST API routes for deterministic chart computation, external import, and layered interpretation."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..deps import get_actor, get_db, get_services, Services
from ...charts.engine import ChartImporter, DeterministicChartEngine
from ...charts.interpreter import ChartInterpreter
from ...charts.models import (
    ChartRequest,
    ChartResult,
    ExternalChartImportRequest,
    InterpretRequest,
    InterpretationResult,
)
from ...charts.retrieval import DualPathRetrievalService
from ...services.actor import Actor


router = APIRouter(prefix="/api/charts", tags=["charts"])

# In-memory registry of recent computed charts for interpretation lookup
_CHART_CACHE: dict[str, ChartResult] = {}


@router.post("/compute", response_model=ChartResult, status_code=status.HTTP_200_OK)
def compute_chart(
    body: ChartRequest,
    actor: Actor = Depends(get_actor),
) -> ChartResult:
    """Compute an astrological or bazi chart using the authoritative deterministic engine."""
    actor.require_owner()

    if body.system == "bazi":
        result = DeterministicChartEngine.compute_bazi(body)
    elif body.system == "western":
        result = DeterministicChartEngine.compute_western(body)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported system '{body.system}'. Supported systems: 'bazi', 'western'.",
        )

    _CHART_CACHE[result.chart_id] = result
    return result


@router.post("/import", response_model=ChartResult, status_code=status.HTTP_200_OK)
def import_external_chart(
    body: ExternalChartImportRequest,
    actor: Actor = Depends(get_actor),
) -> ChartResult:
    """Import an externally generated or scanned chart faithfully without fabrication."""
    actor.require_owner()
    result = ChartImporter.parse_and_import(body)
    _CHART_CACHE[result.chart_id] = result
    return result


@router.post("/interpret", response_model=InterpretationResult, status_code=status.HTTP_200_OK)
def interpret_chart(
    body: InterpretRequest,
    actor: Actor = Depends(get_actor),
    services: Services = Depends(get_services),
) -> InterpretationResult:
    """Produce a four-layer interpretation separating computed facts, public citations, and hypotheses."""
    actor.require_owner()

    chart = _CHART_CACHE.get(body.chart_id)
    if not chart:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chart '{body.chart_id}' not found in current session cache. Please compute or import first.",
        )

    retrieval_svc = DualPathRetrievalService(services.session, memory_service=services.memory)

    # 1. Public citations
    web_citations: list[dict[str, Any]] = []
    if body.include_web_search:
        web_citations = retrieval_svc.retrieve_public_knowledge(
            query=f"{chart.system} {body.perspective}",
            system=chart.system,
        )

    # 2. Authorized personal citations
    personal_citations: list[dict[str, Any]] = []
    if body.include_personal_memory:
        personal_citations = retrieval_svc.retrieve_personal_memory(
            actor=actor,
            domain=body.authorized_domain,
            query=body.user_notes or "personality traits",
        )

    # 3. Layered Interpretation
    return ChartInterpreter.interpret(
        chart=chart,
        perspective=body.perspective,
        web_citations=web_citations,
        personal_citations=personal_citations,
        user_notes=body.user_notes,
    )
