"""REST API routes for deterministic chart computation, external import, and layered interpretation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ...charts.engine import ChartImporter, DeterministicChartEngine
from ...charts.interpreter import ChartInterpreter
from ...charts.models import (
    ChartRequest,
    ChartResult,
    DailyFortuneRequest,
    DailyFortuneResult,
    ExternalChartImportRequest,
    InterpretationResult,
    InterpretRequest,
    SynastryRequest,
    SynastryResult,
    TarotDrawRequest,
    TarotDrawResult,
)
from ...charts.retrieval import DualPathRetrievalService
from ...services.actor import Actor
from ..deps import Services, csrf_protected, get_services

router = APIRouter(prefix="/api/charts", tags=["charts"])

# In-memory registry of recent computed charts for interpretation lookup
_CHART_CACHE: dict[str, ChartResult] = {}


@router.post("/compute", response_model=ChartResult, status_code=status.HTTP_200_OK)
def compute_chart(
    body: ChartRequest,
    actor: Actor = Depends(csrf_protected),
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
    actor: Actor = Depends(csrf_protected),
) -> ChartResult:
    """Import an externally generated or scanned chart faithfully without fabrication."""
    actor.require_owner()
    result = ChartImporter.parse_and_import(body)
    _CHART_CACHE[result.chart_id] = result
    return result


@router.post("/interpret", response_model=InterpretationResult, status_code=status.HTTP_200_OK)
def interpret_chart(
    body: InterpretRequest,
    actor: Actor = Depends(csrf_protected),
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
            actor=actor,
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


@router.post("/fortune/daily", response_model=DailyFortuneResult, status_code=status.HTTP_200_OK)
def get_daily_fortune(
    body: DailyFortuneRequest,
    actor: Actor = Depends(csrf_protected),
) -> DailyFortuneResult:
    """Deterministic daily fortune and sexagenary balance calculation (B-运势-01~05)."""
    actor.require_owner()
    return DeterministicChartEngine.compute_daily_fortune(body)


@router.post("/tarot/draw", response_model=TarotDrawResult, status_code=status.HTTP_200_OK)
def draw_tarot(
    body: TarotDrawRequest,
    actor: Actor = Depends(csrf_protected),
) -> TarotDrawResult:
    """Authoritative deterministic Tarot Oracle draw linked to cabin/deskpet events (B-运势-01~02)."""
    actor.require_owner()
    return DeterministicChartEngine.draw_tarot(body)


@router.post("/synastry", response_model=SynastryResult, status_code=status.HTTP_200_OK)
def compute_synastry(
    body: SynastryRequest,
    actor: Actor = Depends(csrf_protected),
) -> SynastryResult:
    """Deterministic two-chart synastry & element synergy calculation."""
    actor.require_owner()
    return DeterministicChartEngine.compute_synastry(body)

