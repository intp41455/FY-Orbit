"""统一接入抽象层单测（A-统一接入-01 · 补齐包3）。

覆盖：四路（MCP/A2A/CLI/插件）同构 API、AccessRegistry 注册/发现/统一调用、
新增接入源仅实现适配器即接入。
"""

from __future__ import annotations

import json
import sys

import httpx
import pytest

from find_yourself.adapters.a2a import A2AClient, TrustedEndpointRegistry, UntrustedEndpointError
from find_yourself.adapters.mcp import (
    McpClient,
    McpPrompt,
    McpResource,
    McpStdioServer,
    McpTool,
    McpTrustPolicy,
)
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub.access import (
    CHANNEL_A2A,
    CHANNEL_CLI,
    CHANNEL_MCP,
    CHANNEL_PLUGIN,
    ACCESS_CHANNELS,
    AccessRegistry,
    A2AAccessChannel,
    CliChannelAdapter,
    McpAccessChannel,
    PluginChannelAdapter,
    InvokeCall,
)
from find_yourself.services.hub.adapters import HealthReport


def _mcp_client() -> McpClient:
    server = McpStdioServer(tools=[
        McpTool(name="ping", description="d", handler=lambda a: {"pong": True}),
    ])
    return McpClient.from_server(server, trust=McpTrustPolicy(level="trusted"))


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #


def test_registry_register_get_invoke_uniform_surface():
    reg = AccessRegistry()
    reg.register("plugin:echo", PluginChannelAdapter(
        {"plugin_id": "echo"},
        handlers={"run": lambda action, params: {"echo": params.get("text")}},
    ))
    adapter = reg.get("plugin:echo")
    result = adapter.invoke(InvokeCall(action="run", params={"text": "你好"}))
    assert result.ok is True
    assert result.output == {"echo": "你好"}


def test_registry_unknown_channel_raises_honestly():
    reg = AccessRegistry()
    with pytest.raises(ValidationFailed) as err:
        reg.get("mcp:missing")
    assert err.value.code == "hub_access_unknown_channel"


def test_registry_rejects_non_uniform_adapter():
    reg = AccessRegistry()

    class Incomplete:
        channel = CHANNEL_PLUGIN
        source_id = "bad"

    with pytest.raises(ValueError):
        reg.register("plugin:bad", Incomplete())  # type: ignore[arg-type]


def test_registry_replace_and_unregister_and_list():
    reg = AccessRegistry()
    first = PluginChannelAdapter({"plugin_id": "a"}, handlers={"run": lambda a, p: 1})
    reg.register("plugin:a", first)
    with pytest.raises(ValueError):
        reg.register("plugin:a", first)
    second = PluginChannelAdapter({"plugin_id": "a"}, handlers={"run": lambda a, p: 2})
    reg.register("plugin:a", second, replace=True)
    assert reg.get("plugin:a") is second
    assert reg.unregister("plugin:a") is True
    assert reg.unregister("plugin:a") is False
    with pytest.raises(ValidationFailed):
        reg.get("plugin:a")


def test_registry_health_all_and_capabilities_and_invoke_entrypoint():
    reg = AccessRegistry()
    reg.register("plugin:one", PluginChannelAdapter(
        {"plugin_id": "one"}, handlers={"run": lambda a, p: {"n": 1}}))
    reg.register("plugin:two", PluginChannelAdapter({"plugin_id": "two"}))  # 无 handler
    reports = reg.health_all()
    assert reports["plugin:one"].ok is True
    assert reports["plugin:two"].ok is False
    names = [c.name for c in reg.capabilities()]
    assert "plugin.run" in names
    result = reg.invoke("plugin:one", action="run", params={"x": 1})
    assert result.ok and result.output == {"n": 1}


def test_new_source_plugs_in_by_implementing_adapter_shape():
    """新增接入源 = 实现同构适配器 + register，无需改任何调用方。"""
    reg = AccessRegistry()

    class WebhookishChannel:
        channel = CHANNEL_PLUGIN
        source_id = "custom-webhook"

        def health(self, *, timeout_seconds: float = 3.0) -> HealthReport:
            return HealthReport(ok=True, detail="custom up")

        def capabilities(self):
            from find_yourself.services.hub.adapters import Capability

            return [Capability(name="plugin.custom", tags=("custom",))]

        def invoke(self, call):
            from find_yourself.services.hub.adapters import InvokeResult

            return InvokeResult(ok=True, output={"custom": call.params})

    reg.register("plugin:custom-webhook", WebhookishChannel())
    out = reg.invoke("plugin:custom-webhook", action="anything", params={"a": 1})
    assert out.ok and out.output == {"custom": {"a": 1}}


# --------------------------------------------------------------------------- #
# MCP 通道
# --------------------------------------------------------------------------- #


def test_mcp_access_channel_uniform_and_invoke_call_tool():
    ch = McpAccessChannel({"server": "demo"}, client=_mcp_client())
    assert ch.channel == CHANNEL_MCP and ch.source_id == "demo"
    assert {"channel", "source_id", "kind"} <= set(ch.describe())
    report = ch.health()
    assert report.ok is True
    assert "tool:ping" in {c.name for c in report.capabilities}
    result = ch.invoke(InvokeCall(action="call_tool",
                                  params={"name": "ping", "arguments": {}}))
    assert result.ok and result.output == {"pong": True}


def test_mcp_access_channel_discovery_actions():
    server = McpStdioServer(
        tools=[McpTool(name="ping", description="d", handler=lambda a: {"pong": True})],
        resources=[McpResource(uri="file:///x.md", name="x", text="hello")],
        prompts=[McpPrompt(name="hi", template="hey")],
    )
    ch = McpAccessChannel({"server": "demo"},
                          client=McpClient.from_server(server,
                                                       trust=McpTrustPolicy(level="trusted")))
    res = ch.invoke(InvokeCall(action="list_resources"))
    assert res.ok and res.output["resources"][0]["uri"] == "file:///x.md"
    prm = ch.invoke(InvokeCall(action="list_prompts"))
    assert prm.ok and prm.output["prompts"][0]["name"] == "hi"
    got = ch.invoke(InvokeCall(action="get_prompt", params={"name": "hi"}))
    assert got.ok and got.output["messages"][0]["content"]["text"] == "hey"
    bad = ch.invoke(InvokeCall(action="nonsense"))
    assert bad.ok is False and "hub_unsupported_action" in bad.error


# --------------------------------------------------------------------------- #
# A2A 通道
# --------------------------------------------------------------------------- #


def _card_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/.well-known/agent.json":
        return httpx.Response(200, json={
            "name": "research-agent", "version": "1.0", "protocolVersion": "0.3.0",
            "capabilities": {}, "skills": []})
    if request.url.path == "/a2a/v1/jsonrpc":
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"),
                                         "result": {"id": "task-1",
                                                    "status": {"state": "completed"}}})
    return httpx.Response(404)


def test_a2a_access_channel_requires_trusted_registry():
    ch = A2AAccessChannel({"endpoint_key": "research"})  # 空注册表
    report = ch.health()
    assert report.ok is False
    assert "trusted endpoint registry" in report.detail
    result = ch.invoke(InvokeCall(action="send_message", params={"text": "hi"}))
    assert result.ok is False
    assert "trusted endpoint registry" in result.error


def test_a2a_access_channel_send_message_roundtrip():
    registry = TrustedEndpointRegistry()
    registry.register("research", "https://agents.example.com")
    client = A2AClient(registry=registry,
                       http_client=httpx.Client(transport=httpx.MockTransport(_card_handler)))
    ch = A2AAccessChannel({"endpoint_key": "research"}, client=client)
    report = ch.health()
    assert report.ok is True and "research-agent" in report.detail
    result = ch.invoke(InvokeCall(action="send_message", params={"text": "研究一下"}))
    assert result.ok is True
    assert result.output["status"]["state"] == "completed"


def test_a2a_access_channel_missing_endpoint_key():
    with pytest.raises(ValidationFailed):
        A2AAccessChannel({})


# --------------------------------------------------------------------------- #
# CLI 通道（A-统一接入-04 的通道形态；超时/退出码细节见 scheduler 测试组）
# --------------------------------------------------------------------------- #


def test_cli_channel_run_ok_and_exit_code():
    ch = CliChannelAdapter({"source_id": "py-echo",
                            "command": [sys.executable, "-c",
                                        "import sys; print('cli-ok ' + sys.argv[1])", "ARG1"]})
    assert ch.channel == CHANNEL_CLI
    result = ch.invoke(InvokeCall(action="run"))
    assert result.ok is True
    assert "cli-ok ARG1" in result.output["stdout"]
    assert result.meta["exit_code"] == 0


def test_cli_channel_nonzero_exit_fails_with_stderr():
    ch = CliChannelAdapter({"command": [sys.executable, "-c",
                                        "import sys; print('boom', file=sys.stderr); sys.exit(3)"]})
    result = ch.invoke(InvokeCall(action="run"))
    assert result.ok is False
    assert result.meta["exit_code"] == 3
    assert "boom" in result.error


def test_cli_channel_rejects_bad_command():
    with pytest.raises(ValidationFailed):
        CliChannelAdapter({"command": "not-a-list"})


def test_cli_health_probes_without_executing():
    ch = CliChannelAdapter({"command": [sys.executable, "-c", "pass"]})
    report = ch.health()
    assert report.ok is True
    assert report.endpoint_ref.startswith("cli:")
