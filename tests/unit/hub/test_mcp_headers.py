"""守住远程 MCP 的认证头透传（McpServerAdapter._render_headers）。

为什么需要这个文件
------------------
`McpClient.from_url()` 一直支持 `headers` 参数，但 `McpServerAdapter._connect()`
此前调用时没传——认证头被丢弃。后果是所有需要 Bearer token 的远程 MCP Server
（WorkBuddy :57645/mcp、OpenCode :55930）固定返回 401，凭证填了也没用。

这是接多个本机 Agent 的硬阻塞，所以把「headers 真的透传下去了」钉死。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub.adapters import McpServerAdapter


class _FakeClient:
    """记录from_url 收到的参数，验证 headers 是否真的传下去了。"""

    calls: list[dict] = []

    @classmethod
    def from_url(cls, url: str, *, transport: str = "http", headers=None,
                 trust=None, **kw):
        cls.calls.append({"url": url, "transport": transport,
                          "headers": dict(headers or {})})
        inst = cls()
        return inst

    def initialize(self) -> None:
        pass

    def list_tools(self) -> list[dict]:
        return []

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_calls():
    _FakeClient.calls = []
    yield


@pytest.fixture
def fake_mcp(monkeypatch):
    """把 McpClient.from_url 换成可观测的假实现。"""
    import find_yourself.adapters.mcp as mcp_mod

    class _McpClientShim:
        from_url = staticmethod(_FakeClient.from_url)

    monkeypatch.setattr(mcp_mod, "McpClient", _McpClientShim)
    return _FakeClient


# --------------------------------------------------------------------------- #
# 核心：headers 真的透传给 from_url
# --------------------------------------------------------------------------- #


def test_bearer_token_is_forwarded_to_from_url(fake_mcp) -> None:
    """修复前必然失败：headers 被丢弃，从不传给 from_url。"""
    adapter = McpServerAdapter({
        "server": "workbuddy",
        "url": "http://127.0.0.1:57645/mcp",
        "transport": "http",
        "headers": {"Authorization": "Bearer REAL-TOKEN"},
    })
    adapter._connect()
    assert len(fake_mcp.calls) == 1
    assert fake_mcp.calls[0]["headers"] == {"Authorization": "Bearer REAL-TOKEN"}


def test_credential_template_is_rendered(fake_mcp) -> None:
    """凭证走 {credential.api_token} 占位符——用户不必把密钥写进 config 明文。"""
    adapter = McpServerAdapter({
        "server": "workbuddy",
        "url": "http://127.0.0.1:57645/mcp",
        "transport": "http",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "credentials": {"api_token": "SECRET-123"},
    })
    adapter._connect()
    assert fake_mcp.calls[0]["headers"] == {"Authorization": "Bearer SECRET-123"}


def test_param_template_also_works(fake_mcp) -> None:
    """params 也可作占位符来源，与 WebhookAdapter 保持一致。"""
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:55930/mcp",
        "transport": "http",
        "headers": {"X-API-Key": "{param.key}"},
        "params": {"key": "PARAM-KEY"},
    })
    adapter._connect()
    assert fake_mcp.calls[0]["headers"] == {"X-API-Key": "PARAM-KEY"}


# --------------------------------------------------------------------------- #
# 向后兼容：没有 headers 时行为不变
# --------------------------------------------------------------------------- #


def test_no_headers_keeps_old_behaviour(fake_mcp) -> None:
    """没配 headers 时应传空 dict（不是 None、也不是报错）。"""
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:11434/mcp",
        "transport": "http",
    })
    adapter._connect()
    assert fake_mcp.calls[0]["headers"] == {}


def test_empty_headers_dict_keeps_old_behaviour(fake_mcp) -> None:
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:11434/mcp",
        "transport": "http",
        "headers": {},
    })
    adapter._connect()
    assert fake_mcp.calls[0]["headers"] == {}


# --------------------------------------------------------------------------- #
# _render_headers 本身的行为
# --------------------------------------------------------------------------- #


def test_render_headers_returns_empty_for_non_dict() -> None:
    """headers 写成字符串/None 时当作没有——不因配置写错而崩。"""
    adapter = McpServerAdapter({"url": "http://x", "headers": "Bearer abc"})
    assert adapter._render_headers() == {}


def test_stdio_command_ignores_headers(fake_mcp) -> None:
    """stdio 传输不走 from_url，headers 不应被使用（也无副作用）。"""
    adapter = McpServerAdapter({
        "command": ["python", "-c", "print(1)"],
        "headers": {"Authorization": "Bearer X"},
    })
    assert adapter._render_headers() == {"Authorization": "Bearer X"}
    # 不调用 _connect，避免真的起子进程


def test_invalid_transport_still_raises(fake_mcp) -> None:
    """非法传输仍然显式报错，不静默降级。"""
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:1/mcp",
        "transport": "carrier-pigeon",
        "headers": {"Authorization": "Bearer X"},
    })
    with pytest.raises(ValidationFailed):
        adapter._connect()


def test_missing_command_and_url_still_raises(fake_mcp) -> None:
    adapter = McpServerAdapter({"headers": {"X": "y"}})
    with pytest.raises(ValidationFailed):
        adapter._connect()