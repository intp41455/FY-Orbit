"""W4 · provider adapters: OpenAI-compatible / Ollama / Anthropic.

Every HTTP interaction runs against an in-process ``httpx`` transport. No test
here touches the network, and no test asserts success that a provider did not
actually produce.

The retry classification under test is the billing-safety boundary documented in
``runtime/providers/base.py``: transport errors, 429 and 5xx may be retried;
4xx auth/payload rejections and malformed (already possibly billed) 2xx bodies
must not be.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from find_yourself.config import Settings
from find_yourself.runtime import providers
from find_yourself.runtime.gateway import (
    ModelGateway,
    ModelNotConfigured,
    PriceUnknown,
    local_pricing,
)
from find_yourself.runtime.providers.anthropic import AnthropicProvider
from find_yourself.runtime.providers.base import (
    ProviderAuthError,
    ProviderMalformedResponse,
    ProviderRateLimited,
    ProviderTransportError,
    sanitize_message,
)
from find_yourself.runtime.providers.ollama import OllamaProvider
from find_yourself.runtime.providers.openai_compat import OpenAICompatibleProvider
from find_yourself.services.model_catalog import ModelCatalog

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class ScriptedTransport(httpx.BaseTransport):
    """Replays a list of responses/exceptions and records every request."""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("transport received more requests than scripted")
        item = self.script.pop(0)
        if callable(item):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        return item


def json_response(status: int, payload, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload, headers=headers or {},
                          request=httpx.Request("GET", "http://probe.invalid"))


def openai_body(text: str = "hello world", *, p_tokens: int = 7, c_tokens: int = 3,
                request_id: str = "chatcmpl-abc") -> dict:
    return {
        "id": request_id,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                  "total_tokens": p_tokens + c_tokens},
    }


def make_settings(environment: str = "test", **overrides) -> Settings:
    base = dict(environment=environment, session_secret="x" * 40,
                database_url="sqlite://", public_url="http://x")
    base.update(overrides)
    return Settings(**base)


# --------------------------------------------------------------------------- #
# OpenAI-compatible adapter
# --------------------------------------------------------------------------- #


def test_openai_compat_maps_text_usage_and_request_id():
    transport = ScriptedTransport([json_response(200, openai_body("天空是蓝色的", request_id="req-42"))])
    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://api.example/v1",
                                        transport=transport)
    result = provider.complete(model="gpt-4o-mini", prompt="天色如何", max_tokens=64)

    assert result.text == "天空是蓝色的"
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert result.provider_request_id == "req-42"
    assert result.provider_id == "openai_compat"
    assert transport.requests[0].url.path.endswith("/chat/completions")
    assert transport.requests[0].headers["authorization"] == "Bearer sk-test"


def test_openai_compat_429_is_retryable_and_honours_retry_after():
    transport = ScriptedTransport([
        json_response(429, {"error": "rate limited"}, headers={"Retry-After": "1.5"}),
    ])
    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://api.example",
                                        transport=transport)
    with pytest.raises(ProviderRateLimited) as info:
        provider.complete(model="gpt-4o-mini", prompt="hi")

    assert info.value.retryable is True
    assert info.value.retry_after == 1.5


def test_openai_compat_auth_failure_is_not_retryable():
    transport = ScriptedTransport([json_response(401, {"error": "invalid api key"})])
    provider = OpenAICompatibleProvider(api_key="bad", base_url="https://api.example",
                                        transport=transport)
    with pytest.raises(ProviderAuthError) as info:
        provider.complete(model="gpt-4o-mini", prompt="hi")

    # Repeating an auth rejection only repeats the refusal (and possibly the bill).
    assert info.value.retryable is False


def test_openai_compat_transport_failure_is_retryable():
    transport = ScriptedTransport([
        httpx.ConnectError("All connection attempts failed"),
    ])
    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://api.example",
                                        transport=transport)
    with pytest.raises(ProviderTransportError) as info:
        provider.complete(model="gpt-4o-mini", prompt="hi")

    assert info.value.retryable is True


def test_openai_compat_malformed_2xx_is_not_retryable():
    """A 2xx may already have been billed — never silently repeat it."""
    transport = ScriptedTransport([json_response(200, {"choices": []})])
    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://api.example",
                                        transport=transport)
    with pytest.raises(ProviderMalformedResponse) as info:
        provider.complete(model="gpt-4o-mini", prompt="hi")

    assert info.value.retryable is False


def test_openai_compat_health_probe_lists_models():
    transport = ScriptedTransport([
        json_response(200, {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}),
    ])
    provider = OpenAICompatibleProvider(api_key="sk-test", base_url="https://api.example/v1",
                                        transport=transport)
    assert provider.probe_models() == ["gpt-4o-mini", "gpt-4o"]
    assert transport.requests[0].url.path.endswith("/models")


# --------------------------------------------------------------------------- #
# Ollama (local, keyless)
# --------------------------------------------------------------------------- #


def test_ollama_completes_without_any_api_key():
    body = {
        "model": "qwen2.5:7b",
        "message": {"role": "assistant", "content": "我在本地跑的"},
        "prompt_eval_count": 12,
        "eval_count": 5,
        "done": True,
    }
    transport = ScriptedTransport([json_response(200, body)])
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", transport=transport)
    result = provider.complete(model="qwen2.5:7b", prompt="你是谁")

    assert result.text == "我在本地跑的"
    assert result.usage["prompt_tokens"] == 12
    assert result.provider_id == "ollama"
    request = transport.requests[0]
    assert request.url.path == "/api/chat"
    assert "authorization" not in request.headers  # no credential expected locally


def test_ollama_prices_local_models_at_zero_explicitly():
    settings = make_settings(model_provider="ollama", model_base_url="http://127.0.0.1:11434")
    gw = ModelGateway(settings)

    assert gw.configured is True
    pricing = gw.resolve_pricing("qwen2.5:7b", "ollama")
    assert pricing.calculate_cost(1000, 1000) == Decimal("0")
    assert pricing.local_inference is True
    assert pricing.note  # states why it is free, never "unknown treated as zero"


def test_ollama_health_probe_reports_the_real_transport_error():
    transport = ScriptedTransport([httpx.ConnectError("connection refused")])
    health = providers.probe_provider(
        providers.ProviderEndpoint(provider_id="ollama",
                                   base_url="http://127.0.0.1:11434",
                                   transport=transport))

    assert health.ok is False
    assert health.models == []
    assert "transport" in health.error
    # A down local daemon is reported as a failure, never as reachable.
    assert health.latency_ms is not None


def test_ollama_health_probe_success_lists_local_tags():
    transport = ScriptedTransport([
        json_response(200, {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]}),
    ])
    health = providers.probe_provider(
        providers.ProviderEndpoint(provider_id="ollama",
                                   base_url="http://127.0.0.1:11434",
                                   transport=transport))

    assert health.ok is True
    assert health.models == ["qwen2.5:7b", "llama3.1:8b"]


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


def test_anthropic_uses_messages_api_with_version_header():
    transport = ScriptedTransport([json_response(200, {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Claude 回复"}],
        "usage": {"input_tokens": 9, "output_tokens": 4},
    })])
    provider = AnthropicProvider(api_key="sk-ant", transport=transport)
    result = provider.complete(model="claude-3-5-sonnet-latest", prompt="ping")

    assert result.text == "Claude 回复"
    assert result.usage["total_tokens"] == 13
    request = transport.requests[0]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant"
    assert request.headers["anthropic-version"] == "2023-06-01"


# --------------------------------------------------------------------------- #
# Factory / config inference
# --------------------------------------------------------------------------- #


def test_provider_defaults_to_openai_compat_for_legacy_configuration():
    settings = make_settings(model_api_key="k", model_base_url="https://api.example/v1")
    gw = ModelGateway(settings)

    assert gw.primary_provider_id == "openai_compat"
    assert [route.provider_id for route in gw.routes] == ["openai_compat"]


def test_ollama_port_without_key_is_inferred_as_local_provider():
    """Acceptance item 1: a .env holding only the Ollama address must work."""
    settings = make_settings(model_base_url="http://127.0.0.1:11434")
    gw = ModelGateway(settings)

    assert gw.primary_provider_id == "ollama"
    assert gw.configured is True


def test_unsupported_provider_id_is_reported_not_silently_defaulted():
    settings = make_settings(model_provider="totally-made-up",
                             model_api_key="k", model_base_url="https://api.example")
    gw = ModelGateway(settings)

    assert gw.config_error
    assert gw.routes == []
    with pytest.raises(ModelNotConfigured):
        gw.require_configured()


def test_cross_vendor_fallback_refuses_credential_inheritance():
    settings = make_settings(
        model_api_key="openai-key", model_base_url="https://api.openai.com/v1",
        model_fallbacks=[{"provider": "anthropic"},
                         {"provider": "ollama", "model": "qwen2.5:7b"}])
    gw = ModelGateway(settings)

    ids = [route.provider_id for route in gw.routes]
    assert ids == ["openai_compat", "ollama"]
    # The refusal is visible rather than silently dropped.
    assert any("anthropic" in err for err in gw.route_errors)
    assert gw.endpoints()[1].provider_id == "ollama"
    assert gw.endpoints()[1].base_url == "http://127.0.0.1:11434"


def test_unknown_remote_price_still_blocks_instead_of_charging_zero():
    settings = make_settings(model_api_key="k", model_base_url="https://api.example")
    gw = ModelGateway(settings)

    with pytest.raises(PriceUnknown):
        gw.resolve_pricing("some-unpriced-cloud-model", "openai_compat")


def test_configured_price_override_supplies_a_real_rate():
    settings = make_settings(
        model_api_key="k", model_base_url="https://api.example",
        model_price_overrides={"my-model": {"input_usd_per_1k": "0.002",
                                            "output_usd_per_1k": "0.004"}})
    gw = ModelGateway(settings)

    pricing = gw.resolve_pricing("my-model", "openai_compat")
    assert pricing.input_usd_per_1k == Decimal("0.002")
    assert "FY_MODEL_PRICE_OVERRIDES" in pricing.note


# --------------------------------------------------------------------------- #
# Catalog registry / health
# --------------------------------------------------------------------------- #


def test_registry_reports_unconfigured_providers_without_probing_them():
    settings = make_settings()  # nothing configured at all
    catalog = ModelCatalog(settings=settings)
    summary = catalog.gateway_summary(probe=False)

    configured = {p["provider_id"]: p["configured"] for p in summary["providers"]}
    assert configured == {"openai_compat": False, "ollama": False, "anthropic": False}
    # Nothing was contacted, therefore no health block can be present.
    assert all(p["health"] is None for p in summary["providers"])
    assert {p["provider_id"] for p in summary["providers"]} == set(providers.PROVIDER_IDS)


def test_catalog_health_report_uses_a_real_probe_result():
    settings = make_settings(model_provider="ollama", model_base_url="http://127.0.0.1:11434")
    catalog = ModelCatalog(settings=settings)
    transport = ScriptedTransport([
        json_response(200, {"models": [{"name": "qwen2.5:7b"}]}),
    ])
    report = catalog.health_report(timeout_seconds=1.0, transport=transport)

    assert len(report["checks"]) == 1
    check = report["checks"][0]
    assert check["provider_id"] == "ollama"
    assert check["ok"] is True
    assert check["latency_ms"] >= 0
    assert check["models"] == ["qwen2.5:7b"]


def test_sanitize_strips_endpoints_from_error_text():
    cleaned = sanitize_message("ConnectError: failed to connect to http://user:pw@host:9000/v1/path")
    assert "http" not in cleaned
    assert "user:pw" not in cleaned
    assert "<endpoint>" in cleaned


def test_local_pricing_helper_marks_local_inference():
    pricing = local_pricing("llama3.1:8b")
    assert pricing.local_inference is True
    assert pricing.input_usd_per_1k == Decimal("0")
