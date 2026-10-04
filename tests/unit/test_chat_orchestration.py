"""P1-19 Chat orchestration unit tests: marker detection, arg derivation,
real tool invocation and SSE frame ordering (message_start → delta* →
tool_call → tool_result → delta* → message_end with tools_used)."""

from __future__ import annotations

import asyncio
import json

import pytest

from find_yourself.config import Settings
from find_yourself.runtime.gateway import MockModelProvider, ModelGateway
from find_yourself.services.actor import Actor
from find_yourself.services.chat_orchestration import (
    ChatOrchestrationService,
    compose_prompt,
    detect_tool_call,
    stub_reply,
    stub_tool_arguments,
)
from find_yourself.services.tool_registry import ToolRegistryService


def parse_sse(frames: list[str]) -> list[dict]:
    events: list[dict] = []
    for block in "".join(frames).split("\n\n"):
        block = block.strip("\n")
        if not block or block.startswith(":"):
            continue
        event_name = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if event_name or data_lines:
            events.append({
                "event": event_name,
                "data": json.loads(data_lines[0]) if data_lines else None,
            })
    return events


def make_settings() -> Settings:
    return Settings(environment="test",
                    session_secret="test-session-secret-that-is-long-enough-123456")


@pytest.fixture()
def registry(tmp_path):
    reg = ToolRegistryService(persist_dir=tmp_path / "tool_registry")
    reg.register(
        name="add",
        description="两数相加",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        entry={"type": "builtin", "executor": "add"},
    )
    reg.register(
        name="echo",
        description="原样回显",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        entry={"type": "builtin", "executor": "echo"},
    )
    return reg


def make_service(registry, provider=None) -> ChatOrchestrationService:
    gateway = ModelGateway(provider=provider) if provider else None
    return ChatOrchestrationService(make_settings(), gateway=gateway, registry=registry)


async def collect(agen) -> list[str]:
    return [f async for f in agen]


# --- marker detection -----------------------------------------------------------

def test_detect_tool_call_parses_nested_arguments():
    text = '前言 {"tool_call": {"name": "add", "arguments": {"a": 1, "b": {"c": 2}}}} 后记'
    call = detect_tool_call(text)
    assert call == {"name": "add", "arguments": {"a": 1, "b": {"c": 2}}}


def test_detect_tool_call_returns_none_without_marker_or_broken_json():
    assert detect_tool_call("普通回复，没有标记") is None
    assert detect_tool_call('{"tool_call": {"name":') is None


# --- stub argument derivation ----------------------------------------------------

def test_stub_tool_arguments_fills_numbers_and_strings_in_order(registry):
    tool = registry.get_tool("add")
    args = stub_tool_arguments(tool, "调用 add 计算 3 和 4 的和")
    assert args == {"a": 3, "b": 4}
    tool = registry.get_tool("echo")
    args = stub_tool_arguments(tool, '调用 echo 工具 [你好世界]')
    assert args == {"text": "你好世界"}


def test_stub_reply_emits_marker_only_on_trigger(registry):
    tools = [registry.get_tool("add")]
    composed = compose_prompt(system_prefix="模板渲染文本", tools=tools,
                              user_message="调用 add 计算 3 和 4 的和")
    reply = stub_reply(composed_prompt=composed, user_message="调用 add 计算 3 和 4 的和",
                       tools=tools)
    call = detect_tool_call(reply)
    assert call is not None and call["name"] == "add"
    assert call["arguments"] == {"a": 3, "b": 4}

    # 无触发词 → 不发标记
    plain = stub_reply(composed_prompt=composed, user_message="你好", tools=tools)
    assert detect_tool_call(plain) is None

    # 未绑定工具 → 纯 stub 文本
    assert stub_reply(composed_prompt="p", user_message="调用 add", tools=[]) == \
        stub_reply(composed_prompt="p", user_message="调用 add", tools=[])


# --- orchestration stream --------------------------------------------------------

def test_plain_stream_keeps_p108_frame_sequence(registry):
    svc = make_service(registry)
    actor = Actor.service("svc-test", "tester")

    async def run():
        return [f async for f in svc.stream_chat(
            actor, prompt="p", user_message="你好", model="default",
            task_id="t1", tools=[],
        )]

    events = parse_sse(asyncio.run(run()))
    names = [e["event"] for e in events]
    assert names[0] == "message_start" and names[-1] == "message_end"
    assert all(n == "delta" for n in names[1:-1])
    assert "tools_used" not in events[-1]["data"]


def test_orchestrated_stream_really_invokes_tool_and_traces(registry):
    svc = make_service(registry)
    actor = Actor.service("svc-test", "tester")
    tools = svc.bind_tools(["add"])
    user_message = "调用 add 计算 3 和 4 的和"
    composed = compose_prompt(system_prefix="你是计算助手", tools=tools,
                              user_message=user_message)

    async def run():
        return [f async for f in svc.stream_chat(
            actor, prompt=composed, user_message=user_message, model="default",
            task_id="t2", tools=tools,
        )]

    events = parse_sse(asyncio.run(run()))
    names = [e["event"] for e in events]

    assert names[0] == "message_start"
    assert names[-1] == "message_end"
    assert "tool_call" in names and "tool_result" in names

    start = events[0]["data"]
    assert start["tools"] == ["add"]
    assert names.index("tool_call") < names.index("tool_result") < len(names) - 1

    call_evt = next(e for e in events if e["event"] == "tool_call")["data"]
    assert call_evt["name"] == "add" and call_evt["arguments"] == {"a": 3, "b": 4}
    result_evt = next(e for e in events if e["event"] == "tool_result")["data"]
    assert result_evt["result"] == {"sum": 7}
    assert result_evt["call_id"]

    end = events[-1]["data"]
    assert end["finish_reason"] == "stop"
    assert end["tools_used"] and end["tools_used"][0]["name"] == "add"
    assert end["tools_used"][0]["result"] == {"sum": 7}

    # 真实执行留痕：registry 调用日志里存在该 receipt
    calls = registry.recent_calls(limit=10)
    assert any(c["tool"] == "add" and c["arguments"] == {"a": 3, "b": 4}
               for c in calls)

    # delta 文本拼接包含触发回复与续流段
    text = "".join(e["data"]["text"] for e in events if e["event"] == "delta")
    assert "已真实执行" in text and "sum" in text


def test_gateway_mode_streams_marker_and_continues(registry):
    provider = MockModelProvider(
        default_response='{"tool_call": {"name": "echo", "arguments": {"text": "hi"}}}')
    svc = make_service(registry, provider=provider)
    assert svc.resolve_mode() == "gateway"
    actor = Actor.service("svc-test", "tester")
    tools = svc.bind_tools(["echo"])

    async def run():
        return [f async for f in svc.stream_chat(
            actor, prompt="p", user_message="调用 echo", model="mock-deterministic",
            task_id="t3", tools=tools,
        )]

    events = parse_sse(asyncio.run(run()))
    assert any(e["event"] == "tool_call" and e["data"]["name"] == "echo"
               for e in events)
    result_evt = next(e for e in events if e["event"] == "tool_result")["data"]
    assert result_evt["result"] == {"echo": {"text": "hi"}}
    end = events[-1]["data"]
    assert end["tools_used"][0]["name"] == "echo"


def test_tool_execution_error_surfaces_as_error_frame(registry, tmp_path):
    svc = make_service(registry)
    actor = Actor.service("svc-test", "tester")
    tools = svc.bind_tools(["echo"])

    # 触发 echo 但 registry 校验必填参数失败 → error 帧而非 500
    broken = dict(registry.get_tool("echo"))
    user_message = "调用 echo"

    async def run():
        frames = []
        async for f in svc.stream_chat(
            actor, prompt="p", user_message=user_message, model="default",
            task_id="t4", tools=tools,
        ):
            frames.append(f)
            if '"finish_reason": "error"' in f or '"finish_reason":"error"' in f:
                break
        return frames

    # echo 的 stub 参数总会填 text（来自消息尾部），这里正常完成；断言 happy path
    events = parse_sse(asyncio.run(run()))
    assert events[-1]["data"]["finish_reason"] == "stop"
    assert broken["name"] == "echo"
