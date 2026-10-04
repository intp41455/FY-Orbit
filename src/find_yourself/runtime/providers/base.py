"""Shared contracts for model providers (W4 模型网关多 Provider).

Why this module exists
----------------------
``runtime/gateway.py`` owns pricing, budget and privacy guardrails. Putting the
provider-facing value types here keeps the dependency direction one-way
(``gateway -> providers``), so a provider never has to import the gateway.

Billing-safety boundary for retries (task §3, FROZEN_CONTRACT §7)
-----------------------------------------------------------------
A model call is a **paid** action, so a retry is only defensible when we can
argue the previous attempt was not (or need not be) billed:

* **Transport failure / timeout** (connection reset, DNS failure, connect or
  read timeout) — retryable. Either nothing reached the vendor or we never
  received a billable response body.
* **HTTP 429** — retryable, honouring ``Retry-After``; a throttled request is
  rejected before it is served.
* **HTTP 5xx / 408 / 409** — retryable; the failure happened before the vendor
  returned a settled completion.
* **HTTP 4xx (except 408/409/429)** — NOT retryable. Auth scopes, model names
  and payload shapes will be rejected identically on every attempt.
* **Malformed 2xx body** — NOT retryable. The upstream may already have billed
  the call; silently repeating it risks double charging, so the parsing failure
  surfaces to the caller instead.

Anything else raised by a provider propagates unchanged and is never retried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

# --------------------------------------------------------------------------- #
# Result envelope
# --------------------------------------------------------------------------- #


@dataclass
class CallResult:
    """One completed model call plus the honest routing provenance.

    ``provider_id``/``model`` identify what actually served the call, which is
    not always what the caller asked for: when the primary route fails we may
    serve through a fallback. In that case ``degraded_from`` carries the
    originally requested ``"<provider_id>:<model>"`` — degradation is always
    visible, never silent (task §1.3).
    """

    text: str
    usage: dict[str, int]
    settled_amount: Decimal = Decimal("0.0")
    provider_request_id: str = ""
    # --- W4 additions. All default to "unset" so pre-W4 constructors still work.
    provider_id: str = ""
    model: str = ""
    degraded_from: str = ""
    degraded_reason: str = ""
    attempts: int = 1
    retries: int = 0


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

#: Redacts anything that looks like a URL so a transport error can never echo a
#: full connection string back to a client (FROZEN_CONTRACT §1).
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s,;'\"]+")


def sanitize_message(message: str, limit: int = 240) -> str:
    """Bound and de-URL a vendor/transport error before it leaves the process."""
    text = _URL_RE.sub("<endpoint>", str(message or "")).strip()
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


class ProviderError(Exception):
    """Base failure raised by a provider adapter."""

    kind = "provider_error"
    retryable = False

    def __init__(self, message: str, *, status_code: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: Seconds the upstream asked us to wait (``Retry-After``), when given.
        self.retry_after = retry_after

    def describe(self) -> str:
        """Short, sanitised, log/UI-safe description (never a stack trace)."""
        core = sanitize_message(self.message)
        return f"{self.kind}({core})" if core else self.kind


class ProviderTransportError(ProviderError):
    """Connection/timeout failure: nothing billable came back — safe to retry."""

    kind = "transport"
    retryable = True


class ProviderRateLimited(ProviderError):
    """HTTP 429. Upstream refused to serve the request — safe to retry."""

    kind = "rate_limited"
    retryable = True


class ProviderServerError(ProviderError):
    """HTTP 5xx / 408 / 409 — the upstream failed before settling usage."""

    kind = "upstream_error"
    retryable = True


class ProviderAuthError(ProviderError):
    """HTTP 401/403. Retrying repeats the same refusal — never bill twice."""

    kind = "auth_rejected"


class ProviderRequestRejected(ProviderError):
    """HTTP 4xx other than 408/409/429 (bad model name, bad payload...)."""

    kind = "request_rejected"


class ProviderMalformedResponse(ProviderError):
    """HTTP 2xx the adapter could not parse — possibly already billed."""

    kind = "malformed_response"


def classify_status(status: int, message: str, retry_after: float | None = None) -> ProviderError:
    """Map an HTTP status onto the provider error whose retry policy matches."""
    safe = sanitize_message(message)
    if status == 429:
        return ProviderRateLimited(safe, status_code=status, retry_after=retry_after)
    if status in (408, 409) or 500 <= status <= 599:
        return ProviderServerError(safe, status_code=status, retry_after=retry_after)
    if status in (401, 403):
        return ProviderAuthError(safe, status_code=status)
    return ProviderRequestRejected(safe, status_code=status)


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProviderEndpoint:
    """Everything needed to construct a provider adapter.

    ``api_key`` is a secret: it is never logged, echoed or serialised into any
    catalog/health response. Only its *presence* is ever reported outward.
    """

    provider_id: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    #: httpx transport used by unit tests only (MockTransport). Production
    #: leaves it empty so real sockets are used.
    transport: Any | None = None

    def effective_model(self, fallback: str = "") -> str:
        return self.model or fallback


class ModelProvider(Protocol):
    """Structural contract every provider adapter satisfies."""

    provider_id: str
    #: True when inference happens on this machine, so there is no vendor charge.
    local_inference: bool
    #: False for endpoints that need no credential (Ollama).
    requires_api_key: bool

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
    ) -> CallResult: ...

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        """Live health probe. Raises :class:`ProviderError` when unreachable."""
        ...


@dataclass
class ProviderHealth:
    """Result of a live probe — never carries secrets or full URLs."""

    provider_id: str
    ok: bool
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    error: str = ""
    endpoint_ref: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "models": list(self.models),
            "error": self.error,
            "endpoint_ref": self.endpoint_ref,
        }
