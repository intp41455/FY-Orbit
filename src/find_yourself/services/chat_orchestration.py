"""Chat 调试预览编排服务 (P1-19): 模板 + 工具 + SSE 联动.

Extends the P1-08 wire protocol with tool-calling orchestration. The
additional frames are purely additive — a plain (no tools) stream keeps the
exact P1-08 frame sequence:

* ``event: message_start`` — metadata (now may carry ``template`` + ``tools``),
* ``event: delta``         — incremental text chunks,
* ``event: tool_call``     — a tool-call intent detected in the reply,
* ``event: tool_result``   — the REAL execution receipt from the tool registry,
* ``event: message_end``   — terminal frame; carries ``tools_used`` trace.

Tool-call convention: the model expresses an intent by emitting the structured
marker ``{"tool_call": {"name": ..., "arguments": {...}}}`` anywhere in its
reply. The orchestrator scans the accumulated reply text, really invokes the
tool through the P1-05 registry (schema-validated, receipt logged), feeds the
result back as continuation context and streams the follow-up segment. At most
``MAX_TOOL_ROUNDS`` rounds per turn.

Stub mode convention (``FY_SSE_STUB`` deterministic generator): when tools are
bound and the user message mentions a bound tool name together with the
trigger keyword ``调用`` (or ``/call``), the stub emits a tool-call marker for
that tool. Arguments are derived deterministically from the user message:
numbers in order of appearance fill numeric required properties; a quoted
segment (or the message tail) fills the first string property; remaining
required properties fall back to their schema defaults.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from ..runtime.gateway import ModelGateway
from ..services.streaming import (
    HEARTBEAT_EVERY_N_DELTAS,
    HEARTBEAT_FRAME,
    STUB_CHUNK_DELAY_SECONDS,
    StreamingService,
    _sse,
    split_chunks,
    stub_text,
)
from .tool_registry import ToolRegistryService, tool_registry

MAX_TOOL_ROUNDS = 3

_TOOL_CALL_KEY = '{"tool_call"'
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_QUOTED_RE = re.compile(r"[\"'“”『』【】\[](.*?)[\"'“”『』【】\]]")

TOOL_PROTOCOL_INSTRUCTION = (
    '工具调用约定：如需调用工具，请在回复原文中输出结构化标记 '
    '{"tool_call": {"name": "<工具名>", "arguments": {...}}}，'
    "系统会真实执行并把结果回填到本轮上下文。"
)


def build_tool_manifest(tools: list[dict[str, Any]]) -> str:
    """Render the bound-tool manifest injected into the system section."""
    if not tools:
        return ""
    lines = ["[可用工具]"]
    for t in tools:
        params = json.dumps(t.get("parameters") or {}, ensure_ascii=False)
        lines.append(f"- {t['name']}: {t.get('description', '')} | 参数 schema: {params}")
    lines.append(TOOL_PROTOCOL_INSTRUCTION)
    return "\n".join(lines)


def compose_prompt(*, system_prefix: str, tools: list[dict[str, Any]],
                   user_message: str) -> str:
    """Assemble model prompt = 模板渲染结果 + 工具清单 + 用户消息."""
    parts: list[str] = []
    if system_prefix:
        parts.append(f"[系统指令]\n{system_prefix}")
    manifest = build_tool_manifest(tools)
    if manifest:
        parts.append(manifest)
    parts.append(f"[用户消息]\n{user_message}")
    return "\n\n".join(parts)


def detect_tool_call(text: str) -> dict[str, Any] | None:
    """Find and parse the first ``{"tool_call": ...}`` marker in ``text``.

    Uses ``JSONDecoder.raw_decode`` from the marker start so nested argument
    objects are parsed correctly. Returns ``{"name": str, "arguments": dict}``
    or ``None`` when no well-formed marker exists.
    """
    idx = text.find(_TOOL_CALL_KEY)
    if idx < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[idx:])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    call = obj.get("tool_call")
    if not isinstance(call, dict):
        return None
    name = call.get("name")
    arguments = call.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return None
    return {"name": name, "arguments": arguments}


def _numbers_in(message: str) -> list[float]:
    return [float(n) for n in _NUM_RE.findall(message)]


def stub_tool_arguments(tool: dict[str, Any], user_message: str) -> dict[str, Any]:
    """Deterministic stub arguments derived from the user message (stub only)."""
    schema = tool.get("parameters") or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or list(props)
    numbers = _numbers_in(user_message)
    quoted = _QUOTED_RE.findall(user_message)
    args: dict[str, Any] = {}
    for key in required:
        spec = props.get(key) or {}
        vtype = spec.get("type", "string")
        if vtype in ("number", "integer") and numbers:
            value = numbers.pop(0)
            args[key] = int(value) if vtype == "integer" else value
        elif vtype == "boolean":
            args[key] = True
        elif vtype == "string":
            args[key] = quoted.pop(0) if quoted else user_message[-32:]
        else:
            args[key] = spec.get("default")
    return args


def stub_reply(*, composed_prompt: str, user_message: str,
               tools: list[dict[str, Any]]) -> str:
    """Deterministic stub reply; emits a tool-call marker when triggered."""
    base = stub_text(composed_prompt)
    if not tools:
        return base
    lowered = user_message.lower()
    triggered = ("调用" in user_message) or ("/call" in lowered)
    if not triggered:
        return base
    for tool in tools:
        if tool["name"].lower() in lowered:
            args = stub_tool_arguments(tool, user_message)
            marker = json.dumps(
                {"tool_call": {"name": tool["name"], "arguments": args}},
                ensure_ascii=False,
            )
            return f"{base}\n{marker}"
    return base


def stub_continuation(tool_name: str, receipt: dict[str, Any]) -> str:
    """Deterministic post-tool follow-up segment (stub only)."""
    result_json = json.dumps(receipt.get("result"), ensure_ascii=False)
    return (
        f"工具 {tool_name} 已真实执行（call_id {receipt.get('call_id')}），"
        f"结果：{result_json}。以上工具结果已纳入本轮上下文。"
    )


class ChatOrchestrationService:
    """SSE orchestration for Chat 调试预览: template + tools + streaming."""

    def __init__(self, settings, budget=None, gateway: ModelGateway | None = None,
                 registry: ToolRegistryService | None = None, bus_svc=None):
        self.settings = settings
        self.streaming = StreamingService(settings, budget=budget, gateway=gateway)
        self.registry = registry if registry is not None else tool_registry
        self.bus_svc = bus_svc

    @property
    def gateway(self) -> ModelGateway:
        return self.streaming.gateway

    def resolve_mode(self) -> str:
        return self.streaming.resolve_mode()

    def bind_tools(self, names: list[str]) -> list[dict[str, Any]]:
        """Resolve bound tool names to registry metadata (404 when unknown)."""
        return [self.registry.get_tool(n) for n in names]

    async def _gateway_text(self, actor, *, prompt: str, model: str, task_id: str,
                            max_tokens: int) -> tuple[str, dict]:
        from fastapi.concurrency import run_in_threadpool

        result = await run_in_threadpool(
            lambda: self.gateway.complete(
                actor, task_id=task_id, model=model, prompt=prompt,
                max_tokens=max_tokens,
            )
        )
        return result.text, dict(result.usage)

    async def stream_chat(
        self,
        actor,
        *,
        prompt: str,
        user_message: str,
        model: str,
        task_id: str,
        tools: list[dict[str, Any]],
        template_meta: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Yield SSE frames with tool orchestration (see module docstring)."""
        mode = self.resolve_mode()
        stream_id = uuid.uuid4().hex
        started = time.monotonic()
        tools_used: list[dict[str, Any]] = []

        start_payload: dict[str, Any] = {"stream_id": stream_id, "model": model, "mode": mode}
        if tools:
            start_payload["tools"] = [t["name"] for t in tools]
        if template_meta:
            start_payload["template"] = template_meta
        yield _sse("message_start", start_payload)
        yield HEARTBEAT_FRAME

        try:
            usage: dict[str, Any] = {}
            provider_request_id = ""
            # 模型提示词组装 = 模板渲染结果(system_prefix) + 工具清单 + 用户消息。
            model_prompt = compose_prompt(
                system_prefix=prompt, tools=tools, user_message=user_message,
            )
            if mode == "gateway":
                text, usage = await self._gateway_text(
                    actor, prompt=model_prompt, model=model, task_id=task_id,
                    max_tokens=max_tokens,
                )
                provider_request_id = "gateway"
            else:
                text = stub_reply(
                    composed_prompt=model_prompt, user_message=user_message, tools=tools,
                )
                p_tokens = max(1, len(prompt.split()))
                c_tokens = max(1, len(text.split()))
                usage = {"prompt_tokens": p_tokens, "completion_tokens": c_tokens}
                provider_request_id = f"stub-{stream_id[:12]}"

            # Round 1: stream the reply, then orchestrate up to
            # MAX_TOOL_ROUNDS tool rounds (marker -> real invoke -> continue).
            round_no = 0
            delta_index = 0
            while True:
                chunks = split_chunks(text)
                for i, chunk in enumerate(chunks):
                    yield _sse("delta", {"index": delta_index, "text": chunk})
                    delta_index += 1
                    if (delta_index % HEARTBEAT_EVERY_N_DELTAS == 0
                            and i + 1 < len(chunks)):
                        yield HEARTBEAT_FRAME
                    if mode == "stub":
                        await asyncio.sleep(STUB_CHUNK_DELAY_SECONDS)

                call = detect_tool_call(text) if tools else None
                if call is None or round_no >= MAX_TOOL_ROUNDS:
                    break
                round_no += 1

                # REAL tool execution through the P1-05 registry.
                receipt = self.registry.invoke(call["name"], call["arguments"])
                tools_used.append({
                    "index": round_no - 1,
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "call_id": receipt.get("call_id"),
                    "result": receipt.get("result"),
                    "executed": receipt.get("executed", True),
                    "executed_at": receipt.get("executed_at"),
                })
                yield _sse("tool_call", {
                    "index": round_no - 1,
                    "name": call["name"],
                    "arguments": call["arguments"],
                })
                yield _sse("tool_result", {
                    "index": round_no - 1,
                    "name": call["name"],
                    "call_id": receipt.get("call_id"),
                    "result": receipt.get("result"),
                    "executed": receipt.get("executed", True),
                    "executed_at": receipt.get("executed_at"),
                })

                # Continuation: tool result as follow-up context.
                if mode == "gateway":
                    follow_prompt = (
                        f"{model_prompt}\n\n[工具 {call['name']} 结果]\n"
                        f"{json.dumps(receipt.get('result'), ensure_ascii=False)}\n"
                        "请基于以上工具结果继续回答。"
                    )
                    text, u = await self._gateway_text(
                        actor, prompt=follow_prompt, model=model, task_id=task_id,
                        max_tokens=max_tokens,
                    )
                    usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + u.get("prompt_tokens", 0)
                    usage["completion_tokens"] = usage.get("completion_tokens", 0) + u.get("completion_tokens", 0)
                else:
                    text = stub_continuation(call["name"], receipt)

            usage.setdefault(
                "total_tokens",
                usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
            )
            end_payload: dict[str, Any] = {
                "stream_id": stream_id,
                "finish_reason": "stop",
                "usage": usage,
                "provider_request_id": provider_request_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
            if tools:
                end_payload["tools_used"] = tools_used
            yield _sse("message_end", end_payload)
            # Broken Chain #2 修复：向用户的 DM 房间推送 Agent 响应，实现 Controller → 用户窗口的实时通知。
            if self.bus_svc is not None and text.strip():
                owner_id = getattr(actor, "owner_id", None)
                if owner_id:
                    room = f"dm:owner:{owner_id}:agent:coordinator"
                    self.bus_svc.publish_as_role(
                        room=room,
                        role="coordinator",
                        kind="text",
                        content=text[:8000],
                    )
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
