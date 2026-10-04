"""Provider registry and factory (W4 模型网关多 Provider).

The factory is the single place that knows which provider ids exist, what their
default endpoint is, whether they need a credential, and whether inference runs
locally. ``runtime/gateway.py`` asks here for adapters; nothing else reaches
into a concrete provider module.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .anthropic import PROVIDER_ID as ANTHROPIC_ID
from .anthropic import build as build_anthropic
from .base import ProviderEndpoint, ProviderError, ProviderHealth, sanitize_message
from .ollama import DEFAULT_BASE_URL as OLLAMA_URL
from .ollama import PROVIDER_ID as OLLAMA_ID
from .ollama import build as build_ollama
from .ollama import looks_like_ollama
from .openai_compat import DEFAULT_BASE_URL as OPENAI_URL
from .openai_compat import PROVIDER_ID as OPENAI_ID
from .openai_compat import build as build_openai

OPENAI_COMPAT = OPENAI_ID
OLLAMA = OLLAMA_ID
ANTHROPIC = ANTHROPIC_ID

#: Stable order used by the catalog UI and the health probe.
PROVIDER_IDS: tuple[str, ...] = (OPENAI_ID, OLLAMA_ID, ANTHROPIC_ID)

#: Providers whose inference runs on this machine: zero vendor cost, but still
#: a real dependency the health probe must verify.
LOCAL_INFERENCE_PROVIDERS: frozenset[str] = frozenset({OLLAMA_ID})

DEFAULT_BASE_URLS: dict[str, str] = {
    OPENAI_ID: OPENAI_URL,
    OLLAMA_ID: OLLAMA_URL,
    ANTHROPIC_ID: "https://api.anthropic.com",
}

_HEALTH_PATHS: dict[str, str] = {
    OPENAI_ID: "/models",
    OLLAMA_ID: "/api/tags",
    ANTHROPIC_ID: "/v1/models",
}

_DISPLAY_NAMES: dict[str, str] = {
    OPENAI_ID: "OpenAI 兼容接口",
    OLLAMA_ID: "Ollama 本地模型",
    ANTHROPIC_ID: "Anthropic Messages API",
}

#: Accepted spellings so FY_MODEL_PROVIDER tolerates obvious variants.
_ALIASES: dict[str, str] = {
    "openai": OPENAI_ID,
    "openai-compatible": OPENAI_ID,
    "openai_compatible": OPENAI_ID,
    "claude": ANTHROPIC_ID,
    "anthropic-messages": ANTHROPIC_ID,
}

_BUILDERS: dict[str, Callable[[ProviderEndpoint], Any]] = {
    OPENAI_ID: build_openai,
    OLLAMA_ID: build_ollama,
    ANTHROPIC_ID: build_anthropic,
}


class UnsupportedProvider(ValueError):
    """Raised for an explicit FY_MODEL_PROVIDER value we cannot build."""

    def __init__(self, raw: str):
        supported = ", ".join(PROVIDER_IDS)
        super().__init__(f"Unsupported model provider '{raw}'; supported providers: {supported}")


def normalize_provider_id(raw: str | None) -> str:
    """Normalise an explicitly configured provider id.

    Unknown values raise rather than silently degrading to OpenAI-compatible —
    a typo would otherwise produce confusing downstream errors (诚实原则).
    """
    value = (raw or "").strip().lower()
    if not value:
        return ""
    if value in _ALIASES:
        return _ALIASES[value]
    if value in PROVIDER_IDS:
        return value
    raise UnsupportedProvider(raw or value)


def infer_provider_id(*, explicit: str = "", api_key: str = "", base_url: str = "") -> str:
    """Resolve the provider id, preserving pre-W4 behaviour by default.

    * An explicit ``FY_MODEL_PROVIDER`` always wins.
    * Otherwise we stay on ``openai_compat`` (the only provider that existed
      before W4) **except** when the configured base URL plainly addresses an
      Ollama daemon and no credential is set — that is the "Ollama 一键" case,
      where requiring an API key would only produce a false 'not configured'.
    """
    explicit_id = normalize_provider_id(explicit)
    if explicit_id:
        return explicit_id
    if not api_key and looks_like_ollama(base_url):
        return OLLAMA_ID
    return OPENAI_ID


def default_base_url(provider_id: str) -> str:
    return DEFAULT_BASE_URLS.get(provider_id, "")


def endpoint_ref(base_url: str) -> str:
    """Host + path only. Query strings and userinfo never reach the UI."""
    if not base_url:
        return ""
    return base_url.split("?", 1)[0].rstrip("/")


def build_provider(spec: ProviderEndpoint) -> Any:
    builder = _BUILDERS.get(spec.provider_id)
    if builder is None:
        raise UnsupportedProvider(spec.provider_id)
    return builder(spec)


def provider_descriptor(provider_id: str) -> dict[str, Any]:
    """Static, secret-free metadata for the catalog UI."""
    return {
        "provider_id": provider_id,
        "name": _DISPLAY_NAMES.get(provider_id, provider_id),
        "requires_api_key": provider_id not in LOCAL_INFERENCE_PROVIDERS,
        "local_inference": provider_id in LOCAL_INFERENCE_PROVIDERS,
        "default_base_url": DEFAULT_BASE_URLS.get(provider_id, ""),
        "health_path": _HEALTH_PATHS.get(provider_id, ""),
    }


def probe_provider(spec: ProviderEndpoint, *, timeout_seconds: float = 5.0) -> ProviderHealth:
    """Live probe: real round trip, real latency, real error text.

    No mocking, no assumed success — a provider we cannot reach reports ``ok``
    false with the sanitised reason the adapter actually produced.
    """
    started = time.perf_counter()
    try:
        provider = build_provider(spec)
        models = provider.probe_models(timeout_seconds=timeout_seconds)
    except ProviderError as exc:
        latency = int(round((time.perf_counter() - started) * 1000))
        return ProviderHealth(
            provider_id=spec.provider_id,
            ok=False,
            latency_ms=latency,
            models=[],
            error=exc.describe(),
            endpoint_ref=endpoint_ref(spec.base_url or default_base_url(spec.provider_id)),
        )
    except UnsupportedProvider as exc:
        return ProviderHealth(
            provider_id=spec.provider_id,
            ok=False,
            models=[],
            error=sanitize_message(str(exc)),
            endpoint_ref="",
        )
    latency = int(round((time.perf_counter() - started) * 1000))
    return ProviderHealth(
        provider_id=spec.provider_id,
        ok=True,
        latency_ms=latency,
        models=list(models),
        endpoint_ref=endpoint_ref(spec.base_url or default_base_url(spec.provider_id)),
    )


__all__ = [
    "ANTHROPIC",
    "DEFAULT_BASE_URLS",
    "LOCAL_INFERENCE_PROVIDERS",
    "OLLAMA",
    "OPENAI_COMPAT",
    "PROVIDER_IDS",
    "ProviderEndpoint",
    "ProviderHealth",
    "UnsupportedProvider",
    "build_provider",
    "default_base_url",
    "endpoint_ref",
    "infer_provider_id",
    "looks_like_ollama",
    "normalize_provider_id",
    "probe_provider",
    "provider_descriptor",
]
