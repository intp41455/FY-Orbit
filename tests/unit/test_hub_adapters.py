"""W6 · 适配器层单测：SSRF / webhook 真实调用 / 模型通道 / MCP / 知识源桥接。

网络一律用 ``httpx.MockTransport`` 打桩（外加一条**本机真实 socket** 的 mock
server 用例作为集成证据），不打真网、不 mock 冒充真实返回。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from find_yourself.adapters.mcp import McpClient, McpStdioServer, McpTool
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub import adapters as A
from find_yourself.services.tool_registry import ToolRegistryService

# --------------------------------------------------------------------------- #
# SSRF
# --------------------------------------------------------------------------- #


def test_private_endpoint_is_blocked_by_default():
    with pytest.raises(ValidationFailed) as err:
        A.guard_endpoint("http://127.0.0.1:8080/hook")
    assert err.value.code == "hub_ssrf_blocked"


@pytest.mark.parametrize("url", [
    "http://localhost/hook",
    "http://192.168.1.10/hook",
    "http://10.0.0.5/hook",
    "http://169.254.169.254/latest/meta-data",
    "http://172.16.0.1/hook",
    "http://[::1]/hook",
])
def test_private_ranges_are_all_blocked(url):
    with pytest.raises(ValidationFailed) as err:
        A.guard_endpoint(url)
    assert err.value.code == "hub_ssrf_blocked"


def test_public_endpoint_passes():
    assert A.guard_endpoint("https://api.example.com/v1/x").startswith("https://")


def test_private_endpoint_allowed_with_env_override(monkeypatch):
    monkeypatch.setenv("FY_HUB_ALLOW_PRIVATE", "1")
    assert A.guard_endpoint("http://127.0.0.1:9/hook") == "http://127.0.0.1:9/hook"


def test_non_http_scheme_rejected():
    with pytest.raises(ValidationFailed) as err:
        A.guard_endpoint("file:///etc/passwd")
    assert err.value.code == "hub_invalid_endpoint"


# --------------------------------------------------------------------------- #
# http_webhook
# --------------------------------------------------------------------------- #

_SEARCH_CFG = {
    "url": "https://api.example.com/v1/search",
    "method": "POST",
    "headers": {"Authorization": "Bearer {credential.api_token}"},
    "body_template": '{"q": "{param.query}", "limit": 5}',
    "response_path": "data.results",
    "retries": 0,
}


def _webhook_transport(recorder: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"data": {"results": [{"title": "a"}, {"title": "b"}]}})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def test_webhook_invokes_real_request_and_extracts_path():
    seen: list[httpx.Request] = []
    adapter = A.WebhookAdapter(_SEARCH_CFG, credentials={"api_token": "tok-abcdef123456"},
                               transport=_webhook_transport(seen))
    result = adapter.invoke(A.InvokeCall(action="invoke", params={"query": "季度报表"}))
    assert result.ok is True
    assert result.output == [{"title": "a"}, {"title": "b"}]
    sent = json.loads(seen[0].content.decode("utf-8"))
    assert sent == {"q": "季度报表", "limit": 5}
    # 凭证进了 Authorization 头（真实渲染），但没有出现在 output 里
    assert seen[0].headers["authorization"] == "Bearer tok-abcdef123456"


def test_webhook_missing_template_value_fails_loudly():
    cfg = dict(_SEARCH_CFG)
    adapter = A.WebhookAdapter(cfg, credentials={}, transport=_webhook_transport([]))
    result = adapter.invoke(A.InvokeCall(action="invoke", params={"query": "x"}))
    assert result.ok is False
    assert "hub_template_missing" in result.error


def test_webhook_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"data": {"results": ["ok"]}})

    cfg = dict(_SEARCH_CFG, retries=2)
    adapter = A.WebhookAdapter(cfg, credentials={"api_token": "t"},
                               transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    result = adapter.invoke(A.InvokeCall(action="invoke", params={"query": "x"}))
    assert result.ok is True and result.output == ["ok"]
    assert result.meta["attempts"] == 2


def test_webhook_reports_http_error_not_fake_success():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    cfg = dict(_SEARCH_CFG, retries=0)
    adapter = A.WebhookAdapter(cfg, credentials={"api_token": "t"},
                               transport=httpx.MockTransport(handler), sleep=lambda _s: None)
    result = adapter.invoke(A.InvokeCall(action="invoke", params={"query": "x"}))
    assert result.ok is False and "500" in result.error


def test_webhook_health_probe_reflects_status():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    adapter = A.WebhookAdapter(_SEARCH_CFG, credentials={"api_token": "t"},
                               transport=httpx.MockTransport(handler))
    report = adapter.health(timeout_seconds=1.0)
    assert report.ok is True
    assert report.capabilities  # 探活成功才带能力清单


def test_webhook_against_real_local_mock_server(monkeypatch):
    """集成证据：本机真实 socket + 真实 HTTP 服务（非 MockTransport）。"""
    monkeypatch.setenv("FY_HUB_ALLOW_PRIVATE", "1")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(json.dumps({"echo": body}).encode("utf-8"))

        def do_GET(self):  # noqa: N802 — 探活默认走 GET
            self._json(json.dumps({"status": "ok"}).encode("utf-8"))

        def log_message(self, *_args):  # 静音测试输出
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        adapter = A.WebhookAdapter(
            {"url": url, "method": "POST", "retries": 0}, credentials={}
        )
        result = adapter.invoke(A.InvokeCall(action="invoke", params={"hello": "world"}))
        assert result.ok is True
        # 服务端回 {"echo": <原样请求体>} —— 证明参数真的送到了真实 socket
        assert result.output == {"echo": {"hello": "world"}}
        assert adapter.health(timeout_seconds=2.0).ok is True
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# openai_chat / anthropic (W4 provider 复用)
# --------------------------------------------------------------------------- #


def _chat_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": "巴黎是法国首都"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
            })
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def test_chat_adapter_health_lists_real_models():
    adapter = A.ChatModelAdapter(
        {"kind": "openai_chat", "base_url": "https://api.example.com/v1",
         "api_key": "sk-unit-test-123456", "model": "gpt-4o-mini"},
        transport=_chat_transport(),
    )
    report = adapter.health(timeout_seconds=2.0)
    assert report.ok is True
    assert "2 个" in report.detail
    assert any(c.name == "chat" for c in report.capabilities)


def test_chat_adapter_invoke_returns_real_completion():
    adapter = A.ChatModelAdapter(
        {"kind": "openai_chat", "base_url": "https://api.example.com/v1",
         "api_key": "sk-unit-test-123456", "model": "gpt-4o-mini"},
        transport=_chat_transport(),
    )
    result = adapter.invoke(A.InvokeCall(action="complete", params={"prompt": "法国首都是哪"}))
    assert result.ok is True
    assert result.output["text"] == "巴黎是法国首都"
    assert result.output["model"] == "gpt-4o-mini"


def test_chat_adapter_requires_credentials_before_calling():
    adapter = A.ChatModelAdapter({"kind": "openai_chat", "base_url": "https://api.example.com/v1"})
    result = adapter.invoke(A.InvokeCall(action="complete", params={"prompt": "x"}))
    assert result.ok is False and "hub_not_configured" in result.error
    assert adapter.health().ok is False


def test_chat_adapter_rejects_unknown_action():
    adapter = A.ChatModelAdapter(
        {"kind": "openai_chat", "base_url": "https://api.example.com/v1", "api_key": "k"},
        transport=_chat_transport(),
    )
    result = adapter.invoke(A.InvokeCall(action="translate", params={}))
    assert result.ok is False and "hub_unsupported_action" in result.error


# --------------------------------------------------------------------------- #
# mcp_server
# --------------------------------------------------------------------------- #


def _mcp_client() -> McpClient:
    tools = [
        McpTool(name="ping", description="Ping", handler=lambda _a: {"pong": True}),
        McpTool(name="echo", description="Echo", handler=lambda a: {"echo": a}),
    ]
    return McpClient.from_server(McpStdioServer(tools=tools))


def test_mcp_adapter_discovers_tools_and_calls_them():
    adapter = A.McpServerAdapter({"server": "demo"}, client=_mcp_client())
    report = adapter.health(timeout_seconds=2.0)
    assert report.ok is True
    names = {c.name for c in adapter.capabilities()}
    assert names == {"tool:ping", "tool:echo"}
    result = adapter.invoke(A.InvokeCall(action="call_tool",
                                         params={"name": "echo", "arguments": {"x": 1}}))
    assert result.ok is True and result.output == {"echo": {"x": 1}}


def test_mcp_tools_register_into_tool_registry_with_hub_prefix(tmp_path):
    registry = ToolRegistryService(persist_dir=tmp_path)
    adapter = A.McpServerAdapter({"server": "demo"}, client=_mcp_client())
    registered = adapter.register_tools(registry)
    assert "hub.demo.ping" in registered
    assert "hub.demo.echo" in registered
    # 注册中心里真的能查到，且 entry 指向远端
    meta = registry.get_tool("hub.demo.ping")
    assert meta["entry"] == {"type": "mcp", "server": "demo", "remote_tool": "ping"}


def test_mcp_invalid_command_reports_honestly():
    adapter = A.McpServerAdapter({"server": "broken"})
    report = adapter.health()
    assert report.ok is False
    assert "hub_mcp_invalid_command" in report.detail


# --------------------------------------------------------------------------- #
# knowledge_source (W3 bridge)
# --------------------------------------------------------------------------- #


def test_knowledge_adapter_reports_unconfigured_honestly():
    adapter = A.KnowledgeSourceAdapter({"source_id": "baidu_pan"})
    report = adapter.health()
    assert report.ok is False
    assert "未接入" in report.detail
    result = adapter.invoke(A.InvokeCall(action="list", params={}))
    assert result.ok is False and "baidu_pan_not_implemented" in result.error


def test_knowledge_adapter_maps_w3_capabilities():
    adapter = A.KnowledgeSourceAdapter({"source_id": "ima", "api_key": "k",
                                        "base_url": "https://ima.example"})
    names = {c.name for c in adapter.capabilities()}
    # ima 声明 searchable + full_text
    assert {"knowledge.list", "knowledge.search", "knowledge.fetch"} <= names


def test_knowledge_adapter_rejects_unknown_source():
    adapter = A.KnowledgeSourceAdapter({"source_id": "dropbox"})
    result = adapter.invoke(A.InvokeCall(action="list", params={}))
    assert result.ok is False and "hub_unknown_knowledge_source" in result.error


# --------------------------------------------------------------------------- #
# factory + helpers
# --------------------------------------------------------------------------- #


def test_build_adapter_unknown_kind_raises():
    with pytest.raises(ValidationFailed) as err:
        A.build_adapter("carrier_pigeon", {})
    assert err.value.code == "hub_unknown_kind"


def test_extract_path_supports_lists_and_missing():
    payload = {"data": {"items": [{"text": "first"}, {"text": "second"}]}}
    assert A.extract_path(payload, "data.items[0].text") == "first"
    assert A.extract_path(payload, "data.items[9].text") is None
    assert A.extract_path(payload, "") == payload


def test_render_template_missing_key_is_an_error():
    with pytest.raises(ValidationFailed):
        A.render_template("{credential.nope}", {"credential": {}})
