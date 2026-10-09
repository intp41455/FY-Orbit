"""W4 · /api/models/catalog and /api/models/health-check (real requests, no mock data).

Nothing here fabricates a provider result: health is whatever the transport
actually returned, including the refusal to probe endpoints that are not
configured.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


def _free_closed_port() -> int:
    """A port nobody is listening on, so connecting fails deterministically."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_catalog_requires_authentication(client: TestClient):
    r = client.get("/api/models/catalog?probe=false")
    assert r.status_code == 401


def test_catalog_lists_every_provider_with_real_configuration_state(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/models/catalog?probe=false", headers=headers)
    assert r.status_code == 200, r.text

    body = r.json()
    providers = {p["provider_id"]: p for p in body["providers"]}
    assert set(providers) == {"openai_compat", "ollama", "anthropic"}
    # The test fixture configures no model credentials — say so, don't fake `configured`.
    assert all(not p["configured"] for p in providers.values())
    assert all(p["health"] is None for p in providers.values())
    # Ollama is the only provider that needs no credential; everyone else must.
    assert providers["ollama"]["requires_api_key"] is False
    assert providers["openai_compat"]["requires_api_key"] is True
    # A catalog response never carries secret material.
    assert "model_api_key" not in r.text
    assert body["models"]


def test_catalog_probe_touches_only_configured_endpoints(client: TestClient):
    """Unconfigured providers stay unprobed even with probe=true."""
    headers = login_owner(client)
    r = client.get("/api/models/catalog", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(p["health"] is None for p in body["providers"] if not p["configured"])


def test_health_check_rejects_missing_csrf(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/models/health-check", json={"providers": []},
                    headers={"Cookie": headers.get("Cookie", "")} if "Cookie" in headers else {})
    # Either unauthenticated (no session cookie) or CSRF-denied: never 2xx.
    assert r.status_code in (401, 403)


def test_health_check_reports_nothing_configured_with_a_hint(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/models/health-check", json={}, headers=headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["checks"] == []
    assert "FY_MODEL_BASE_URL" in body["hint"]
    assert body["checked_at"]


def test_health_check_surfaces_the_real_transport_error_for_a_dead_endpoint(
        client: TestClient, app):
    """A configured endpoint that does not answer is reported as unreachable."""
    port = _free_closed_port()
    app.state.settings.model_provider = "ollama"
    app.state.settings.model_base_url = f"http://127.0.0.1:{port}"
    headers = login_owner(client)
    try:
        r = client.post("/api/models/health-check", json={"timeout_seconds": 2.0},
                        headers=headers)
    finally:
        app.state.settings.model_provider = ""
        app.state.settings.model_base_url = ""

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["checks"]) == 1
    check = body["checks"][0]
    assert check["provider_id"] == "ollama"
    # Real failure, real reason — never `ok: true` for an endpoint that is down.
    assert check["ok"] is False
    assert check["error"]
    assert "transport" in check["error"] or "Refused" in check["error"] or "refused" in check["error"]
    # The error is sanitised: no URL leaks to the client.
    assert "127.0.0.1" not in check["error"]


@pytest.mark.parametrize("payload", [{}, {"providers": ["ollama"]}, {"timeout_seconds": "junk"}])
def test_health_check_accepts_bounded_inputs(client: TestClient, payload):
    headers = login_owner(client)
    r = client.post("/api/models/health-check", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0 < body["probe_timeout_seconds"] <= 20.0
