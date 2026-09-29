"""Health and metrics routes (FROZEN_CONTRACT §5.1).

``/health/live`` and ``/health/ready`` are public but return only minimal status;
they never leak topology, connection strings or exception details. ``/metrics``
is restricted to loopback / internal networks and its labels never contain raw
content, task text or tokens.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text

from ..deps import get_services, Services

router = APIRouter(tags=["health"])

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request, svc: Services = Depends(get_services)) -> JSONResponse:
    # Check DB connectivity; on failure return 503 with an opaque body.
    try:
        svc.session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - deliberately opaque
        return JSONResponse({"status": "unavailable"}, status_code=503)

    tr = getattr(request.app.state, "temporal", None)
    temporal_status = tr.is_enabled() if tr is not None else False
    body = {"status": "ok", "temporal": "ready" if temporal_status else "disabled"}
    return JSONResponse(body)


@router.get("/metrics")
async def metrics(request: Request) -> PlainTextResponse:
    client = request.client.host if request.client else ""
    if client not in LOOPBACK:
        # Only internal network / loopback may scrape metrics.
        return PlainTextResponse("forbidden", status_code=403)
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return PlainTextResponse(generate_latest().decode("utf-8"),
                             media_type=CONTENT_TYPE_LATEST)
