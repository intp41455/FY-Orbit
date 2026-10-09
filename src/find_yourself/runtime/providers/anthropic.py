"""Anthropic Messages API provider.

Uses ``POST /v1/messages`` with the ``x-api-key`` / ``anthropic-version``
headers and ``GET /v1/models`` for health probing. Retry policy follows the
shared billing-safety rules in ``base.py``: transport failures, 429 and 5xx are
retryable; 401/403 and other 4xx are not.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import (
    CallResult,
    ProviderEndpoint,
    ProviderMalformedResponse,
    ProviderTransportError,
    approx_tokens,
    classify_status,
    normalize_messages,
    sanitize_message,
)

PROVIDER_ID = "anthropic"
DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Provider for Anthropic's native Messages API."""

    provider_id = PROVIDER_ID
    local_inference = False
    requires_api_key = True

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, transport: Any | None = None):
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
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None,
                 timeout_seconds: float) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with self._client(timeout_seconds) as client:
                if method == "GET":
                    resp = client.get(url, headers=self._headers())
                else:
                    resp = client.post(url, headers=self._headers(), json=payload or {})
        except httpx.TimeoutException as exc:
            raise ProviderTransportError(f"Request timed out after {timeout_seconds}s") from exc
        except httpx.RequestError as exc:
            raise ProviderTransportError(sanitize_message(str(exc))) from exc
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text or resp.reason_phrase,
                                  _retry_after(resp))
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
        # Anthropic 与 OpenAI 的消息形态不同：system 是顶层参数，不进messages；
        # messages 只接受 user / assistant 交替。直接透传 OpenAI 形态会被拒。
        conversation = normalize_messages(messages, prompt)
        system_chunks = [m["content"] for m in conversation if m["role"] == "system"]
        turns = [m for m in conversation if m["role"] != "system"]

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": turns,
        }
        if system_chunks:
            payload["system"] = "\n\n".join(system_chunks)
        data = self._request("POST", "/v1/messages", payload, timeout_seconds)
        if not isinstance(data, dict):
            raise ProviderMalformedResponse("Anthropic response was not an object")
        blocks = data.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise ProviderMalformedResponse("Anthropic response contained no content blocks")
        texts = [str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        if not texts:
            raise ProviderMalformedResponse("Anthropic response contained no text block")
        text = "".join(texts)

        usage = data.get("usage") or {}
        p_tokens = _as_int(usage.get("input_tokens"), approx_tokens(conversation))
        c_tokens = _as_int(usage.get("output_tokens"), len(text.split()))
        return CallResult(
            text=text,
            usage={"prompt_tokens": p_tokens, "completion_tokens": c_tokens,
                   "total_tokens": p_tokens + c_tokens},
            provider_request_id=str(data.get("id") or ""),
            provider_id=self.provider_id,
            model=model,
        )

    def probe_models(self, *, timeout_seconds: float = 5.0) -> list[str]:
        data = self._request("GET", "/v1/models", None, timeout_seconds)
        rows = data.get("data") if isinstance(data, dict) else None
        out: list[str] = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("id"):
                out.append(str(row["id"]))
        return out


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return max(1, default)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    try:
        return float(str(raw).strip()) if raw else None
    except (TypeError, ValueError):
        return None


def build(spec: ProviderEndpoint) -> AnthropicProvider:
    return AnthropicProvider(api_key=spec.api_key, base_url=spec.base_url, transport=spec.transport)
