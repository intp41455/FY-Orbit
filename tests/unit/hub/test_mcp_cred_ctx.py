"""守住 MCP 凭证的两条注入路径（平铺 vs credentials 子字典）。

为什么需要这个文件
------------------
`ConnectionService.adapter_config()`（connections.py:170-175）把解密后的凭证**平铺**
进 config（``cfg["api_token"] = ...``），没有 credentials 子字典；
而 `build_adapter(credentials=...)`（adapters.py:912-913）会把凭证并进 cfg，
manifest 导入路径可能显式带子字典。

两条路径不一致的后果（实测422/ 500）：
    headers.Authorization = "Bearer {credential.api_token}"
    -> hub_template_missing: 模板占位符没有对应的值

所以 `_credential_ctx()` 必须同时认这两种来源，且平铺优先。
这是接本机多个带认证 Agent（WorkBuddy/OpenCode）的必要条件。
"""

from __future__ import annotations

from find_yourself.services.hub.adapters import McpServerAdapter


# --------------------------------------------------------------------------- #
# 路径一：平铺（ConnectionService.adapter_config 的实际形状）
# --------------------------------------------------------------------------- #


def test_flat_credential_is_resolved() -> None:
    """真实形状：凭证平铺在 config 顶层。"""
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:8931/mcp",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "api_token": "FLAT-TOKEN",
    })
    assert adapter._render_headers() == {"Authorization": "Bearer FLAT-TOKEN"}


def test_flat_api_key_is_resolved() -> None:
    adapter = McpServerAdapter({
        "url": "http://x",
        "headers": {"X-API-Key": "{credential.api_key}"},
        "api_key": "K",
    })
    assert adapter._render_headers() == {"X-API-Key": "K"}


# --------------------------------------------------------------------------- #
# 路径二：credentials 子字典（build_adapter / manifest 导入）
# --------------------------------------------------------------------------- #


def test_nested_credential_is_resolved() -> None:
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:8931/mcp",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "credentials": {"api_token": "NESTED-TOKEN"},
    })
    assert adapter._render_headers() == {"Authorization": "Bearer NESTED-TOKEN"}


def test_flat_wins_over_nested() -> None:
    """平铺是用户刚填的实时值，优先于子字典。"""
    adapter = McpServerAdapter({
        "url": "http://x",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "credentials": {"api_token": "OLD"},
        "api_token": "NEW",
    })
    assert adapter._render_headers() == {"Authorization": "Bearer NEW"}


# --------------------------------------------------------------------------- #
# 保留键不能被当成凭证
# --------------------------------------------------------------------------- #


def test_reserved_keys_not_treated_as_credentials() -> None:
    """url / headers / server 等结构字段不能出现在渲染结果里。"""
    adapter = McpServerAdapter({
        "url": "http://127.0.0.1:8931/mcp",
        "transport": "http",
        "server": "workbuddy",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "api_token": "T",
    })
    ctx = adapter._credential_ctx()
    assert "url" not in ctx
    assert "headers" not in ctx
    assert "transport" not in ctx
    assert "server" not in ctx
    assert ctx["api_token"] == "T"


def test_params_still_available() -> None:
    """params 与 credentials 是两套命名空间，互不干扰。"""
    adapter = McpServerAdapter({
        "url": "http://x",
        "headers": {"X-Key": "{credential.api_token}", "X-Param": "{param.version}"},
        "api_token": "T",
        "params": {"version": "v2"},
    })
    assert adapter._render_headers() == {"X-Key": "T", "X-Param": "v2"}


def test_empty_credential_value_is_ignored() -> None:
    """空值不该让模板渲染成 'Bearer ' 这种半吊子请求头。"""
    adapter = McpServerAdapter({
        "url": "http://x",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
        "api_token": "",
    })
    #空值被过滤 -> 模板找不到 -> 显式报错，而不是静默发空 token
    import pytest

    from find_yourself.services.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        adapter._render_headers()


def test_no_credentials_at_all_raises_explicitly() -> None:
    """没填凭证却引用了占位符 —— 必须显式报错，不能发空 header。"""
    import pytest

    from find_yourself.services.errors import ValidationFailed

    adapter = McpServerAdapter({
        "url": "http://x",
        "headers": {"Authorization": "Bearer {credential.api_token}"},
    })
    with pytest.raises(ValidationFailed):
        adapter._render_headers()


def test_no_headers_returns_empty_without_error() -> None:
    """没配 headers 时不该报错（stdio 连接的正常情况）。"""
    adapter = McpServerAdapter({"url": "http://x", "api_token": "T"})
    assert adapter._render_headers() == {}