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
    ProviderTruncatedResponse,
    classify_status,
    normalize_messages,
    sanitize_message,
)
from .base import (
    approx_tokens as _approx_tokens,
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
            message = choices[0]["message"]
            text = message.get("content") or ""
            # 推理模型把预算花在 reasoning 上，content 可能是空串/None。
            # 早期只读 content，于是 max_tokens 被思考吃光时**静默返回空文本**，
            # 调用方拿到 ok=True + text="" —— 上层完全看不出这是一次失败。
            # 实测：deepseek-v4.1-flash + 2000 max_tokens + 长任务
            # → finish_reason=length, reasoning_tokens=2000, content 长度 0。
            reasoning = message.get("reasoning_content") or ""
            finish_reason = str(choices[0].get("finish_reason") or "")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            # Upstream answered 2xx; it may already have billed us. Do not retry.
            raise ProviderMalformedResponse("Response contained no choices[0].message.content") from exc

        # 两段都没有可读文本 = 这次调用没产出任何东西。必须报错而不是
        # 返回空串：空串会被当成「模型这么答的」拼进多轮上下文，污染后续每一步。
        if not text.strip() and not reasoning.strip():
            raise ProviderTruncatedResponse(
                f"上游返回 2xx 但没有任何文本内容"
                f"（finish_reason={finish_reason or 'unknown'}，"
                f"prompt_tokens={_usage_int(data, 'prompt_tokens')}，"
                f"completion_tokens={_usage_int(data, 'completion_tokens')}）"
            )
        truncated_note = ""
        if not text.strip() and reasoning.strip():
            # content 空但有 reasoning —— 必须**如实说明原因**，不能一律
            # 说成「被 max_tokens 截断」。实测至少有两种成因：
            #   finish_reason=length     → 思考把预算吃光，确实是截断
            #   finish_reason=tool_calls → 模型想调工具，content 自然为空
            # 两者给调用方的处置完全不同（调大预算 vs 给工具/换 prompt），
            # 混为一谈会把人引到错误的排查方向上。
            text = reasoning
            if finish_reason == "length":
                why = (f"思考过程用光了 max_tokens（completion_tokens="
                       f"{_usage_int(data, 'completion_tokens')}）")
            elif finish_reason == "tool_calls":
                why = "上游返回了工具调用而没有正文（本次调用未提供工具）"
            else:
                why = f"上游未产出正文（finish_reason={finish_reason or 'unknown'}）"
            truncated_note = f"正文缺失，已降级返回 reasoning_content：{why}"

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
            # 截断必须**可见**：上层要能区分「模型就这么答的」和
            # 「预算不够、只拿到思考过程」。放进 degraded_reason 而不是
            # usage —— 后者声明是 dict[str, int]，塞标记会破坏契约。
            degraded_reason=truncated_note,
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


def _usage_int(data: dict[str, Any], key: str) -> int:
    """从 ``usage`` 里安全取整数——只用于错误信息，不参与计费判断。"""
    usage = data.get("usage") or {}
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


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
