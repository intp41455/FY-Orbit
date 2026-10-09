"""OpenAI-compatible provider (deepseek / openai / vllm / ollama-compat endpoints).

This is the pre-W4 production adapter moved out of ``runtime/gateway.py`` so all
providers share one package. Behaviour is unchanged except that HTTP failures
now surface as :class:`ProviderError` subclasses whose ``retryable`` flag is
derived from the documented billing-safety boundary (see ``base.py``).
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import (
    CallResult,
    ProviderEndpoint,
    ProviderMalformedResponse,
    ProviderTransportError,
    classify_status,
    normalize_messages,
    approx_tokens as _approx_tokens,
    sanitize_message,
)

PROVIDER_ID = "openai_compat"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider:
    """Provider for OpenAI-compatible ``POST /chat/completions`` endpoints."""

    provider_id = PROVIDER_ID
    local_inference = False
    requires_api_key = True

    def __init__(self, api_key: str, base_url: str, transport: Any | None = None):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.transport = transport

    # -- internals ---------------------------------------------------------
    def _client(self, timeout_seconds: float) -> httpx.Client:
        kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.Client(**kwargs)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        try:
            with self._client(timeout_seconds) as client:
                resp = client.post(f"{self.base_url}{path}", headers=self._headers(), json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTransportError(f"Request timed out after {timeout_seconds}s", retry_after=None) from exc
        except httpx.RequestError as exc:
            # Transport-level failure: nothing billable came back.
            raise ProviderTransportError(sanitize_message(str(exc))) from exc
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text or resp.reason_phrase, parse_retry_after(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderMalformedResponse("Response body was not valid JSON") from exc

    def _get(self, path: str, timeout_seconds: float) -> dict[str, Any]:
        try:
            with self._client(timeout_seconds) as client:
                resp = client.get(f"{self.base_url}{path}", headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ProviderTransportError(f"Health probe timed out after {timeout_seconds}s") from exc
        except httpx.RequestError as exc:
            raise ProviderTransportError(sanitize_message(str(exc))) from exc
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text or resp.reason_phrase, parse_retry_after(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderMalformedResponse("Health probe body was not valid JSON") from exc

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
        conversation = normalize_messages(messages, prompt)
        payload = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_tokens,
        }
        data = self._post("/chat/completions", payload, timeout_seconds)
        try:
            choices = data["choices"]
            text = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # Upstream answered 2xx; it may already have billed us. Do not retry.
            raise ProviderMalformedResponse("Response contained no choices[0].message.content") from exc

        usage = data.get("usage") or {}
        try:
            p_tokens = int(usage.get("prompt_tokens") or _approx_tokens(conversation))
            c_tokens = int(usage.get("completion_tokens") or len(text.split()))
        except (TypeError, ValueError) as exc:
            raise ProviderMalformedResponse("Usage block was not numeric") from exc

        return CallResult(
            text=text,
            usage={"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                   "total_tokens": p_tokens + c_tokens},
            provider_request_id=str(data.get("id") or ""),
            provider_id=self.provider_id,
            model=model,
        )

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        """``GET /models`` — the standard OpenAI-compatible listing."""
        data = self._get("/models", timeout_seconds)
        rows = data.get("data") if isinstance(data, dict) else None
        if rows is None:
            return []
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                out.append(str(row["id"]))
        return out


def parse_retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def build(spec: ProviderEndpoint) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(api_key=spec.api_key, base_url=spec.base_url,
                                    transport=spec.transport)
