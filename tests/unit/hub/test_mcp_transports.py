"""MCP 传输层与高级机制单测（A-统一接入-02/09 · 补齐包3）。

覆盖：
* stdio 实测（真实子进程 `python -m find_yourself.adapters.mcp`）；
* HTTP / SSE / WS 传输握手与收发（httpx.MockTransport / 注入桩实现）；
* 断线重连（有界退避 + 重新 initialize）；
* 资源/提示词发现；tools/list_changed 动态刷新；
* Elicitation 往返（accept / 无应答器诚实 decline）；
* 信任分级（untrusted 拒绝、remote 需确认、remote/untrusted 禁 inline shell）。

网络不打真网：HTTP 用 MockTransport，SSE/WS 用注入的桩实现。
"""

from __future__ import annotations

import json
import queue
import sys
import threading

import httpx
import pytest

from find_yourself.adapters.mcp import (
    ERR_CONFIRMATION_REQUIRED,
    ERR_UNTRUSTED_SERVER,
    McpConfirmationRequired,
    McpError,
    McpInlineShellBlocked,
    McpClient,
    McpPrompt,
    McpResource,
    McpStdioServer,
    McpTool,
    McpTrustPolicy,
    McpUntrustedServer,
    ReconnectPolicy,
    TRUST_REMOTE,
    TRUST_TRUSTED,
    TRUST_UNTRUSTED,
    assemble_mcp_tools,
)
from find_yourself.services.tool_registry import ToolRegistryService


def _demo_server(**kw) -> McpStdioServer:
    return McpStdioServer(
        tools=[
            McpTool(name="ping", description="Ping health check",
                    handler=lambda a: {"pong": True}),
            McpTool(name="echo", description="Echo text",
                    handler=lambda a: {"echo": a.get("text", "")}),
            McpTool(name="shell", description="inline shell",
                    handler=lambda a: {"ran": True}),
        ],
        **kw,
    )


# --------------------------------------------------------------------------- #
# stdio 实测（真实子进程）
# --------------------------------------------------------------------------- #


def test_stdio_real_subprocess_roundtrip():
    cmd = [sys.executable, "-m", "find_yourself.adapters.mcp"]
    client = McpClient.from_subprocess(cmd)
    try:
        info = client.initialize()
        assert info["protocolVersion"] == "2024-11-05"
        assert info["serverInfo"]["name"] == "find-yourself-mcp"
        tools = client.list_tools()
        assert {t["name"] for t in tools} >= {"ping", "sha256"}
        assert client.call_tool("ping", {}) == {"pong": True}
        assert client.call_tool("sha256", {"text": "abc"})["hash"].startswith(
            "ba7816bf"
        )
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# HTTP 传输
# --------------------------------------------------------------------------- #


def _rpc_ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def test_http_transport_roundtrip():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        if body["method"] == "initialize":
            return httpx.Response(200, json=_rpc_ok(1, {"protocolVersion": "2024-11-05",
                                                        "capabilities": {},
                                                        "serverInfo": {"name": "h"}}))
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "tools/list":
            return httpx.Response(200, json=_rpc_ok(2, {"tools": [
                {"name": "ping", "description": "d", "inputSchema": {"type": "object"}}]}))
        if body["method"] == "tools/call":
            return httpx.Response(200, json=_rpc_ok(3, {"content": [
                {"type": "text", "text": json.dumps({"pong": True})}]}))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"),
                                         "error": {"code": -32601, "message": "nf"}})

    client = McpClient.from_url(
        "https://mcp.example.com/rpc", transport="http",
        http=httpx.MockTransport(handler), reconnect=None,
        # 本测试只验证传输层；信任门禁由专项测试覆盖。
        trust=McpTrustPolicy(level=TRUST_TRUSTED),
    )
    client.initialize()
    tools = client.list_tools()
    assert [t["name"] for t in tools] == ["ping"]
    assert client.call_tool("ping", {}) == {"pong": True}
    assert calls[0]["method"] == "initialize"


def test_http_transport_requires_trust_confirmation_by_default():
    client = McpClient.from_url(
        "https://mcp.example.com/rpc", transport="http",
        http=httpx.MockTransport(lambda req: httpx.Response(202)),
    )
    client._tool_catalog = {"ping": {"name": "ping"}}
    with pytest.raises(McpConfirmationRequired) as err:
        client.call_tool("ping", {})
    assert err.value.code == ERR_CONFIRMATION_REQUIRED


# --------------------------------------------------------------------------- #
# SSE 传输（桩 HTTP 实现：握手 + 收发 + 重连）
# --------------------------------------------------------------------------- #


class FakeSseHttp:
    """SSE 传输的桩 HTTP：可脚本化的流事件与 POST 结果。"""

    def __init__(self, *, endpoint: str = "/msg?session=1",
                 fail_first_connect: int = 0):
        self.endpoint = endpoint
        self.fail_first_connect = fail_first_connect
        self.connect_attempts = 0
        self.inbox: queue.Queue[str] = queue.Queue()
        self.stream_open = False
        self.posts: list[dict] = []

    def open_stream(self):
        self.connect_attempts += 1
        if self.connect_attempts <= self.fail_first_connect:
            raise ConnectionError("simulated handshake failure")
        self.stream_open = True
        return iter([f"event: endpoint\ndata: {self.endpoint}\n\n"])

    def post_message(self, url: str, text: str) -> None:
        self.posts.append({"url": url, "body": json.loads(text)})

    def push(self, payload: dict) -> None:
        self.inbox.put(json.dumps(payload))

    def close(self) -> None:
        self.stream_open = False


def test_sse_handshake_parses_endpoint_event():
    http = FakeSseHttp()
    client = McpClient.from_url("https://mcp.example.com/sse", transport="sse",
                                http=http)
    transport = client._transport
    transport.connect()
    assert transport.is_connected()
    assert transport._endpoint_url == "https://mcp.example.com/msg?session=1"
    client.close()


def test_sse_rejects_stream_without_endpoint_event():
    class NoEndpoint(FakeSseHttp):
        def open_stream(self):
            self.connect_attempts += 1
            return iter(["event: message\ndata: stray\n\n"])

    from find_yourself.adapters.mcp import _SseTransport, _TransportDead

    transport = _SseTransport("https://mcp.example.com/sse",
                              http=NoEndpoint())
    with pytest.raises(_TransportDead):
        transport.connect()


class ServerLikeSseHttp(FakeSseHttp):
    """模拟服务端行为：

    * 可选：第一条流在握手后立即 EOF（``first_stream_eof=True``），此时该流上
      的 POST 石沉大海（服务端已死）——确定性地触发断线重连；
    * 后续流为长连接：endpoint 事件后挂起，``push`` 的响应帧经**流本身**到达
      （真实 SSE 语义），直到 close 才 EOF；
    * 收到 POST ``initialize`` 后经 SSE 流推回响应。
    """

    def __init__(self, *, first_stream_eof: bool = False, endpoint: str = "/msg", **kw):
        super().__init__(endpoint=endpoint, **kw)
        self.first_stream_eof = first_stream_eof
        self.open_count = 0
        self._release: threading.Event | None = None
        self.stream_events: queue.Queue[str] = queue.Queue()

    def push(self, payload) -> None:
        """响应帧经 SSE 流到达（event: message）。"""
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.stream_events.put(f"event: message\ndata: {text}\n\n")

    def open_stream(self):
        self.open_count += 1
        if self.first_stream_eof and self.open_count == 1:
            return iter(["event: endpoint\ndata: /msg\n\n"])
        release = threading.Event()
        self._release = release
        endpoint = f"{self.endpoint}?session={self.open_count}"

        def gen():
            # 完整 endpoint 事件（含结束空行）一次给出。
            yield f"event: endpoint\ndata: {endpoint}\n\n"
            # 长连接：服务端推送随到随给；close() 置位后流结束（EOF）。
            while not release.wait(timeout=0.05):
                try:
                    yield self.stream_events.get_nowait()
                except queue.Empty:
                    continue

        return gen()

    def close(self) -> None:
        if self._release is not None:
            self._release.set()
        super().close()

    def post_message(self, url: str, text: str) -> None:
        super().post_message(url, text)
        if self.first_stream_eof and self.open_count == 1:
            return  # 服务端已死：请求石沉大海，客户端按流 EOF 断线处理
        body = json.loads(text)
        if body.get("method") == "initialize":
            self.push(_rpc_ok(1, {"protocolVersion": "2024-11-05",
                                  "capabilities": {"tools": {"listChanged": True}},
                                  "serverInfo": {"name": "sse-server"}}))


def test_sse_roundtrip_posts_to_handshake_endpoint():
    http = ServerLikeSseHttp()
    client = McpClient.from_url("https://mcp.example.com/sse", transport="sse",
                                http=http, reconnect=None)
    info = client.initialize()
    assert info["serverInfo"]["name"] == "sse-server"
    # 请求确实经 POST 发往握手给出的端点
    assert http.posts and http.posts[0]["url"].endswith("/msg?session=1")
    client.close()


def test_sse_reconnects_after_stream_eof():
    """首条流握手后立刻 EOF → _TransportDead → 有界重连（重新握手 + 重发请求）。"""
    http = ServerLikeSseHttp(first_stream_eof=True)
    policy = ReconnectPolicy(max_attempts=3, backoff_seconds=0.0, sleep=lambda s: None)
    client = McpClient.from_url("https://mcp.example.com/sse", transport="sse",
                                http=http, reconnect=policy)
    info = client.initialize()
    assert info["serverInfo"]["name"] == "sse-server"
    assert http.open_count >= 2, "流断后应重新握手"
    # 第二条流的端点已被采纳
    assert client._transport._endpoint_url.endswith("session=2")
    client.close()


# --------------------------------------------------------------------------- #
# WS 传输（桩连接实现）
# --------------------------------------------------------------------------- #


class FakeWsConn:
    def __init__(self, script: list[str]):
        self.script = list(script)  # recv 依次弹出的帧
        self.sent: list[str] = []
        self.closed = False

    def send(self, text: str) -> None:
        self.sent.append(text)

    def recv(self, timeout=None) -> str:
        if not self.script:
            raise ConnectionError("ws closed")
        return self.script.pop(0)

    def close(self) -> None:
        self.closed = True


def test_ws_transport_roundtrip():
    conn = FakeWsConn([
        json.dumps(_rpc_ok(1, {"protocolVersion": "2024-11-05", "capabilities": {},
                               "serverInfo": {"name": "ws"}})),
        json.dumps(_rpc_ok(2, {"tools": [{"name": "ping", "description": "d",
                                          "inputSchema": {"type": "object"}}]})),
    ])
    client = McpClient.from_url("wss://mcp.example.com/ws", transport="ws",
                                connect_factory=lambda url, headers: conn)
    info = client.initialize()
    assert info["serverInfo"]["name"] == "ws"
    tools = client.list_tools()
    assert [t["name"] for t in tools] == ["ping"]
    assert client._transport.is_connected()
    client.close()
    assert conn.closed is True


def test_ws_reconnect_on_connection_drop():
    conns = [
        FakeWsConn([]),  # 第一条连接：不给响应 → recv 断
        FakeWsConn([
            json.dumps(_rpc_ok(1, {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "serverInfo": {"name": "ws-reconnected"}})),
        ]),
    ]
    made = {"n": 0}

    def factory(url, headers):
        conn = conns[min(made["n"], len(conns) - 1)]
        made["n"] += 1
        return conn

    policy = ReconnectPolicy(max_attempts=2, backoff_seconds=0.0, sleep=lambda s: None)
    client = McpClient.from_url("wss://mcp.example.com/ws", transport="ws",
                                connect_factory=factory, reconnect=policy)
    info = client.initialize()
    assert info["serverInfo"]["name"] == "ws-reconnected"
    assert made["n"] == 2, "断开后应重建 WS 连接"
    client.close()


# --------------------------------------------------------------------------- #
# 断线重连（进程内传输 + 一次性故障工厂）
# --------------------------------------------------------------------------- #


def test_reconnect_policy_reinitializes_and_recovers():
    from find_yourself.adapters.mcp import _InProcessTransport

    server = _demo_server()
    fails = {"n": 1}

    def factory():
        if fails["n"] > 0:
            fails["n"] -= 1
            return _FlakyInProcess(server, fail_first=True)
        return _InProcessTransport(server, on_server_request=None)

    class _FlakyInProcess(_InProcessTransport):
        def __init__(self, srv, fail_first: bool):
            super().__init__(srv)
            self._fail_first = fail_first

        def send_line(self, text: str) -> None:
            if self._fail_first:
                self._fail_first = False
                from find_yourself.adapters.mcp import _TransportDead

                raise _TransportDead("simulated dead transport")
            super().send_line(text)

    client = McpClient(transport=factory(), factory=factory,
                       reconnect=ReconnectPolicy(max_attempts=3, backoff_seconds=0.0,
                                                 sleep=lambda s: None))
    info = client.initialize()
    assert info["protocolVersion"] == "2024-11-05"
    assert client.call_tool("ping", {}) == {"pong": True}


def test_reconnect_exhaustion_raises_honest_error():
    class DeadTransport:
        def send_line(self, text):
            from find_yourself.adapters.mcp import _TransportDead

            raise _TransportDead("dead")

        def recv_line(self, timeout=None):
            raise AssertionError("should not be reached")

        def close(self):
            pass

    client = McpClient(transport=DeadTransport(),
                       factory=DeadTransport,
                       reconnect=ReconnectPolicy(max_attempts=1, backoff_seconds=0.0,
                                                 sleep=lambda s: None))
    with pytest.raises(McpError) as err:
        client.initialize()
    assert "reconnect exhausted" in err.value.message


# --------------------------------------------------------------------------- #
# 资源 / 提示词发现（A-统一接入-02）
# --------------------------------------------------------------------------- #


def test_resources_and_prompts_discovery_in_process():
    server = _demo_server(
        resources=[McpResource(uri="file:///notes.md", name="notes", text="# notes")],
        prompts=[McpPrompt(name="greet", description="问候模板",
                           template="你好，{name}")],
    )
    client = McpClient.from_server(server)
    client.initialize()
    resources = client.list_resources()
    assert [r["uri"] for r in resources] == ["file:///notes.md"]
    read = client.read_resource("file:///notes.md")
    assert read["contents"][0]["text"] == "# notes"
    prompts = client.list_prompts()
    assert [p["name"] for p in prompts] == ["greet"]
    got = client.get_prompt("greet")
    assert got["messages"][0]["content"]["text"] == "你好，{name}"
    with pytest.raises(McpError):
        client.read_resource("file:///missing.md")


def test_stdio_resources_and_prompts_via_real_subprocess_module(monkeypatch):
    # 走 stdio 模块入口的最小服务（ping/sha256 无资源/提示词）——验证空清单形状
    cmd = [sys.executable, "-m", "find_yourself.adapters.mcp"]
    client = McpClient.from_subprocess(cmd)
    try:
        client.initialize()
        assert client.list_resources() == []
        assert client.list_prompts() == []
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# 动态刷新（A-统一接入-09）
# --------------------------------------------------------------------------- #


def test_tools_list_changed_notification_drives_refresh():
    notified = threading.Event()
    server = _demo_server()
    sink_events: list[dict] = []

    client = McpClient.from_server(server)
    client.on_tools_changed(lambda: (notified.set(), client.refresh_tools()))
    client.on_notification(sink_events.append)
    client.initialize()

    # 进程内推送：server.notify_tools_changed() → 通知沉到客户端监听器
    server._notify_sink = lambda payload: client._handle_notification(payload)
    server.notify_tools_changed()
    assert notified.wait(2)
    assert any(p.get("method") == "notifications/tools/list_changed"
               for p in sink_events)
    assert client.supports_tool_list_changed()


def test_hub_mcp_adapter_refresh_discovers_new_tools():
    server = _demo_server()
    adapter_registry = ToolRegistryService()
    from find_yourself.services.hub.adapters import McpServerAdapter

    adapter = McpServerAdapter({"server": "demo"}, client=McpClient.from_server(server))
    registered = adapter.register_tools(adapter_registry)
    assert "hub.demo.ping" in registered
    names_before = set(adapter.tool_names())
    # 服务端动态新增工具并推送 list_changed → 客户端 refresh
    server._tools["summarize"] = McpTool(name="summarize", description="sum",
                                         handler=lambda a: {"ok": True})
    server._notify_sink = lambda payload: adapter._client._handle_notification(payload)
    server.notify_tools_changed()
    refreshed = adapter.refresh()
    assert "summarize" in {t["name"] for t in refreshed} - names_before | {"summarize"}


# --------------------------------------------------------------------------- #
# Elicitation（A-统一接入-09）
# --------------------------------------------------------------------------- #


def _elicitable_tool(server_probe: dict):
    def handler(args, ctx):
        answer = ctx.elicit("请补充目标语言", {"type": "object"})
        server_probe["answer"] = answer
        if answer.get("action") != "accept":
            return {"ok": False, "why": "declined"}
        return {"ok": True, "lang": answer.get("content", {}).get("lang")}

    return McpTool(name="translate", description="elicitable", handler=handler,
                   wants_context=True)


def test_elicitation_roundtrip_accept():
    probe: dict = {}
    server = McpStdioServer(tools=[_elicitable_tool(probe)])
    client = McpClient.from_server(server)
    client.on_elicitation(lambda params: {"action": "accept",
                                          "content": {"lang": "en"}})
    client.initialize()
    result = client.call_tool("translate", {})
    assert result == {"ok": True, "lang": "en"}
    assert probe["answer"]["action"] == "accept"


def test_elicitation_defaults_to_honest_decline_without_handler():
    probe: dict = {}
    server = McpStdioServer(tools=[_elicitable_tool(probe)])
    client = McpClient.from_server(server)  # 不注册应答器
    client.initialize()
    result = client.call_tool("translate", {})
    assert result == {"ok": False, "why": "declined"}
    assert probe["answer"]["action"] == "decline"


def test_elicitation_decline_flows_to_tool():
    probe: dict = {}
    server = McpStdioServer(tools=[_elicitable_tool(probe)])
    client = McpClient.from_server(server)
    client.on_elicitation(lambda params: {"action": "decline"})
    client.initialize()
    result = client.call_tool("translate", {})
    assert result == {"ok": False, "why": "declined"}


# --------------------------------------------------------------------------- #
# 信任分级（A-统一接入-09）
# --------------------------------------------------------------------------- #


def test_trust_untrusted_server_refuses_all_tool_calls():
    client = McpClient.from_server(_demo_server(),
                                   trust=McpTrustPolicy(level=TRUST_UNTRUSTED))
    client.initialize()
    client.list_tools()
    with pytest.raises(McpUntrustedServer) as err:
        client.call_tool("ping", {})
    assert err.value.code == ERR_UNTRUSTED_SERVER


def test_trust_remote_requires_confirmation_callback():
    client = McpClient.from_server(_demo_server(),
                                   trust=McpTrustPolicy(level=TRUST_REMOTE))
    client.initialize()
    client.list_tools()
    with pytest.raises(McpConfirmationRequired):
        client.call_tool("echo", {"text": "hi"})  # 无 confirm 回调
    confirmed: list[dict] = []

    def allow(decision: dict) -> bool:
        confirmed.append(decision)
        return True

    client2 = McpClient.from_server(
        _demo_server(), trust=McpTrustPolicy(level=TRUST_REMOTE, confirm=allow))
    client2.initialize()
    client2.list_tools()
    assert client2.call_tool("echo", {"text": "hi"}) == {"echo": "hi"}
    assert confirmed and confirmed[0]["tool"] == "echo"


def test_trust_denying_confirmation_blocks_call():
    client = McpClient.from_server(
        _demo_server(), trust=McpTrustPolicy(level=TRUST_REMOTE, confirm=lambda d: False))
    client.initialize()
    client.list_tools()
    with pytest.raises(McpConfirmationRequired):
        client.call_tool("ping", {})


def test_trust_inline_shell_blocked_for_remote_even_with_confirm():
    client = McpClient.from_server(
        _demo_server(),
        trust=McpTrustPolicy(level=TRUST_REMOTE, confirm=lambda d: True))
    client.initialize()
    client.list_tools()  # 目录里有名为 shell 的工具
    with pytest.raises(McpInlineShellBlocked):
        client.call_tool("shell", {})


def test_trust_inline_shell_blocked_for_untrusted_by_name_heuristic():
    policy = McpTrustPolicy(level=TRUST_UNTRUSTED)
    with pytest.raises(McpInlineShellBlocked):
        policy.gate_tool_call("bash")  # 无元数据也按名字启发式拒绝


def test_trust_trusted_stdio_needs_no_confirmation():
    client = McpClient.from_server(_demo_server(),
                                   trust=McpTrustPolicy(level=TRUST_TRUSTED))
    client.initialize()
    client.list_tools()
    assert client.call_tool("shell", {}) == {"ran": True}
    assert client.call_tool("ping", {}) == {"pong": True}


def test_from_subprocess_defaults_to_trusted_and_from_url_to_remote():
    local = McpClient.from_subprocess([sys.executable, "-c", "pass"])
    assert local._trust.level == TRUST_TRUSTED
    remote = McpClient.from_url("https://mcp.example.com/rpc", transport="http",
                                http=httpx.MockTransport(lambda r: httpx.Response(202)))
    assert remote._trust.level == TRUST_REMOTE
    remote.close()
    local.close()


# --------------------------------------------------------------------------- #
# B1：单一装配入口
# --------------------------------------------------------------------------- #


def test_hub_register_tools_delegates_to_single_assembly_entry(monkeypatch):
    """hub 命名空间装配走 assemble_mcp_tools(prefix="hub.")——单一入口。

    证据：把 assemble_mcp_tools 打桩后，McpServerAdapter.register_tools 必然
    经过它；不打桩时两条路径行为一致（同一冲突/跳过语义）。
    """
    import find_yourself.adapters.mcp as mcp_mod
    from find_yourself.services.hub.adapters import McpServerAdapter

    calls = []
    real = mcp_mod.assemble_mcp_tools

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(mcp_mod, "assemble_mcp_tools", spy)
    registry = ToolRegistryService()
    adapter = McpServerAdapter({"server": "demo"}, client=McpClient.from_server(_demo_server()))
    registered = adapter.register_tools(registry)
    assert registered and calls, "register_tools 必须委托 assemble_mcp_tools"
    assert calls[0]["prefix"] == "hub."
    assert "hub.demo.ping" in registered
    # 装配状态报告带 prefix 证据
    assert adapter.last_skipped == []


def test_two_namespaces_assemble_from_one_entry_without_collision():
    registry = ToolRegistryService()
    client = McpClient.from_server(_demo_server())
    statuses = assemble_mcp_tools(registry=registry, clients={"demo": client},
                                  prefix="")
    assert statuses[0]["ok"] is True
    assert "demo.ping" in statuses[0]["registered"]
    hub_client = McpClient.from_server(_demo_server())
    statuses_hub = assemble_mcp_tools(registry=registry, clients={"demo": hub_client},
                                      prefix="hub.")
    assert statuses_hub[0]["ok"] is True
    assert "hub.demo.ping" in statuses_hub[0]["registered"]
    # 两个命名空间互不干扰，且同 entry 重复装配幂等
    again = assemble_mcp_tools(registry=registry, clients={"demo": McpClient.from_server(_demo_server())},
                               prefix="hub.")
    assert again[0]["ok"] is True and again[0]["registered"]
