"""Ollama native provider — local inference, no credential required.

Ollama serves ``POST /api/chat`` and ``GET /api/tags`` on the user's own
machine. Two consequences drive this adapter:

* **No API key.** Absence of a credential is not a misconfiguration here, so
  the gateway must treat "base URL configured" as sufficient.
* **Zero cost.** Inference runs on local hardware; there is no vendor invoice.
  The gateway therefore prices these models at 0 explicitly (see
  ``ModelGateway.resolve_pricing``) rather than treating them as "unknown price
  silently charged as zero" — the frozen contract only forbids the latter.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import (
    CallResult,
    normalize_messages,
    ProviderEndpoint,
    ProviderMalformedResponse,
    ProviderTransportError,
    classify_status,
    sanitize_message,
)

PROVIDER_ID = "ollama"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
#: Ollama's conventional port, used to infer the provider when only a base URL
#: is configured ("Ollama 一键", acceptance item 1).
DEFAULT_PORT = 11434


class OllamaProvider:
    """Provider for the Ollama native chat API."""

    provider_id = PROVIDER_ID
    local_inference = True
    requires_api_key = False

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str = "", transport: Any | None = None):
        # ``api_key`` is accepted and ignored: Ollama needs none, but the
        # factory builds every provider from the same spec shape.
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.transport = transport

    # -- internals ---------------------------------------------------------
    def _client(self, timeout_seconds: float) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None,
                 timeout_seconds: float) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with self._client(timeout_seconds) as client:
                if method == "GET":
                    resp = client.get(url)
                else:
                    resp = client.post(url, json=payload or {})
        except httpx.TimeoutException as exc:
            raise ProviderTransportError(f"Request timed out after {timeout_seconds}s") from exc
        except httpx.RequestError as exc:
            raise ProviderTransportError(sanitize_message(str(exc))) from exc
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text or resp.reason_phrase)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderMalformedResponse("Response body was not valid JSON") from exc

    # -- ModelProvider -----------------------------------------------------
    def complete(
        self,
        *,
        model: str,
        prompt: str = "",
        max_tokens: int = 1024,
        timeout_seconds: float = 30.0,
        messages: list[dict[str, Any]] | None = None,
    ) -> CallResult:
        # /api/chat 的 messages 与 OpenAI 同形，归一化后可直接透传。
        conversation = normalize_messages(messages, prompt)
        payload: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        data = self._request("POST", "/api/chat", payload, timeout_seconds)
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Ollama /api/chat returned a non-object body")
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderMalformedResponse("Ollama response contained no message.content") from exc

        # Ollama reports actual token counts; absence is not an error, it just
        # means we fall back to whitespace estimates as before.
        p_tokens = _as_int(data.get("prompt_eval_count"), len(prompt.split()))
        c_tokens = _as_int(data.get("eval_count"), len(text.split()))
        return CallResult(
            text=text,
            usage={"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                   "total_tokens": p_tokens + c_tokens},
            provider_request_id=str(data.get("created_at") or ""),
            provider_id=self.provider_id,
            model=model,
        )

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        """``GET /api/tags`` doubles as the local health probe."""
        data = self._request("GET", "/api/tags", None, timeout_seconds)
        rows = data.get("models") if isinstance(data, dict) else None
        out: list[str] = []
        for row in rows or []:
            if isinstance(row, dict):
                name = row.get("name") or row.get("model")
                if name:
                    out.append(str(name))
        return out


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return max(1, default)


def looks_like_ollama(base_url: str) -> bool:
    """True when a bare base URL points at an Ollama daemon."""
    lowered = (base_url or "").strip().lower()
    if not lowered:
        return False
    return f":{DEFAULT_PORT}" in lowered or lowered.rstrip("/").endswith(f":{DEFAULT_PORT}")


def build(spec: ProviderEndpoint) -> OllamaProvider:
    return OllamaProvider(base_url=spec.base_url, api_key=spec.api_key, transport=spec.transport)
