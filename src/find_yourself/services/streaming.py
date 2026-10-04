"""P1-08 REST+SSE streaming channel.

Produces the standard ``text/event-stream`` wire protocol:

* ``event: message_start`` — stream metadata (id, model, mode),
* ``: heartbeat`` — SSE comment frames keeping intermediaries from buffering,
* ``event: delta`` — incremental text chunks,
* ``event: message_end`` — terminal frame with finish reason and usage,
* ``event: error`` — mid-stream failure frame followed by ``message_end``.

Two data modes:

* ``gateway`` — a real model provider is configured; the guarded inference
  chain (domain privacy, atomic budget reservation, settlement) runs via
  :class:`~find_yourself.runtime.gateway.ModelGateway`, and the resulting text
  is re-emitted as delta frames.
* ``stub`` — no provider is configured; a deterministic, configurable stub
  generator (``FY_SSE_STUB``) emits deterministic deltas so the wire protocol
  can be verified with real curl traffic without external credentials. The
  stub is never active in production environments.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from typing import AsyncIterator

from ..runtime.gateway import ModelGateway, ModelNotConfigured
from ..services.actor import Actor

# Number of delta frames between heartbeat comment frames.
HEARTBEAT_EVERY_N_DELTAS = 5

# Deterministic pacing for the stub generator (seconds between deltas).
STUB_CHUNK_DELAY_SECONDS = 0.02


def _sse(event: str | None, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


HEARTBEAT_FRAME = ": heartbeat\n\n"


def stub_mode_allowed(settings) -> bool:
    """Resolve the configurable stub switch.

    ``FY_SSE_STUB`` accepts ``auto`` (default: stub when no provider is
    configured and environment is not production), explicit ``on``/``off``.
    """
    raw = os.environ.get("FY_SSE_STUB", "auto").strip().lower()
    if raw in {"0", "off", "false", "no"}:
        return False
    if raw in {"1", "on", "true", "yes"}:
        return True
    return getattr(settings, "environment", "local") != "production"


def stub_text(prompt: str) -> str:
    """Deterministic stub answer: the same prompt always yields the same text."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return (
        "Deterministic streaming stub. "
        f"Received prompt of {len(prompt)} characters. "
        f"Fingerprint {digest[:12]}. "
        "This stream exercises the P1-08 SSE wire protocol end to end: "
        "message_start, delta chunks, heartbeats and message_end."
    )


def split_chunks(text: str, max_chars: int = 24) -> list[str]:
    """Split text into deterministic word-boundary chunks (~max_chars each).

    Chunk concatenation reproduces the input text exactly, including the
    single spaces between words.
    """
    if not text:
        return [text]
    pieces = [word + " " for word in text.split(" ")]
    pieces[-1] = pieces[-1][:-1]  # no trailing space after the final word
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = buf + piece
        if len(candidate) > max_chars and buf:
            chunks.append(buf)
            buf = piece
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


class StreamingService:
    """Builds SSE event frames for the streaming chat endpoint."""

    def __init__(self, settings, budget=None, gateway: ModelGateway | None = None):
        self.settings = settings
        self.gateway = gateway if gateway is not None else ModelGateway(settings, budget)

    def resolve_mode(self) -> str:
        """Pick the data mode up front so misconfiguration surfaces as a
        proper ``MODEL_NOT_CONFIGURED`` error envelope (HTTP 503) instead of a
        broken stream after the 200 has been committed."""
        if self.gateway.provider is not None:
            return "gateway"
        if stub_mode_allowed(self.settings):
            return "stub"
        raise ModelNotConfigured()

    async def stream_chat(
        self,
        actor: Actor,
        *,
        prompt: str,
        model: str,
        task_id: str,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        mode = self.resolve_mode()
        stream_id = uuid.uuid4().hex
        started = time.monotonic()

        yield _sse("message_start", {"stream_id": stream_id, "model": model, "mode": mode})
        yield HEARTBEAT_FRAME

        try:
            if mode == "gateway":
                from fastapi.concurrency import run_in_threadpool

                result = await run_in_threadpool(
                    lambda: self.gateway.complete(
                        actor, task_id=task_id, model=model, prompt=prompt,
                        max_tokens=max_tokens,
                    )
                )
                text = result.text
                usage = dict(result.usage)
                provider_request_id = result.provider_request_id
            else:
                text = stub_text(prompt)
                p_tokens = max(1, len(prompt.split()))
                c_tokens = max(1, len(text.split()))
                usage = {"prompt_tokens": p_tokens, "completion_tokens": c_tokens}
                provider_request_id = f"stub-{stream_id[:12]}"

            chunks = split_chunks(text)
            for i, chunk in enumerate(chunks):
                yield _sse("delta", {"index": i, "text": chunk})
                if (i + 1) % HEARTBEAT_EVERY_N_DELTAS == 0 and i + 1 < len(chunks):
                    yield HEARTBEAT_FRAME
                if mode == "stub":
                    await asyncio.sleep(STUB_CHUNK_DELAY_SECONDS)

            usage.setdefault(
                "total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
            )
            yield _sse("message_end", {
                "stream_id": stream_id,
                "finish_reason": "stop",
                "usage": usage,
                "provider_request_id": provider_request_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # mid-stream failure -> error frame, then close
            yield _sse("error", {
                "code": getattr(exc, "code", "stream_error"),
                "message": str(getattr(exc, "message", exc)),
            })
            yield _sse("message_end", {
                "stream_id": stream_id,
                "finish_reason": "error",
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            })
