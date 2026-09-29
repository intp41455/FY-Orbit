"""Runnable ASGI entrypoint.

Run with (env prefix ``FY_``):

    uv run uvicorn find_yourself.api.main:app --host 127.0.0.1 --port 8000

Health: GET /health/live , GET /health/ready
OpenAPI: GET /openapi.json
"""

from __future__ import annotations

from .app import create_app

app = create_app()
