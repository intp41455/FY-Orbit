"""B6-① 适配翻译：任何协议翻译成统一信封（BusEnvelope）。

* A2A 入站 message → 信封（role/parts 压平为 content，contextId 进 meta）；
* 信封 → A2A 回复 message（role agent + text part）；
* MCP 工具调用 → 信封（tool 名进 sender，参数 JSON 进 content）；
* 无文本/无实质内容一律抛错，绝不造空信封。
"""

from __future__ import annotations

import json

import pytest

from find_yourself.services.bus_adapter import (
    envelope_to_a2a_message,
    from_a2a_message,
    from_mcp_tool_call,
)


def test_a2a_message_to_envelope() -> None:
    env = from_a2a_message(
        {"role": "user", "parts": [{"kind": "text", "text": "帮我查天气"}]},
        room="room1", sender="agent:ext", context_id="ctx-9",
    )
    assert env.room == "room1"
    assert env.sender == "agent:ext"
    assert env.kind == "text"
    assert env.content == "帮我查天气"
    assert env.meta["a2a_context_id"] == "ctx-9"
    assert env.meta["a2a_role"] == "user"


def test_a2a_message_without_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="文本内容"):
        from_a2a_message({"role": "user", "parts": []}, room="r", sender="s")


def test_envelope_to_a2a_message() -> None:
    from find_yourself.services.bus_backend import BusEnvelope

    env = BusEnvelope(id="1", room="r", sender="agent:local", kind="text",
                      content="结果来了", ts="2026-10-15T00:00:00+00:00")
    msg = envelope_to_a2a_message(env)
    assert msg["role"] == "agent"
    assert msg["parts"] == [{"kind": "text", "text": "结果来了"}]


def test_mcp_tool_call_to_envelope() -> None:
    env = from_mcp_tool_call(
        "weather.query", {"city": "杭州"}, room="room2", sender="tool:weather",
    )
    assert env.sender == "tool:weather"
    assert env.kind == "tool_call"
    assert json.loads(env.content) == {"tool": "weather.query", "args": {"city": "杭州"}}
    assert env.meta["mcp_tool"] == "weather.query"


def test_mcp_tool_call_requires_name() -> None:
    with pytest.raises(ValueError, match="tool"):
        from_mcp_tool_call("", {"a": 1}, room="r", sender="s")
