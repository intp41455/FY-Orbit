"""Shared API test constants and helpers (imported via tests/api on sys.path)."""

from __future__ import annotations

from fastapi.testclient import TestClient

OWNER_SUB = "owner-sub-123"
CLIENT_ID = "fy-web"
ISSUER = "http://issuer"
LOCAL_TOKEN = "dev-token-secret"


def login_owner(c: TestClient) -> dict:
    """Log in as the owner via the loopback dev-token; return CSRF headers."""
    r = c.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}
