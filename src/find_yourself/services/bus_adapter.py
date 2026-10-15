"""B6-① 适配翻译：任何协议翻译成统一信封（N9 地基）。

``BusEnvelope``（见 ``services/bus_backend.py``）是跨协议唯一内语；
本模块只做无损形状转换，不做路由、不做鉴权、不做调度：

* A2A 入站 ``message``（role + parts）→ 信封：文本压平进 content，
  ``contextId`` 与原始 role 进 ``meta``；
* 信封 → A2A 回复 ``message``（role agent + 单 text part）；
* MCP 工具调用（tool 名 + 参数）→ 信封：sender 记 ``tool:<name>``，
  ``{"tool", "args"}`` JSON 进 content，tool 名同时进 ``meta``。

无文本/无实质内容一律抛 ``ValueError``——翻译层绝不造空信封；
空信封一旦流入总线，下游会把它当成“对方已读不回”。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .bus_backend import BusEnvelope


def _text_parts(parts: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(parts, list):
        return out
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            text = part["text"].strip()
            if text:
                out.append(text)
    return out


def from_a2a_message(message: dict[str, Any], *, room: str, sender: str,
                     context_id: str = "") -> BusEnvelope:
    """A2A 入站 message → 统一信封。"""
    role = str((message or {}).get("role") or "")
    texts = _text_parts((message or {}).get("parts"))
    if not texts:
        raise ValueError("A2A message 里没有可执行的文本内容（parts 为空）")
    meta: dict[str, Any] = {"a2a_role": role}
    if context_id:
        meta["a2a_context_id"] = context_id
    return BusEnvelope(
        id=uuid4().hex, room=room, sender=sender, kind="text",
        content="\n".join(texts),
        ts=datetime.now(timezone.utc).isoformat(), meta=meta,
    )


def envelope_to_a2a_message(env: BusEnvelope) -> dict[str, Any]:
    """统一信封 → A2A 回复 message（role agent + 单 text part）。"""
    return {"role": "agent", "parts": [{"kind": "text", "text": env.content}]}


def from_mcp_tool_call(tool_name: str, args: Any, *, room: str,
                       sender: str) -> BusEnvelope:
    """MCP 工具调用 → 统一信封。"""
    name = (tool_name or "").strip()
    if not name:
        raise ValueError("MCP tool 调用必须具名（tool 名为空）")
    payload = {"tool": name, "args": args if isinstance(args, dict) else {}}
    return BusEnvelope(
        id=uuid4().hex, room=room, sender=sender or f"tool:{name}",
        kind="tool_call", content=json.dumps(payload, ensure_ascii=False),
        ts=datetime.now(timezone.utc).isoformat(), meta={"mcp_tool": name},
    )
