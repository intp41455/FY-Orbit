"""Unified error envelope and exception mapping (FROZEN_CONTRACT §1).

Every error returned to a client is::

    {"error": {"code": "machine_readable_code", "message": "safe message", "details": {}}}

Stack traces, connection strings, secrets, cookies and other tenants' objects
are never leaked. Core :class:`DomainError`s map to their declared HTTP status;
anything unexpected becomes a generic 500 with a stable code.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..services.errors import DomainError

log = logging.getLogger("find_yourself.api")


def _envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        # DomainError messages are curated safe strings by construction.
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo raw pydantic internals that could reflect request shape;
        # return a stable code and a bounded, sanitised summary.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope("validation_failed", "Request validation failed",
                              {"error_count": len(exc.errors())}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Log server-side with traceback; return a generic opaque error.
        log.exception("unhandled_api_error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "Internal server error"),
        )
