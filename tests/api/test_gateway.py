"""R01 / G4: model gateway cold start — MODEL_NOT_CONFIGURED, no fabricated answer, no outbound call."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from find_yourself.config import Settings
from find_yourself.runtime.gateway import ModelGateway, ModelNotConfigured, PriceUnknown
from find_yourself.services.actor import Actor

from helpers import login_owner


def test_gateway_unconfigured_reports_model_not_configured():
    settings = Settings(environment="test", session_secret="x" * 40,
                        database_url="sqlite://", public_url="http://x")
    gw = ModelGateway(settings, budget=None)
    assert gw.configured is False
    with pytest.raises(ModelNotConfigured):
        gw.require_configured()
    with pytest.raises(ModelNotConfigured):
        gw.complete(Actor.owner("o"), task_id="t", model="m", prompt="p")


def test_gateway_unknown_price_blocks_instead_of_zero_cost():
    settings = Settings(environment="test", session_secret="x" * 40,
                        database_url="sqlite://", public_url="http://x",
                        model_api_key="secret", model_base_url="https://api.example")
    gw = ModelGateway(settings, budget=None)
    assert gw.configured is True
    with pytest.raises(PriceUnknown):
        gw.estimated_cost("m", 1024)


def test_api_inference_unconfigured_surface_model_not_configured(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/inference/complete",
                    json={"model": "m", "prompt": "hello"}, headers=headers)
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "model_not_configured"
    # No fabricated answer field.
    assert "text" not in body.get("data", {})


def test_api_inference_requires_auth(client: TestClient):
    r = client.post("/api/inference/complete", json={"model": "m", "prompt": "hi"})
    assert r.status_code == 401
