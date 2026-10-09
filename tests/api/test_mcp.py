"""A02: stdlib MCP JSON-RPC/stdio — initialize handshake, tools/list, permission gate, cancellation, errors."""

from __future__ import annotations

import json

from find_yourself.adapters.mcp import McpStdioServer, McpTool


def _call(server, method, params=None, req_id=1):
    line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                        "params": params or {}})
    return server.handle_line(line)


def test_initialize_handshake_reports_version_and_caps():
    s = McpStdioServer(tools=[])
    out = _call(s, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
    assert out["result"]["protocolVersion"] == "2024-11-05"
    assert out["result"]["serverInfo"]["name"] == "find-yourself-mcp"


def test_tools_list_returns_injected_catalogue():
    s = McpStdioServer(tools=[
        McpTool(name="memory_search", description="search memory", handler=lambda a: {"hits": []}),
        McpTool(name="core_export", description="privileged", privileged=True,
                handler=lambda a: {"data": 1}),
    ])
    out = _call(s, "tools/list")
    names = [t["name"] for t in out["result"]["tools"]]
    assert names == ["memory_search", "core_export"]


def test_privileged_tool_denied_without_injected_scope():
    s = McpStdioServer(tools=[McpTool(name="core_export", description="x", privileged=True,
                                     handler=lambda a: {"data": 1})],
                       allowed_privileged=set())
    out = _call(s, "tools/call", {"name": "core_export", "arguments": {}})
    assert out["error"]["code"] == -32003  # permission denied


def test_privileged_tool_allowed_when_scope_injected():
    s = McpStdioServer(tools=[McpTool(name="core_export", description="x", privileged=True,
                                     handler=lambda a: {"data": 1})],
                       allowed_privileged={"core_export"})
    out = _call(s, "tools/call", {"name": "core_export", "arguments": {}})
    assert "result" in out
    assert "text" in out["result"]["content"][0]


def test_normal_tool_runs_and_unknown_tool_errors():
    s = McpStdioServer(tools=[McpTool(name="echo", description="e",
                                      handler=lambda a: {"echo": a.get("msg")})])
    ok = _call(s, "tools/call", {"name": "echo", "arguments": {"msg": "hi"}})
    assert json.loads(ok["result"]["content"][0]["text"]) == {"echo": "hi"}
    miss = _call(s, "tools/call", {"name": "ghost", "arguments": {}})
    assert miss["error"]["code"] == -32004


def test_cancellation_notification_supersedes_call():
    s = McpStdioServer(tools=[McpTool(name="slow", description="s", handler=lambda a: 1)])
    # Cancel request id 9 then call with id 9.
    s.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": "9"}}))
    out = _call(s, "tools/call", {"name": "slow", "arguments": {}}, req_id="9")
    assert out["error"]["code"] == -32000  # cancelled


def test_malformed_frame_is_parse_error():
    s = McpStdioServer(tools=[])
    out = s.handle_line("{not json")
    assert out["error"]["code"] == -32700


def test_unknown_method_error():
    s = McpStdioServer(tools=[])
    out = _call(s, "frobnicate")
    assert out["error"]["code"] == -32601
