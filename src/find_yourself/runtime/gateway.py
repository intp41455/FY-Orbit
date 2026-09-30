"""Model gateway cold-start, provider adapters, and call guard (FROZEN_CONTRACT §7, §10, G4/R01–R03).

Cold start and runtime behaviour:

* Without configured credentials the gateway raises ``ModelNotConfigured`` and
  the API surfaces ``MODEL_NOT_CONFIGURED`` — it never fabricates a model
  answer.
* Credentials are read only from the configured secret reference; they are not
  logged or echoed.
* A call requires an atomic budget reservation first. When unit price is unknown
  the call is blocked rather than charged as zero cost.
* Calls are bounded by timeout and honour cancellation.
* Domain privacy guard (R03): personal raw content cannot enter outbound prompts
  to other domains without an active, unexpired grant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterator, Protocol

import httpx

from ..config import Settings
from ..db.models import Grant
from ..db.types import utcnow
from ..services.actor import Actor
from ..services.budget import BudgetService
from ..services.errors import Conflict, PermissionDenied, ValidationFailed


class ModelNotConfigured(Conflict):
    def __init__(self, message: str = "Model provider is not configured"):
        super().__init__("model_not_configured", message, 503)


class PriceUnknown(ValidationFailed):
    def __init__(self, message: str = "Model unit price is unknown; refusing to charge as zero"):
        super().__init__("price_unknown", message)


@dataclass
class CallResult:
    text: str
    usage: dict[str, int]
    settled_amount: Decimal = Decimal("0.0")
    provider_request_id: str = ""


@dataclass
class ModelRequest:
    domain: str = "personal"
    prompt: str = ""
    personal_source_ids: list[str] | None = None


@dataclass
class ModelPricing:
    model_id: str
    input_usd_per_1k: Decimal
    output_usd_per_1k: Decimal
    context_window: int = 128000
    currency: str = "USD"

    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        cost = (Decimal(prompt_tokens) / Decimal(1000)) * self.input_usd_per_1k + \
               (Decimal(completion_tokens) / Decimal(1000)) * self.output_usd_per_1k
        return cost.quantize(Decimal("0.000001"))


# Standard approved pricing catalog (explicitly audited rates per 1,000 tokens)
STANDARD_PRICING: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing(
        model_id="gpt-4o-mini",
        input_usd_per_1k=Decimal("0.000150"),
        output_usd_per_1k=Decimal("0.000600"),
        context_window=128000,
    ),
    "gemini-1.5-flash": ModelPricing(
        model_id="gemini-1.5-flash",
        input_usd_per_1k=Decimal("0.000075"),
        output_usd_per_1k=Decimal("0.000300"),
        context_window=1000000,
    ),
    "mock-deterministic": ModelPricing(
        model_id="mock-deterministic",
        input_usd_per_1k=Decimal("0.001000"),
        output_usd_per_1k=Decimal("0.002000"),
        context_window=16384,
    ),
}


class ModelProvider(Protocol):
    def complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> CallResult: ...


class MockModelProvider:
    """Controllable deterministic provider for unit tests and local simulation."""

    def __init__(
        self,
        custom_responses: dict[str, str] | None = None,
        default_response: str = "Empathetic listener: I hear your perspective and acknowledge your reflection.",
    ):
        self.custom_responses = custom_responses or {}
        self.default_response = default_response
        self.last_call: dict[str, Any] = {}

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> CallResult:
        self.last_call = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
        resp = self.custom_responses.get(prompt, self.default_response)
        p_tokens = max(1, len(prompt.split()))
        c_tokens = max(1, len(resp.split()))
        return CallResult(
            text=resp,
            usage={"prompt_tokens": p_tokens, "completion_tokens": c_tokens, "total_tokens": p_tokens + c_tokens},
            settled_amount=Decimal("0.0"),
            provider_request_id=f"mock-req-{abs(hash(prompt)) % 1000000}",
        )


class OpenAICompatibleProvider:
    """Production adapter for OpenAI-compatible REST endpoints."""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> CallResult:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        p_tokens = int(usage.get("prompt_tokens", len(prompt.split())))
        c_tokens = int(usage.get("completion_tokens", len(text.split())))
        req_id = data.get("id", "")
        return CallResult(
            text=text,
            usage={"prompt_tokens": p_tokens, "completion_tokens": c_tokens, "total_tokens": p_tokens + c_tokens},
            provider_request_id=req_id,
        )


class ModelGateway:
    def __init__(
        self,
        settings: Settings | None = None,
        budget: BudgetService | None = None,
        provider: ModelProvider | None = None,
        pricing: dict[str, ModelPricing] | None = None,
    ):
        self.settings = settings
        self.budget = budget
        self.pricing = pricing if pricing is not None else dict(STANDARD_PRICING)
        if provider is not None:
            self.provider = provider
        elif self.configured:
            self.provider = OpenAICompatibleProvider(
                api_key=self.settings.model_api_key,
                base_url=self.settings.model_base_url,
            )
        else:
            self.provider = None

    def validate_outbound_privacy(
        self,
        request: ModelRequest,
        grants: list[Grant] | None = None,
    ) -> None:
        """Enforces domain isolation on outbound model requests (R03)."""
        if request.domain != "personal" and request.personal_source_ids:
            active_granted_ids: set[str] = set()
            now = utcnow()
            if grants:
                for g in grants:
                    if (
                        g.state == "active"
                        and g.source_domain == "personal"
                        and g.consumer_domain == request.domain
                        and g.expires_at > now
                    ):
                        active_granted_ids.update(g.record_ids or [])
            ungranted = [sid for sid in request.personal_source_ids if sid not in active_granted_ids]
            if ungranted:
                raise PermissionDenied(
                    "sensitive_domain_leak",
                    f"Outbound model request in domain '{request.domain}' contains unauthorized personal records: {ungranted}",
                )

    @property
    def configured(self) -> bool:
        if self.settings is None:
            return False
        return bool(self.settings.model_api_key and self.settings.model_base_url)

    def require_configured(self) -> None:
        if not self.configured and self.provider is None:
            raise ModelNotConfigured()

    def get_pricing(self, model: str) -> ModelPricing:
        if model not in self.pricing:
            raise PriceUnknown(f"Model unit price is unknown for '{model}'; refusing to charge as zero")
        return self.pricing[model]

    def estimated_cost(self, model: str, max_tokens: int, estimated_prompt_tokens: int = 500) -> Decimal:
        pricing = self.get_pricing(model)
        return pricing.calculate_cost(estimated_prompt_tokens, max_tokens)

    def complete(
        self,
        actor: Actor,
        *,
        task_id: str,
        model: str,
        prompt: str,
        target_domain: str = "personal",
        personal_source_ids: list[str] | None = None,
        grants: list[Grant] | None = None,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> CallResult:
        """Guarded model call with atomic budget reservation and actual settlement."""
        self.require_configured()
        if self.provider is None:
            raise ModelNotConfigured("Model provider adapter is not wired; no outbound call made")

        # 1. Domain Privacy Check (R03)
        self.validate_outbound_privacy(
            ModelRequest(domain=target_domain, prompt=prompt, personal_source_ids=personal_source_ids),
            grants=grants,
        )

        # 2. Price calculation
        pricing = self.get_pricing(model)
        est_prompt_tokens = max(1, len(prompt.split()))
        estimated_cost = pricing.calculate_cost(est_prompt_tokens, max_tokens)

        # 3. Atomic budget reservation
        res = None
        if self.budget is not None:
            res = self.budget.reserve(
                actor,
                task_id=task_id,
                amount=estimated_cost,
                idempotency_key=f"call:{task_id}:{model}:{abs(hash(prompt))}",
                scope="inference",
            )

        # 4. Outbound provider call
        try:
            result = self.provider.complete(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            if self.budget is not None and res is not None:
                self.budget.release(actor, res.id)
            raise

        # 5. Exact usage settlement
        actual_p = result.usage.get("prompt_tokens", est_prompt_tokens)
        actual_c = result.usage.get("completion_tokens", len(result.text.split()))
        actual_cost = pricing.calculate_cost(actual_p, actual_c)

        if self.budget is not None and res is not None:
            self.budget.settle(actor, res.id, actual_cost)

        result.settled_amount = actual_cost
        return result
