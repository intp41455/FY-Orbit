"""包6 · A-云盘RAG-01 · 万能云盘连接器契约 + 百度网盘协议级实装单测。

全部用 ``httpx.MockTransport`` 打桩（**不打真网**）。真实云盘连通性
待用户配置凭证后验收（交付报告诚实登记）。
"""

from __future__ import annotations

import httpx
import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.sources.base import UnsupportedCapability
from find_yourself.services.knowledge.sources.baidu_pan import BaiduPanSource
from find_yourself.services.knowledge.sources.base import SourceRef, ensure_capability
from find_yourself.services.knowledge.sources.connector import (
    DriveProfile,
    DriveRoute,
    UniversalCloudDriveConnector,
)
from find_yourself.services.knowledge.sources import build_source, secret_store

TOKEN_URL = "/oauth/2.0/token"
USERINFO_URL = "/rest/2.0/passport/users/getInfo"


def _handler(routes: dict[tuple[str, str, str], object], calls: list[str], *, fail_first: int = 0, fail_status: int = 502):
    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["count"] += 1
        method_param = request.url.params.get("method", "")
        key = (request.method, request.url.path, method_param)
        calls.append(f"{request.method} {request.url.path}?{method_param}")
        if state["count"] <= fail_first:
            return httpx.Response(fail_status, json={"error": "upstream busy"})
        payload = routes.get(key)
        if payload is None:
            return httpx.Response(404, json={"errno": 2})
        if isinstance(payload, bytes):
            return httpx.Response(200, content=payload)
        return httpx.Response(200, json=payload)

    return handler


def _baidu(routes: dict[tuple[str, str], object], **kwargs) -> tuple[BaiduPanSource, list[str]]:
    calls: list[str] = []
    fail_first = kwargs.pop("fail_first", 0)
    client = httpx.Client(transport=httpx.MockTransport(_handler(routes, calls, fail_first=fail_first)))
    src = BaiduPanSource(
        app_key="test-app-key", app_secret="test-secret",
        redirect_uri="https://app.example/callback",
        client=client, timeout=5.0, **kwargs,
    )
    return src, calls


LIST_ROUTE = ("GET", "/rest/2.0/xpan/file", "list")
DOWNLOAD_ROUTE = ("GET", "/rest/2.0/xpan/file", "download")


# --------------------------------------------------------------------------- #
# 连接器契约（通用引擎）
# --------------------------------------------------------------------------- #

def test_connector_unconfigured_is_honest():
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘", route=DriveRoute(list_url="https://x/list"),
    )
    src = UniversalCloudDriveConnector(profile, credentials={})
    health = src.health_check()
    assert health["available"] is False and health["configured"] is False
    assert "未接入" in health["detail"]
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "generic_not_configured"


def test_connector_configured_without_token_reports_needs_authorization():
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(userinfo_url="https://x/me", userinfo_params={"access_token": "{access_token}"}),
    )
    src = UniversalCloudDriveConnector(profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r"})
    health = src.health_check()
    assert health["configured"] is True and health["available"] is False
    assert "OAuth 授权" in health["detail"]


def test_connector_authorize_url_and_code_exchange():
    calls: list[str] = []
    handler = _handler({("POST", "/token", ""): {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 2592000}}, calls)
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(
            authorize_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
        ),
    )
    src = UniversalCloudDriveConnector(
        profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "https://app/cb"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    url = src.authorize_url(state="st-1")
    assert url.startswith("https://auth.example/authorize?")
    assert "client_id=k" in url and "redirect_uri=https%3A%2F%2Fapp%2Fcb" in url and "state=st-1" in url
    tokens = src.exchange_code("abc")
    assert tokens["access_token"] == "at-1"
    assert any(c.startswith("POST /oauth/token") or "token" in c for c in calls)


def test_connector_refresh_tokens_requires_refresh_token():
    profile = DriveProfile(drive_type="generic", display_name="通用盘", route=DriveRoute(token_url="https://x/token"))
    src = UniversalCloudDriveConnector(profile, credentials={"app_key": "k"})
    with pytest.raises(ValidationFailed) as err:
        src.refresh_tokens()
    assert err.value.code == "generic_no_refresh_token"


def test_connector_health_probe_real_call():
    calls: list[str] = []
    handler = _handler({("GET", USERINFO_URL, ""): {"errno": 0, "userid": 42}}, calls)
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(userinfo_url=f"https://api.example{USERINFO_URL}",
                         userinfo_params={"access_token": "{access_token}"}),
    )
    src = UniversalCloudDriveConnector(
        profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r", "access_token": "tok"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    health = src.health_check(probe=True)
    assert health["available"] is True and health["degraded"] is False
    assert health["latency_ms"] is not None


def test_connector_health_probe_failure_is_honest():
    calls: list[str] = []
    handler = _handler({}, calls)  # 一切 404
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(userinfo_url="https://api.example/me",
                         userinfo_params={"access_token": "{access_token}"}),
    )
    src = UniversalCloudDriveConnector(
        profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r", "access_token": "tok"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    health = src.health_check(probe=True)
    assert health["available"] is False and health["degraded"] is True
    assert "探测失败" in health["detail"]


def test_connector_retry_on_502_then_success():
    calls: list[str] = []
    handler = _handler({("GET", "/files", ""): {"errno": 0, "list": []}}, calls, fail_first=2, fail_status=502)
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(list_url="https://api.example/files",
                         list_params={"access_token": "{access_token}", "dir": "{dir}"}),
    )
    src = UniversalCloudDriveConnector(
        profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r", "access_token": "t"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert src.list_files("/", recursive=False) == []
    assert sum(1 for c in calls if "GET /files?" in c) == 3  # 2 次失败 + 1 次成功


def test_connector_search_without_route_is_explicit():
    profile = DriveProfile(drive_type="generic", display_name="通用盘", route=DriveRoute())
    src = UniversalCloudDriveConnector(profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r"})
    with pytest.raises(UnsupportedCapability):
        src.search_metadata(SourceRef("generic", "d", "d"), "q")


def test_connector_route_misconfiguration_blows_at_call_site():
    profile = DriveProfile(
        drive_type="generic", display_name="通用盘",
        route=DriveRoute(list_url="https://x/list",
                         list_params={"access_token": "{access_token}", "dir": "{dir}", "bogus": "{missing_key}"}),
    )
    src = UniversalCloudDriveConnector(
        profile, credentials={"app_key": "k", "app_secret": "s", "redirect_uri": "r", "access_token": "t"},
        client=httpx.Client(transport=httpx.MockTransport(_handler({}, []))),
    )
    with pytest.raises(ValidationFailed) as err:
        src.list_files("/")
    assert err.value.code == "drive_route_misconfigured"


# --------------------------------------------------------------------------- #
# 百度网盘：协议级（list / download / errno / OAuth / 增量 / 骨架兼容）
# --------------------------------------------------------------------------- #

BAIDU_LIST_PAYLOAD = {
    "errno": 0,
    "list": [
        {"fs_id": 1, "path": "/资料", "server_filename": "资料", "isdir": 1,
         "size": 0, "server_mtime": 1700000000},
        {"fs_id": 2, "path": "/资料/命理笔记.md", "server_filename": "命理笔记.md",
         "isdir": 0, "size": 30, "server_mtime": 1700000100},
        {"fs_id": 3, "path": "/资料/照片.png", "server_filename": "照片.png",
         "isdir": 0, "size": 999, "server_mtime": 1700000200},
    ],
}


def test_baidu_unconfigured_keeps_v1_skeleton_contract():
    """未配置态：v1 骨架的错误码与健康检查语义原样保留（存量测试锁定）。"""
    secret_store.forget("baidu_pan")  # 防并行用例残留：凭证存储必须为空
    src = BaiduPanSource()
    health = src.health_check()
    assert health["available"] is False and health["configured"] is False
    assert "未接入" in health["detail"]
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "baidu_pan_not_implemented"
    with pytest.raises(ValidationFailed):
        src.fetch_document(SourceRef("baidu_pan", "1", "dir"))
    with pytest.raises(ValidationFailed) as err:
        src.oauth_authorize_url()
    assert err.value.code == "baidu_pan_not_implemented"
    with pytest.raises(ValidationFailed) as err:
        src.search_metadata(SourceRef("baidu_pan", "d", "d"), "q")
    assert err.value.code == "baidu_pan_not_implemented"


def test_baidu_configured_oauth_url_is_real_protocol_template():
    src, _ = _baidu({})
    url = src.oauth_authorize_url()
    assert url.startswith("https://openapi.baidu.com/oauth/2.0/authorize?")
    assert "client_id=test-app-key" in url
    assert "scope=basic%2Cnetdisk" in url or "scope=basic,netdisk" in url


def test_baidu_complete_oauth_persists_tokens_into_secret_store():
    src, calls = _baidu({("POST", "/oauth/2.0/token", ""): {"access_token": "at", "refresh_token": "rt", "expires_in": 2592000}})
    try:
        result = src.complete_oauth("auth-code-1")
        # 返回体只含脱敏信息：字段是否已填 + 有效期/scope，绝不含明文 token
        assert result["authorized"] is True
        assert result["credentials_present"] == {"access_token": True, "refresh_token": True}
        assert set(result) == {"authorized", "credentials_present", "expires_in", "scope"}
        assert secret_store.has("baidu_pan")
        assert secret_store.get("baidu_pan")["access_token"] == "at"
    finally:
        secret_store.forget("baidu_pan")


def test_baidu_list_sources_returns_root_folders():
    src, calls = _baidu({LIST_ROUTE: BAIDU_LIST_PAYLOAD}, access_token="tok")
    refs = src.list_sources()
    assert [r.external_id for r in refs] == ["/资料"]
    assert refs[0].kind == "folder"
    assert any(c.endswith("?list") for c in calls)


def test_baidu_fetch_folder_yields_text_and_skips_binary_honestly():
    src, _ = _baidu(
        {
            LIST_ROUTE: BAIDU_LIST_PAYLOAD,
            DOWNLOAD_ROUTE: "八字用神，专求月令。".encode("utf-8"),
        },
        access_token="tok",
    )
    docs = list(src.fetch_document(SourceRef("baidu_pan", "/资料", "资料", kind="folder")))
    assert [d.name for d in docs] == ["命理笔记.md"]
    assert docs[0].text == "八字用神，专求月令。"
    assert docs[0].metadata["drive_path"] == "/资料/命理笔记.md"
    skipped = src.last_sync["skipped"]
    assert skipped == [{"name": "照片.png", "reason": "unsupported_extension"}]
    assert src.last_sync["fetched"] == 1


def test_baidu_fetch_decodes_gb18030_text():
    payload = {
        "errno": 0,
        "list": [{"fs_id": 2, "path": "/t.txt", "server_filename": "t.txt",
                  "isdir": 0, "size": 30, "server_mtime": 1700000100}],
    }
    src, _ = _baidu(
        {LIST_ROUTE: payload, DOWNLOAD_ROUTE: "滴天髓：五行贵在中和。".encode("gb18030")},
        access_token="tok",
    )
    docs = list(src.fetch_document(SourceRef("baidu_pan", "/t.txt", "t.txt", kind="file")))
    assert docs[0].text == "滴天髓：五行贵在中和。"


def test_baidu_errno_surfaces_as_honest_error():
    src, _ = _baidu({LIST_ROUTE: {"errno": 111}}, access_token="tok")
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "baidu_pan_api_errno"
    assert "111" in err.value.message


def test_baidu_changes_since_incremental_cursor():
    src, _ = _baidu({LIST_ROUTE: BAIDU_LIST_PAYLOAD}, access_token="tok")
    changed = src.changes_since("/资料", 1700000050)
    assert [e["path"] for e in changed] == ["/资料/命理笔记.md", "/资料/照片.png"]
    # 游标归零 → 全部文件（目录不计）
    assert len(src.changes_since("/资料", 0)) == 2


def test_baidu_incremental_capability_gated_by_token():
    """没有 token 的实例不声明增量；有 token 才如实升级（ensure_capability 可过）。"""
    unauthenticated = BaiduPanSource(
        app_key="k", app_secret="s", redirect_uri="r",
    )
    with pytest.raises(UnsupportedCapability):
        ensure_capability(unauthenticated, "incremental")
    src, _ = _baidu({}, access_token="tok")
    ensure_capability(src, "incremental")  # 不抛即通过


def test_baidu_build_source_declares_incremental_when_token_in_store():
    try:
        secret_store.set("baidu_pan", {
            "app_key": "k", "app_secret": "s", "redirect_uri": "r", "access_token": "tok",
        })
        src = build_source("baidu_pan")
        ensure_capability(src, "incremental")
        assert src.is_configured() is True
    finally:
        secret_store.forget("baidu_pan")


def test_baidu_token_401_fails_without_retry():
    calls: list[str] = []

    def always_401(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(401, json={"errno": -6})

    src = BaiduPanSource(
        app_key="k", app_secret="s", redirect_uri="r", access_token="expired",
        client=httpx.Client(transport=httpx.MockTransport(always_401)),
    )
    with pytest.raises(ValidationFailed) as err:
        src.list_sources()
    assert err.value.code == "baidu_pan_api_error"
    assert len([c for c in calls if "xpan" in c]) == 1  # 4xx 不重试
