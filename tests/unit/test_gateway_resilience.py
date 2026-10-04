"""W4 · gateway resilience: bounded retries, honest degradation, budget correctness.

The rules asserted here come straight from the task book and the frozen contract:

* at most two retries per route, exponential backoff honouring ``Retry-After``;
* retries only for failures that cannot have been billed (transport/429/5xx);
* when the primary route stays down the fallback answers — and the result says so
  (``degraded_from`` / ``degraded_reason``);
* each budget reservation is made against the provider that will actually run,
  released on failure and settled on success;
* if every route fails the caller gets a ``Conflict`` subclass with the real,
  sanitised per-route reasons — never a fabricated answer.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from find_yourself.config import Settings
from find_yourself.runtime.gateway import (
    CallResult,
    ModelGateway,
    ModelProviderUnavailable,
    MockModelProvider,
    PriceUnknown,
    ProviderRoute,
)
from find_yourself.runtime.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTransportError,
)
from find_yourself.services.actor import Actor
from find_yourself.services.butler import ButlerService
from find_yourself.services.errors import Conflict, PermissionDenied


# --------------------------------------------------------------------------- #
# doubles
# --------------------------------------------------------------------------- #


class FakeBudget:
    """Records the reserve/settle/release sequence without touching a database."""

    def __init__(self):
        self.reservations: list[dict] = []
        self.settled: list[tuple[str, Decimal]] = []
        self.released: list[str] = []
        self._next = 0

    def reserve(self, actor, *, task_id, amount, idempotency_key, scope):
        self._next += 1
        row = {"id": f"res-{self._next}", "task_id": task_id, "amount": Decimal(str(amount)),
               "idempotency_key": idempotency_key, "scope": scope}
        self.reservations.append(row)
        return type("Res", (), {"id": row["id"]})()

    def settle(self, actor, reservation_id, amount):
        self.settled.append((reservation_id, Decimal(str(amount))))

    def release(self, actor, reservation_id):
        self.released.append(reservation_id)


class ScriptedProvider:
    """Raises queued :class:`ProviderError`s, then returns queued results."""

    def __init__(self, script, *, provider_id="scripted", local_inference=False):
        self.script = list(script)
        self.provider_id = provider_id
        self.local_inference = local_inference
        self.calls: list[dict] = []

    def complete(self, *, model, prompt, max_tokens=1024, timeout_seconds=30.0):
        self.calls.append({"model": model, "prompt": prompt})
        if not self.script:
            raise AssertionError("ScriptedProvider ran out of scripted outcomes")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, CallResult):
            return item
        return CallResult(text=item or "ok", usage={"prompt_tokens": 2, "completion_tokens": 2,
                                                    "total_tokens": 4})


def ok_result(text: str, *, prompt_tokens: int = 5, completion_tokens: int = 5) -> CallResult:
    return CallResult(text=text, usage={"prompt_tokens": prompt_tokens,
                                        "completion_tokens": completion_tokens,
                                        "total_tokens": prompt_tokens + completion_tokens})


def _settings(**overrides) -> Settings:
    base = dict(environment="test", session_secret="x" * 40,
                database_url="sqlite://", public_url="http://x",
                model_api_key="k", model_base_url="https://api.example/v1")
    base.update(overrides)
    return Settings(**base)


def build(primary: object, fallbacks: list[ProviderRoute] | None = None, *,
          budget: FakeBudget | None = None, primary_id: str = "openai_compat",
          max_retries: int = 2, sleeper=None) -> ModelGateway:
    """Gateway whose route list is swapped for controllable doubles."""
    gw = ModelGateway(_settings(), budget=budget, max_retries=max_retries, sleeper=sleeper)
    routes = [ProviderRoute(primary_id, primary, source="primary")]
    routes.extend(fallbacks or [])
    gw.routes = routes
    return gw


ACTOR = Actor.owner("owner-1")


# --------------------------------------------------------------------------- #
# retries
# --------------------------------------------------------------------------- #


def test_429_then_success_records_retry_count():
    slept: list[float] = []
    provider = ScriptedProvider([
        ProviderRateLimited("slow down", retry_after=1.25),
        ok_result("终于成功"),
    ])
    gw = build(provider, sleeper=slept.append)

    result = gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert result.text == "终于成功"
    assert result.attempts == 2
    assert result.retries == 1
    # Retry-After wins over our own exponential schedule.
    assert slept == [1.25]
    assert len(provider.calls) == 2


def test_server_error_backoff_is_exponential_and_capped_at_two_retries():
    slept: list[float] = []
    provider = ScriptedProvider([
        ProviderServerError("bad gateway 502"),
        ProviderServerError("bad gateway 502"),
        ProviderServerError("still down"),
    ])
    gw = build(provider, sleeper=slept.append)

    with pytest.raises(ModelProviderUnavailable):
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert len(provider.calls) == 3  # 1 initial + 2 retries, no more
    assert slept == [0.5, 1.0]


def test_non_retryable_failure_is_attempted_exactly_once():
    provider = ScriptedProvider([ProviderAuthError("invalid api key")])
    gw = build(provider)

    with pytest.raises(ModelProviderUnavailable) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert len(provider.calls) == 1
    assert "auth_rejected" in str(info.value.message)


def test_unexpected_provider_exception_is_never_retried():
    """An unclassified error from a provider must not trigger a second charge."""
    provider = ScriptedProvider([RuntimeError("who knows what happened")])
    gw = build(provider)

    with pytest.raises(ModelProviderUnavailable) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert len(provider.calls) == 1
    assert "RuntimeError" in info.value.message


# --------------------------------------------------------------------------- #
# degradation
# --------------------------------------------------------------------------- #


def test_fallback_serves_the_call_and_announces_the_degradation():
    budget = FakeBudget()
    fallback = ScriptedProvider([ok_result("本地模型兜底回答", prompt_tokens=11,
                                           completion_tokens=7)],
                                provider_id="ollama", local_inference=True)
    gw = build(ScriptedProvider([ProviderTransportError("connect failed")] * 3),
               fallbacks=[ProviderRoute("ollama", fallback, model_override="qwen2.5:7b",
                                        source="fallback")],
               budget=budget)

    result = gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert result.text == "本地模型兜底回答"
    assert result.provider_id == "ollama"
    assert result.model == "qwen2.5:7b"
    # Honesty requirement: the caller can see the model was swapped.
    assert result.degraded_from == "openai_compat:gpt-4o-mini"
    assert "transport" in result.degraded_reason


def test_successful_primary_never_reports_degradation():
    gw = build(ScriptedProvider([ok_result("云端正常回答")]),
               fallbacks=[ProviderRoute("ollama", ScriptedProvider([ok_result("不该用到")]),
                                        source="fallback")])

    result = gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert result.degraded_from == ""
    assert result.degraded_reason == ""
    assert result.provider_id == "openai_compat"


def test_injected_provider_keeps_the_pre_w4_single_route_behaviour():
    """``ModelGateway(provider=...)`` must stay a one-route, no-rewrite gateway."""
    provider = MockModelProvider(default_response="mock keeps working")
    gw = ModelGateway(provider=provider)

    assert [r.source for r in gw.routes] == ["injected"]
    result = gw.complete(ACTOR, task_id="t", model="mock-deterministic", prompt="ping")

    assert result.text == "mock keeps working"
    assert gw.estimated_cost("mock-deterministic", 100) > 0


# --------------------------------------------------------------------------- #
# budget correctness
# --------------------------------------------------------------------------- #


def test_reservation_is_released_when_a_route_fails():
    budget = FakeBudget()
    gw = build(ScriptedProvider([ProviderTransportError("connect failed")] * 3),
               fallbacks=[ProviderRoute("ollama", ScriptedProvider([ok_result("fallback ok")]),
                                        model_override="qwen2.5:7b", source="fallback")],
               budget=budget)

    gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    # One reservation for the cloud attempt, released when it failed; the local
    # fallback costs 0 and therefore opens no reservation at all.
    assert len(budget.reservations) == 1
    assert budget.released == [budget.reservations[0]["id"]]
    assert budget.settled == []
    assert budget.reservations[0]["idempotency_key"].startswith("call:t1:openai_compat:")


def test_successful_cloud_call_settles_against_actual_usage():
    budget = FakeBudget()
    gw = build(ScriptedProvider([ok_result("ok", prompt_tokens=1000, completion_tokens=1000)]),
               budget=budget)

    result = gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert len(budget.reservations) == 1
    assert budget.released == []
    reservation_id = budget.reservations[0]["id"]
    assert budget.settled == [(reservation_id, Decimal("0.000750"))]
    assert result.settled_amount == Decimal("0.000750")


def test_local_zero_cost_call_opens_no_reservation():
    """BudgetService rejects non-positive amounts, so nothing is reserved."""
    budget = FakeBudget()
    provider = ScriptedProvider([ok_result("local answer")], provider_id="ollama",
                                local_inference=True)
    gw = build(provider, budget=budget, primary_id="ollama")

    result = gw.complete(ACTOR, task_id="t1", model="qwen2.5:7b", prompt="hello world")

    assert budget.reservations == []
    assert budget.settled == []
    assert result.settled_amount == Decimal("0")
    assert result.provider_id == "ollama"


def test_fallback_with_unknown_price_is_skipped_not_charged_as_zero():
    budget = FakeBudget()
    cloud_fallback = ScriptedProvider([ok_result("should not be reachable")],
                                      provider_id="openai_compat")
    gw = build(ScriptedProvider([ProviderTransportError("down")] * 3),
               fallbacks=[ProviderRoute("openai_compat", cloud_fallback,
                                        model_override="unpriced-cloud-model",
                                        source="fallback")],
               budget=budget)

    with pytest.raises(ModelProviderUnavailable) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert cloud_fallback.calls == []
    assert "单价未知" in info.value.message


# --------------------------------------------------------------------------- #
# failure surface
# --------------------------------------------------------------------------- #


def test_all_routes_failing_raises_a_conflict_with_per_route_reasons():
    gw = build(
        ScriptedProvider([ProviderAuthError("cloud key rejected")]),
        fallbacks=[ProviderRoute("ollama", ScriptedProvider([ProviderTransportError(
            "connection refused"), ProviderTransportError("connection refused"),
            ProviderTransportError("connection refused")]), source="fallback")],
    )

    with pytest.raises(ModelProviderUnavailable) as info:
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert isinstance(info.value, Conflict)
    assert info.value.http_status == 503
    assert "openai_compat:gpt-4o-mini" in info.value.message
    assert "ollama" in info.value.message


def test_unknown_price_on_primary_still_blocks_the_call():
    gw = build(ScriptedProvider([ok_result("should never be called")]))

    with pytest.raises(PriceUnknown):
        gw.complete(ACTOR, task_id="t1", model="totally-unpriced", prompt="hello world")


def test_privacy_guard_still_runs_before_any_call():
    provider = ScriptedProvider([ok_result("must not be used")])
    gw = build(provider)

    with pytest.raises(PermissionDenied):
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world",
                    target_domain="work", personal_source_ids=["mem-1"])

    assert provider.calls == []


def test_retry_classification_matches_the_documented_billing_boundary():
    """Transport/429/5xx retryable; auth and malformed responses are not."""
    cases = [
        (ProviderTransportError("timeout"), True),
        (ProviderRateLimited("429"), True),
        (ProviderServerError("500"), True),
        (ProviderAuthError("401"), False),
        (ProviderError("generic"), False),
    ]
    for error, expected in cases:
        assert error.retryable is expected, type(error).__name__


def test_raw_httpx_error_fails_closed_without_retry():
    """Errors the adapter did not classify are never retried (fail closed).

    Real adapters wrap httpx failures into ProviderTransportError before they
    reach the gateway; an unclassified exception must not become a second
    outbound attempt of a possibly-billed call.
    """
    provider = ScriptedProvider([
        httpx.ConnectError("connection refused"),
        ok_result("recovered"),
    ])
    gw = build(provider)

    with pytest.raises(ModelProviderUnavailable):
        # httpx errors are not ProviderError, so they surface immediately and
        # are NOT retried — failing closed instead of repeating a paid call.
        gw.complete(ACTOR, task_id="t1", model="gpt-4o-mini", prompt="hello world")

    assert len(provider.calls) == 1


def test_downstream_services_inherit_multi_provider_without_source_changes():
    """butler / companion / inference all build ModelGateway themselves.

    None of those files were touched by W4; they receive the new routing, local
    zero pricing and degradation metadata purely from the gateway change.
    """
    settings = _settings(model_provider="ollama",
                         model_base_url="http://127.0.0.1:11434",
                         model_name="qwen2.5:7b")
    butler_gateway = ButlerService(settings=settings)._gateway()

    assert butler_gateway.primary_provider_id == "ollama"
    assert butler_gateway.configured is True  # local provider needs no API key
    assert butler_gateway.resolve_pricing("qwen2.5:7b", "ollama").local_inference is True
    assert [r.provider_id for r in butler_gateway.routes] == ["ollama"]
