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

W4 additions — multi-provider routing with retry + degradation
-------------------------------------------------------------

* Adapters live in ``runtime/providers/`` (``openai_compat``, ``ollama``,
  ``anthropic``). This module keeps ownership of pricing, budget and privacy.
* The primary route retries **only** failures that are safe to repeat for a
  paid call (transport errors, 429 with ``Retry-After``, 408/409/5xx) and at
  most ``max_retries`` times with exponential backoff. See
  ``providers/base.py`` for the billing-safety rationale.
* When the primary route stays down the gateway walks the fallback chain from
  ``FY_MODEL_FALLBACKS``. **Degradation is always visible**: the returned
  :class:`CallResult` carries ``degraded_from`` / ``degraded_reason`` with the
  originally requested provider:model and why it failed. Nothing silently swaps
  the model behind the user's back.
* Local providers (Ollama) are priced at zero *explicitly* because inference
  runs on this machine; that is different from an unknown remote price, which
  still raises :class:`PriceUnknown` (FROZEN_CONTRACT §7).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Protocol

from ..config import Settings
from ..db.models import Grant
from ..db.types import utcnow
from ..services.actor import Actor
from ..services.budget import BudgetService
from ..services.errors import Conflict, PermissionDenied, ValidationFailed
from ..services.offline import OfflineUnavailable, remote_block_reason
from .providers import (
    LOCAL_INFERENCE_PROVIDERS,
    OPENAI_COMPAT,
    PROVIDER_IDS,
    ProviderEndpoint,
    UnsupportedProvider,
    build_provider,
    default_base_url,
    endpoint_ref,
    infer_provider_id,
    normalize_provider_id,
    provider_descriptor,
)
from .providers.base import CallResult, ProviderError, sanitize_message
from .providers.openai_compat import OpenAICompatibleProvider

__all__ = [
    "CallResult",
    "ModelRequest",
    "ModelPricing",
    "STANDARD_PRICING",
    "LOCAL_INFERENCE_NOTE",
    "ModelGateway",
    "ModelNotConfigured",
    "ModelProvider",
    "ModelProviderUnavailable",
    "MockModelProvider",
    "OfflineUnavailable",
    "OpenAICompatibleProvider",
    "PriceUnknown",
    "ProviderRoute",
    "local_pricing",
    "PROVIDER_IDS",
]


class ModelNotConfigured(Conflict):
    def __init__(self, message: str = "Model provider is not configured"):
        super().__init__("model_not_configured", message, 503)


class PriceUnknown(ValidationFailed):
    def __init__(self, message: str = "Model unit price is unknown; refusing to charge as zero"):
        super().__init__("price_unknown", message)


class ModelProviderUnavailable(Conflict):
    """Every route in the chain failed; carries the honest per-route reasons.

    Raised instead of leaking a raw provider exception so the API layer can map
    it to the unified error envelope (FROZEN_CONTRACT §1) rather than an opaque
    500. ``reasons`` already went through :func:`sanitize_message`.
    """

    def __init__(self, requested: str, reasons: list[str]):
        detail = " | ".join(reasons) if reasons else "no route could be attempted"
        if len(detail) > 400:
            detail = detail[:397] + "..."
        super().__init__(
            "model_provider_unavailable",
            f"No configured provider could serve '{requested}': {detail}",
            503,
        )


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
    #: Human-readable provenance: vendor rate vs. local inference.
    note: str = ""
    #: True for on-device inference: zero cost is real, not an unknown default.
    local_inference: bool = False

    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        cost = (Decimal(prompt_tokens) / Decimal(1000)) * self.input_usd_per_1k + \
               (Decimal(completion_tokens) / Decimal(1000)) * self.output_usd_per_1k
        return cost.quantize(Decimal("0.000001"))


#: Marker used for locally served models so the UI can say why cost is zero.
LOCAL_INFERENCE_NOTE = "本地推理，无云端计费"


def local_pricing(model_id: str, context_window: int = 32768) -> ModelPricing:
    """Explicit zero-cost pricing for on-device models (never 'unknown as zero')."""
    return ModelPricing(
        model_id=model_id,
        input_usd_per_1k=Decimal("0"),
        output_usd_per_1k=Decimal("0"),
        context_window=context_window,
        note=LOCAL_INFERENCE_NOTE,
        local_inference=True,
    )


# Standard approved pricing catalog (explicitly audited rates per 1,000 tokens)
STANDARD_PRICING: dict[str, ModelPricing] = {
    "gpt-4o-mini": ModelPricing(
        model_id="gpt-4o-mini",
        input_usd_per_1k=Decimal("0.000150"),
        output_usd_per_1k=Decimal("0.000600"),
        context_window=128000,
        note="批准的供应商公开费率",
    ),
    "gemini-1.5-flash": ModelPricing(
        model_id="gemini-1.5-flash",
        input_usd_per_1k=Decimal("0.000075"),
        output_usd_per_1k=Decimal("0.000300"),
        context_window=1000000,
        note="批准的供应商公开费率",
    ),
    "mock-deterministic": ModelPricing(
        model_id="mock-deterministic",
        input_usd_per_1k=Decimal("0.001000"),
        output_usd_per_1k=Decimal("0.002000"),
        context_window=16384,
        note="确定性本地桩 priced deliberately high so misuse is visible",
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


@dataclass(frozen=True)
class ProviderRoute:
    """One hop in the chain: primary first, then configured fallbacks."""

    provider_id: str
    provider: Any
    model_override: str | None = None
    source: str = "primary"
    endpoint_ref: str = ""

    @property
    def label(self) -> str:
        return f"{self.provider_id}:{self.model_override or '*'}"

    def model_for(self, requested: str) -> str:
        return self.model_override or requested


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


INJECTED_PROVIDER_ID = "injected"


class ModelGateway:
    def __init__(
        self,
        settings: Settings | None = None,
        budget: BudgetService | None = None,
        provider: ModelProvider | None = None,
        pricing: dict[str, ModelPricing] | None = None,
        *,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 8.0,
        sleeper: Callable[[float], None] | None = None,
        fallbacks: list[dict[str, Any]] | None = None,
    ):
        self.settings = settings
        self.budget = budget
        self.pricing = pricing if pricing is not None else dict(STANDARD_PRICING)

        #: Populated when FY_MODEL_PROVIDER names something we cannot build.
        self.config_error: str = ""
        #: Routes we could parse but not build (bad fallback provider id...).
        self.route_errors: list[str] = []
        self.price_overrides: dict[str, ModelPricing] = self._load_price_overrides()

        #: ≤2 retries by default (task §1.3), exponential backoff.
        self.max_retries = max(0, int(max_retries))
        self.retry_base_delay = float(retry_base_delay)
        self.retry_max_delay = float(retry_max_delay)
        #: Injectable clock so tests never actually sleep.
        self._sleeper = sleeper

        #: Populated when FY_MODEL_PROVIDER names something we cannot build.
        self.config_error: str = ""
        #: Routes we could parse but not build (bad fallback provider id...).
        self.route_errors: list[str] = []
        self.routes: list[ProviderRoute] = []
        self.primary_provider_id = ""
        #: Resolved endpoint specs in chain order (holder of any credential;
        #: never serialised into a public response).
        self._endpoint_specs: list[ProviderEndpoint] = []

        if provider is not None:
            # Explicit injection wins: tests and hosts that wire their own
            # adapter get exactly one route and no fallback rewriting.
            self.provider = provider
            self.primary_provider_id = getattr(provider, "provider_id", "") or INJECTED_PROVIDER_ID
            self.routes = [ProviderRoute(self.primary_provider_id, provider, source="injected")]
        else:
            self.provider = None
            if self.settings is not None:
                self._wire_from_settings(fallbacks)

        #: 由 settings 配出来的路由快照（**对象身份**，不是 provider_id）。
        #: 离线门只拦这些——它们才代表「应用自己会去连外网」的路径；
        #: 宿主注入的适配器与测试替换的替身不在此列（见 offline_block_reason）。
        self._settings_wired: list[ProviderRoute] = (
            [] if provider is not None else list(self.routes)
        )

    # -- wiring ------------------------------------------------------------
    def _load_price_overrides(self) -> dict[str, ModelPricing]:
        raw = getattr(self.settings, "model_price_overrides", None) if self.settings else None
        out: dict[str, ModelPricing] = {}
        for model_id, spec in (raw or {}).items():
            try:
                out[str(model_id)] = ModelPricing(
                    model_id=str(model_id),
                    input_usd_per_1k=Decimal(str(spec.get("input_usd_per_1k", "0"))),
                    output_usd_per_1k=Decimal(str(spec.get("output_usd_per_1k", "0"))),
                    context_window=int(spec.get("context_window", 128000)),
                    note="按 FY_MODEL_PRICE_OVERRIDES 配置价计费",
                )
            except (ArithmeticError, TypeError, ValueError, AttributeError):
                # A malformed override must not silently become "free"; the model
                # then falls through to the normal unknown-price guard.
                self.route_errors.append(
                    f"price override for '{model_id}' is malformed and was ignored")
        return out

    def _wire_from_settings(self, fallbacks: list[dict[str, Any]] | None) -> None:
        assert self.settings is not None
        explicit_raw = str(getattr(self.settings, "model_provider", "") or "")
        try:
            explicit = normalize_provider_id(explicit_raw)
        except UnsupportedProvider as exc:
            self.config_error = sanitize_message(str(exc))
            return
        api_key = str(getattr(self.settings, "model_api_key", "") or "")
        base_url = str(getattr(self.settings, "model_base_url", "") or "")
        self.primary_provider_id = infer_provider_id(
            explicit=explicit, api_key=api_key, base_url=base_url)

        if not self._endpoint_ready(self.primary_provider_id, api_key, base_url):
            return
        primary_spec = ProviderEndpoint(
            provider_id=self.primary_provider_id,
            base_url=base_url or default_base_url(self.primary_provider_id),
            api_key=api_key,
        )
        primary = self._build_from_spec(primary_spec)
        if primary is None:
            return
        self.provider = primary
        self._endpoint_specs.append(primary_spec)
        self.routes.append(ProviderRoute(
            self.primary_provider_id, primary, source="primary",
            endpoint_ref=endpoint_ref(str(primary_spec.base_url))))

        specs = fallbacks if fallbacks is not None else list(
            getattr(self.settings, "model_fallbacks", None) or [])
        for index, spec in enumerate(specs):
            if not isinstance(spec, dict):
                self.route_errors.append(f"fallback #{index} is not an object and was ignored")
                continue
            raw_id = spec.get("provider")
            try:
                fid = normalize_provider_id(str(raw_id or ""))
            except UnsupportedProvider as exc:
                self.route_errors.append(sanitize_message(str(exc)))
                continue
            if not fid:
                self.route_errors.append(f"fallback #{index} has no provider id and was ignored")
                continue
            f_base = str(spec.get("base_url") or "")
            f_key = str(spec.get("api_key") or "")
            fallback_spec = self._resolve_fallback_endpoint(
                fid, f_base, f_key, base_url, api_key, index)
            if fallback_spec is None:
                continue
            built = self._build_from_spec(fallback_spec)
            if built is None:
                continue
            model_override = str(spec.get("model") or "") or None
            self._endpoint_specs.append(fallback_spec)
            self.routes.append(
                ProviderRoute(fid, built, model_override=model_override, source="fallback",
                              endpoint_ref=endpoint_ref(str(fallback_spec.base_url))))

    def _resolve_fallback_endpoint(self, fid: str, f_base: str, f_key: str,
                                   base_url: str, api_key: str,
                                   index: int) -> ProviderEndpoint | None:
        """Decide which endpoint a fallback rung actually points at.

        Credential inheritance across **different vendors** is refused rather
        than guessed: posting an OpenAI key to Anthropic is not something the
        gateway should improvise. Local providers need no credential and fall
        back to their own well-known port. The refusal is recorded in
        ``route_errors`` so it shows up in the catalog instead of vanishing.
        """
        same_provider = fid == self.primary_provider_id
        if not same_provider and not f_base and not f_key:
            # Cross-vendor, nothing explicit: only local providers can self-default.
            if fid in LOCAL_INFERENCE_PROVIDERS:
                return ProviderEndpoint(provider_id=fid, base_url=default_base_url(fid), api_key="")
            self.route_errors.append(
                f"fallback #{index} ({fid}) 与主 provider 不同且未显式给出 "
                f"base_url/api_key，拒绝跨厂商继承凭据，未加入降级链")
            return None
        resolved_base = f_base or (base_url if same_provider else default_base_url(fid))
        resolved_key = f_key or (api_key if same_provider or f_base else "")
        if not self._endpoint_ready(fid, resolved_key, resolved_base):
            self.route_errors.append(
                f"fallback {fid} 端点未就绪（缺少 base_url 或凭据），未加入降级链")
            return None
        return ProviderEndpoint(provider_id=fid, base_url=resolved_base, api_key=resolved_key)

    def _endpoint_ready(self, provider_id: str, api_key: str, base_url: str) -> bool:
        """Does this provider have enough configuration to be callable?

        Ollama needs only an address (and falls back to its default port);
        everything else needs a credential too — unchanged from pre-W4.
        """
        if provider_id in LOCAL_INFERENCE_PROVIDERS:
            return True
        return bool(api_key and base_url)

    def _build_from_spec(self, spec: ProviderEndpoint) -> Any | None:
        """Instantiate a provider from a resolved endpoint spec."""
        try:
            return build_provider(spec)
        except UnsupportedProvider as exc:
            # Only reachable for ids the factory does not know.
            self.config_error = sanitize_message(str(exc))
            return None

    # -- configuration surface --------------------------------------------
    def endpoints(self) -> list[ProviderEndpoint]:
        """Resolved endpoints in chain order — consumed by the health probe."""
        return list(self._endpoint_specs)

    def provider_chain(self) -> list[dict[str, Any]]:
        """Secret-free description of the current chain (for the catalog UI)."""
        rows: list[dict[str, Any]] = []
        for route in self.routes:
            meta = provider_descriptor(route.provider_id)
            rows.append({
                "provider_id": route.provider_id,
                "name": meta["name"],
                "model": route.model_override or (getattr(self.settings, "model_name", "") or ""),
                "source": route.source,
                "local_inference": meta["local_inference"],
                "endpoint_ref": route.endpoint_ref,
            })
        return rows

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
        """True when the gateway actually holds a callable provider adapter."""
        if self.provider is not None:
            return True
        if self.settings is None:
            return False
        # Mirror the pre-W4 definition so existing callers see the same answer.
        if self.config_error:
            return False
        primary = self.primary_provider_id or OPENAI_COMPAT
        if primary in LOCAL_INFERENCE_PROVIDERS:
            return True
        return bool(self.settings.model_api_key and self.settings.model_base_url)

    def require_configured(self) -> None:
        if not self.configured and self.provider is None:
            raise ModelNotConfigured()

    def get_pricing(self, model: str) -> ModelPricing:
        if model not in self.pricing:
            raise PriceUnknown(f"Model unit price is unknown for '{model}'; refusing to charge as zero")
        return self.pricing[model]

    def resolve_pricing(self, model: str, provider_id: str | None = None) -> ModelPricing:
        """Price lookup honouring explicit overrides and local inference.

        Order: static catalog → configured override → local provider (explicit
        zero) → :class:`PriceUnknown`. FROZEN_CONTRACT §7 forbids treating an
        unknown *remote* price as zero; local pricing is a different, stated case.
        """
        if model in self.pricing:
            return self.pricing[model]
        override = self.price_overrides.get(model)
        if override is not None:
            return override
        if provider_id in LOCAL_INFERENCE_PROVIDERS:
            return local_pricing(model)
        raise PriceUnknown(f"Model unit price is unknown for '{model}'; refusing to charge as zero")

    def estimated_cost(self, model: str, max_tokens: int, estimated_prompt_tokens: int = 500) -> Decimal:
        pricing = self.get_pricing(model)
        return pricing.calculate_cost(estimated_prompt_tokens, max_tokens)

    # -- resilience internals ---------------------------------------------
    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if self._sleeper is not None:
            self._sleeper(seconds)
        else:
            time.sleep(seconds)

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, min(float(retry_after), self.retry_max_delay))
        return min(self.retry_base_delay * (2 ** (attempt - 1)), self.retry_max_delay)

    def _call_with_retry(self, route: ProviderRoute, model: str, prompt: str,
                         max_tokens: int, timeout_seconds: float) -> CallResult:
        """One route, up to ``1 + max_retries`` attempts.

        Only :class:`ProviderError` instances flagged ``retryable`` are retried;
        any other exception propagates untouched so it can never be silently
        converted into a second billable call.
        """
        attempts = 0
        while True:
            attempts += 1
            try:
                result = route.provider.complete(
                    model=model,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds,
                )
            except ProviderError as exc:
                if not exc.retryable or attempts > self.max_retries:
                    raise
                self._sleep(self._backoff_delay(attempts, exc.retry_after))
                continue
            result.attempts = attempts
            result.retries = attempts - 1
            return result

    @staticmethod
    def _describe_failure(route: ProviderRoute, model: str, exc: BaseException) -> str:
        if isinstance(exc, ProviderError):
            reason = exc.describe()
        else:
            name = type(exc).__name__
            detail = sanitize_message(str(exc))
            reason = f"{name}: {detail}" if detail else name
        return f"{route.provider_id}:{model} → {reason}"

    # -- offline gate ------------------------------------------------------ #
    def offline_block_reason(self, route: ProviderRoute) -> str:
        """该路由是否被离线门拦住；允许时返回空串。

        门只拦**由 settings 配出来的远程路由**（:attr:`_settings_wired`）——
        那才是应用自己会去连外网的路径。两类路由刻意不在门内，因为拦它们
        是拦错东西：

        * 宿主 ``provider=`` 显式注入的适配器（自带推理 / 本地桩 / 测试替身）
          —— 显式接线不是「悄悄出网」，出不出网由宿主自己负责；
        * 本地推理 provider（如 ollama，连的是本机 socket）—— 「默认离线」
          拦的是**外网**，不是把本机模型也一起关掉；本机连不上时它自己会失败。
        """
        if not any(route is wired for wired in self._settings_wired):
            return ""
        if route.provider_id in LOCAL_INFERENCE_PROVIDERS:
            return ""
        # 用本网关自己的 settings，不是全局单例：宿主可以拿一份「联网」的配置
        # 单独建网关（否则 FY_OFFLINE_MODE=0 会被全局单例的默认离线吃掉）。
        return remote_block_reason(f"远程 provider「{route.provider_id}」", self.settings)

    # -- public API --------------------------------------------------------
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
        """Guarded model call: offline → privacy → price → reserve → call → settle.

        Default-offline (A-离线优先-01/03): settings-wired remote routes never leave
        the machine while ``FY_OFFLINE_MODE`` is on (default); local-inference routes
        and host-injected adapters are unaffected. Walking the *provider chain* still
        happens — every fallback hop is recorded on the returned
        result (``degraded_from`` / ``degraded_reason``) so the UI always shows
        which provider actually answered.
        """
        # 0. 离线门先于「未配置模型」：默认离线时远程路由本就出不了网，
        #    此时报 model_not_configured 是把用户支去配一个仍然用不上的 key——
        #    真正的原因是离线，就先报离线。
        if self.routes and all(self.offline_block_reason(r) for r in self.routes):
            raise OfflineUnavailable(
                f"远程模型调用（{self.primary_provider_id or 'model'}）")

        self.require_configured()
        if not self.routes:
            if self.config_error:
                raise ModelNotConfigured(
                    f"Model provider configuration is invalid: {self.config_error}")
            raise ModelNotConfigured("Model provider adapter is not wired; no outbound call made")

        # 1. Domain Privacy Check (R03)
        self.validate_outbound_privacy(
            ModelRequest(domain=target_domain, prompt=prompt, personal_source_ids=personal_source_ids),
            grants=grants,
        )

        requested_label = f"{self.primary_provider_id}:{model}"
        est_prompt_tokens = max(1, len(prompt.split()))
        reasons: list[str] = list(self.route_errors)
        last_exc: BaseException | None = None

        for index, route in enumerate(self.routes):
            target_model = route.model_for(model)

            # 0. 离线门（A-离线优先-01/03）：默认离线时远程路由**不出网**。
            #    拦下要带原因，让降级链照常往下走（本地路由顶上也如实标
            #    degraded_from/degraded_reason），而不是静默失败。
            offline_reason = self.offline_block_reason(route)
            if offline_reason:
                reasons.append(f"{route.provider_id}:{target_model} → {offline_reason}")
                continue

            # 2. Price calculation — per *actually executed* provider (task §3).
            try:
                pricing = self.resolve_pricing(target_model, route.provider_id)
            except PriceUnknown as exc:
                if index == 0:
                    raise
                reasons.append(f"{route.provider_id}:{target_model} 单价未知，跳过（不得按零费用放行）")
                last_exc = exc
                continue

            estimated_cost = pricing.calculate_cost(est_prompt_tokens, max_tokens)

            # 3. Atomic budget reservation. Zero-cost local inference has nothing
            # to reserve — BudgetService rejects non-positive amounts.
            res = None
            if self.budget is not None and estimated_cost > 0:
                res = self.budget.reserve(
                    actor,
                    task_id=task_id,
                    amount=estimated_cost,
                    idempotency_key=f"call:{task_id}:{route.provider_id}:{target_model}:{abs(hash(prompt))}",
                    scope="inference",
                )

            # 4. Outbound provider call with bounded retries.
            try:
                outcome = self._call_with_retry(
                    route, target_model, prompt, max_tokens, timeout_seconds)
            except Exception as exc:
                if self.budget is not None and res is not None:
                    self.budget.release(actor, res.id)
                reasons.append(self._describe_failure(route, target_model, exc))
                last_exc = exc
                continue

            # 5. Exact usage settlement against the *actual* provider's price.
            actual_p = outcome.usage.get("prompt_tokens", est_prompt_tokens)
            actual_c = outcome.usage.get("completion_tokens", len(outcome.text.split()))
            actual_cost = pricing.calculate_cost(actual_p, actual_c)
            if self.budget is not None and res is not None:
                self.budget.settle(actor, res.id, actual_cost)

            outcome.settled_amount = actual_cost
            outcome.provider_id = route.provider_id
            outcome.model = target_model
            if index > 0:
                # Degradation is never silent: remember what was asked for and why.
                outcome.degraded_from = requested_label
                outcome.degraded_reason = " | ".join(reasons)
            return outcome

        raise ModelProviderUnavailable(requested_label, reasons) from last_exc
