"""B1 · ima 通道单测（MCP 进程内桩服务器，不打真网）。

覆盖派单书验收判据的后端部分：

* MCP 主通道（B-IMA-01）：``search_knowledge`` 命中规范化 + ``src`` 出处回溯（G4）；
* REST OpenAPI 兜底（B-IMA-02）：MCP 失败时走 ``ImaSource``（MockTransport 打桩）；
* 分页 + 按类型/标签过滤（B1 验收）；
* 断网走本地缓存（B3 验收 3）：缓存命中时 ``cached=true`` 且不报错；
  实时检索零命中不得被旧缓存顶替（诚实边界）；
* 参数形状试探：``kb_id`` / ``knowledge_base_id`` 两形按序重试；
* 未配置 / 全通道失败时显式抛 ``ima_not_configured`` / ``ima_unavailable``，
  绝不返回构造出来的假条目。
"""

from __future__ import annotations

import httpx
import pytest

from find_yourself.adapters.mcp import McpClient, McpStdioServer, McpTool
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.ima_channel import (
    ImaChannel,
    ImaChannelConfig,
    reset_ima_channel,
)
from find_yourself.services.knowledge.sources.ima import ImaSource

KB = "7509748362520236"

HITS = [
    {
        "media_id": f"m{i}",
        "title": f"新手入门{i:02d}_八字怎么看日主强弱.md",
        "introduction": f"日主强弱判断口诀 {i}",
        "content": f"正文：日主强弱判断口诀 {i}",
        "tags": ["八字"] if i % 2 == 0 else ["紫微"],
        "media_type": 7 if i % 2 == 0 else 1,
        "can_fetch_content": True,
        "can_preview": True,
        "folder_info": {"name": "八字基础"},
    }
    for i in range(5)
]


def _ima_server(**overrides):
    """进程内 MCP 桩服务器：实现实测三方法中检索用到的两个。"""

    def list_bases(_args):
        return {"data": [{"kb_id": KB, "name": "八字紫微奇门印度占星塔罗排盘算命｜天地玄黄", "type": 1004}]}

    def search_knowledge(args):
        if "query" not in args:
            raise KeyError("query")
        return {"data": {"results": [dict(h) for h in HITS]}}

    tools = [
        McpTool(name="get_knowledge_base_list", description="list bases", handler=list_bases),
        McpTool(name="search_knowledge", description="search hits", handler=search_knowledge),
    ]
    for tool in overrides.pop("extra_tools", []):
        tools.append(tool)
    return McpStdioServer(tools=tools)


def _channel(*, server=None, client=None, rest=None, **cfg_kwargs) -> ImaChannel:
    config = ImaChannelConfig(
        kb_id=KB,
        cache_path=cfg_kwargs.pop("cache_path", ".runtime/ima_cache"),
        mcp_server=cfg_kwargs.pop("mcp_server", {"command": ["ima-mcp-stub"]}),
        credentials=cfg_kwargs.pop("credentials", {}),
        **cfg_kwargs,
    )
    built_client = client

    def client_factory(_cfg):
        if built_client is not None:
            return built_client
        if server is None:
            return None
        return McpClient.from_server(server)

    return ImaChannel(
        config,
        client_factory=client_factory,
        rest_factory=lambda _cfg: rest,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_ima_channel()
    yield
    reset_ima_channel()


# --- MCP 主通道 ---------------------------------------------------------------- #

def test_mcp_search_normalizes_hits_with_src(tmp_path):
    ch = _channel(server=_ima_server(), cache_path=str(tmp_path / "cache"))
    out = ch.search("八字")
    assert out["channel"] == "mcp"
    assert out["total"] == 5
    hit = out["results"][0]
    assert hit["media_id"] == "m0"
    assert hit["title"].startswith("新手入门")
    assert hit["src"] == f"ima://{KB}/m0"
    assert hit["type"] == "7"
    assert hit["folder"] == "八字基础"
    assert hit["tags"] == ["八字"]
    assert hit["can_fetch_content"] is True
    assert hit["preview_only"] is False
    assert hit["content"].startswith("正文：")


def test_mcp_search_pagination_and_filters(tmp_path):
    ch = _channel(server=_ima_server(), cache_path=str(tmp_path / "cache"))
    page2 = ch.search("八字", page=2, page_size=2)
    assert page2["pages"] == 3
    assert [h["media_id"] for h in page2["results"]] == ["m2", "m3"]

    md_only = ch.search("八字", type_="7")
    assert md_only["total"] == 3
    assert all(h["type"] == "7" for h in md_only["results"])

    tagged = ch.search("八字", tag="紫微")
    assert tagged["total"] == 2
    assert all("紫微" in h["tags"] for h in tagged["results"])


def test_search_arg_shape_retries_until_server_shape_matches(tmp_path):
    """服务器只认 knowledge_base_id 形：kb_id 形 KeyError 后换形成功。"""

    def search_knowledge(args):
        if "knowledge_base_id" not in args:
            raise KeyError("knowledge_base_id")
        return {"data": [dict(HITS[0])]}

    server = McpStdioServer(tools=[
        McpTool(name="search_knowledge", description="s", handler=search_knowledge),
    ])
    ch = _channel(server=server, cache_path=str(tmp_path / "cache"))
    out = ch.search("八字")
    assert out["total"] == 1
    # 成功形状被记忆：第二次调用直接命中，不再走失败形。
    assert ch._search_shape == ("knowledge_base_id", "query")


def test_search_shape_error_for_non_param_errors_propagates(tmp_path):
    def search_knowledge(_args):
        raise RuntimeError("quota exceeded")

    server = McpStdioServer(tools=[
        McpTool(name="search_knowledge", description="s", handler=search_knowledge),
    ])
    ch = _channel(server=server, rest=None)
    with pytest.raises(ValidationFailed) as err:
        ch.search("八字")
    # 运行时错误不属于形状问题：MCP 失败 → 无 REST → 无缓存 → 诚实失败。
    # （进程内桩服务器按 MCP 层设计只回异常类型名，不外泄 handler 内文本。）
    assert err.value.code == "ima_unavailable"
    assert "mcp:" in err.value.message


# --- REST 兜底（B-IMA-02）------------------------------------------------------ #

def test_rest_fallback_when_mcp_fails(tmp_path):
    def broken(_args):
        raise RuntimeError("mcp down")

    server = McpStdioServer(tools=[
        McpTool(name="search_knowledge", description="s", handler=broken),
    ])
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": [
            {"id": "r1", "title": "兜底命中", "content": "REST 全文"},
        ]})
    )
    rest = ImaSource(
        api_key="k", base_url="https://ima.example",
        client=httpx.Client(transport=transport),
    )
    ch = _channel(server=server, rest=rest, cache_path=str(tmp_path / "cache"))
    out = ch.search("八字")
    assert out["channel"] == "rest"
    assert out["results"][0]["title"] == "兜底命中"
    assert out["results"][0]["src"] == f"ima://{KB}/r1"
    assert out["errors"] and out["errors"][0].startswith("mcp:")


# --- 本地缓存（B3 验收 3）------------------------------------------------------- #

def test_offline_serves_cache_without_error(tmp_path):
    cache_dir = tmp_path / "cache"
    live = _channel(server=_ima_server(), cache_path=str(cache_dir))
    first = live.search("八字")
    assert first["cached"] is False

    # 断网：MCP 与 REST 全部失败，同一查询必须无报错地走本地缓存。
    class _DeadClient:
        def call_tool(self, *_a, **_k):
            raise RuntimeError("network down")

        def initialize(self):
            raise RuntimeError("network down")

    offline = _channel(
        client=_DeadClient(),
        rest=ImaSource(api_key="k", base_url="https://ima.example",
                       client=httpx.Client(transport=httpx.MockTransport(
                           lambda request: httpx.Response(503, json={}))),
        ),
        cache_path=str(cache_dir),
    )
    second = offline.search("八字")
    assert second["cached"] is True
    assert second["cache_time"]  # ISO 时间戳非空
    assert second["channel"].endswith("+cache")
    assert second["total"] == 5
    assert second["results"][0]["src"] == f"ima://{KB}/m0"


def test_zero_live_hits_not_masked_by_stale_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    live = _channel(server=_ima_server(), cache_path=str(cache_dir))
    assert live.search("八字")["total"] == 5

    # 库内容变了：同样的查询实时返回零命中 —— 必须如实返回 0，不得拿缓存顶替。
    def empty_search(_args):
        return {"data": {"results": []}}

    server = McpStdioServer(tools=[
        McpTool(name="search_knowledge", description="s", handler=empty_search),
    ])
    still_live = _channel(server=server, cache_path=str(cache_dir))
    out = still_live.search("八字")
    assert out["total"] == 0
    assert out["cached"] is False
    assert out["channel"] == "mcp"


def test_all_channels_down_without_cache_raises(tmp_path):
    class _DeadClient:
        def call_tool(self, *_a, **_k):
            raise RuntimeError("network down")

    ch = _channel(
        client=_DeadClient(),
        rest=ImaSource(api_key="k", base_url="https://ima.example",
                       client=httpx.Client(transport=httpx.MockTransport(
                           lambda request: httpx.Response(503, json={}))),
        ),
        cache_path=str(tmp_path / "cache"),
    )
    with pytest.raises(ValidationFailed) as err:
        ch.search("八字")
    assert err.value.code == "ima_unavailable"
    assert "mcp:" in err.value.message and "rest:" in err.value.message


def test_unconfigured_channel_raises_honestly(tmp_path):
    ch = _channel(mcp_server=None, rest=None, cache_path=str(tmp_path / "cache"))
    assert ch.is_configured() is False
    with pytest.raises(ValidationFailed) as err:
        ch.search("八字")
    assert err.value.code == "ima_not_configured"


def test_empty_query_rejected(tmp_path):
    ch = _channel(server=_ima_server(), cache_path=str(tmp_path / "cache"))
    with pytest.raises(ValidationFailed) as err:
        ch.search("   ")
    assert err.value.code == "ima_query_required"


# --- 状态（适配器卡 / 设置页） --------------------------------------------------- #

def test_status_reports_channels_and_kb_match(tmp_path):
    ch = _channel(server=_ima_server(), cache_path=str(tmp_path / "cache"))
    status = ch.status()
    assert status["configured"] is True
    assert status["channels"]["mcp"]["configured"] is True
    assert status["channels"]["mcp"]["available"] is True
    assert status["kb_matched"] is True
    assert status["kb_id"] == KB

    bare = _channel(mcp_server=None, rest=None, cache_path=str(tmp_path / "cache"))
    bare_status = bare.status()
    assert bare_status["configured"] is False
    assert "未接入" in bare_status["detail"]


def test_default_client_factory_merges_credentials_into_env(monkeypatch):
    """stdio 形：凭证并入子进程 env；URL 形：api_key 并入 Authorization 头。

    不真 spawn 子进程——monkeypatch ``McpClient.from_subprocess`` 记录调用。
    """
    from find_yourself.adapters.mcp import McpClient
    from find_yourself.services.knowledge.ima_channel import _default_client_factory

    recorded: dict = {}

    def fake_from_subprocess(cmd, env=None, **kwargs):
        recorded["cmd"] = cmd
        recorded["env"] = env
        return "stdio-client"

    monkeypatch.setattr(McpClient, "from_subprocess", staticmethod(fake_from_subprocess))

    config = ImaChannelConfig(
        mcp_server={"command": ["ima-mcp"], "env": {"FIXED": "1"}},
        credentials={"app_id": "app-1", "api_key": "key-1", "secret_key": "sec-1"},
    )
    client = _default_client_factory(config)
    assert client == "stdio-client"
    assert recorded["cmd"] == ["ima-mcp"]
    assert recorded["env"]["FIXED"] == "1"
    assert recorded["env"]["IMA_APP_ID"] == "app-1"
    assert recorded["env"]["IMA_API_KEY"] == "key-1"
    assert recorded["env"]["IMA_SECRET_KEY"] == "sec-1"

    # URL 形：api_key 并入 Authorization 头。
    url_config = ImaChannelConfig(
        mcp_server={"url": "http://127.0.0.1:9999/mcp", "transport": "http"},
        credentials={"api_key": "key-1"},
    )
    url_client = _default_client_factory(url_config)
    assert url_client is not None

    # 什么都没配：None（诚实未配置）。
    assert _default_client_factory(ImaChannelConfig()) is None


def test_content_cap_marks_truncation_honestly(tmp_path):
    big = dict(HITS[0])
    big["media_id"] = "big"
    big["content"] = "字" * (30000)
    server_payload = {"data": [big]}

    def search_knowledge(_args):
        return server_payload

    server = McpStdioServer(tools=[
        McpTool(name="search_knowledge", description="s", handler=search_knowledge),
    ])
    ch = _channel(server=server, cache_path=str(tmp_path / "cache"))
    out = ch.search("八字")
    hit = out["results"][0]
    assert len(hit["content"]) == 20000
    assert hit["content_truncated"] is True
