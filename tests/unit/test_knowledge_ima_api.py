"""B1 · ima 检索端点 HTTP 单测（``/api/knowledge/ima/*``）。

覆盖：

* 鉴权：未登录 401；
* ``POST /search``：真实通道（桩）结果透传，含 ``src`` 出处与分页字段（B2/G4）；
* 断网缓存态透传：``cached=true`` + ``cache_time``（B3 验收 3）；
* ``GET /status``：通道状态透传；
* ``POST /api/kb/sources/ima/configure``：App ID / API Key / Secret Key 三件套
  经 SECRETS 门禁入库（G2），且 ``credentials_present`` 只报「哪些字段已填」；
* 请求体校验：空 query 422。

通道本身在 ``tests/unit/test_ima_channel.py`` 已用进程内 MCP 桩服务器单测；
这里把路由依赖整体替换为桩，验证 HTTP 契约与鉴权，不打真网、不 spawn 子进程。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.services.knowledge.sources import secret_store

LOCAL_TOKEN = "dev-token-secret-b1-ima"
KB = "7509748362520236"


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


from find_yourself.db.types import TZDateTime  # noqa: E402

TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def session_maker():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def app(session_maker) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        owner_id="owner",
    )
    return create_app(session_maker=session_maker, settings=settings)


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


class _StubChannel:
    """路由测试桩：按预设载荷返回，记录收到的参数以便断言。"""

    def __init__(self, payload: dict | None = None, status: dict | None = None):
        self.payload = payload or {"total": 0, "page": 1, "page_size": 10, "pages": 1,
                                   "results": [], "channel": "mcp", "cached": False,
                                   "cache_time": None, "query": "", "kb_id": KB, "errors": []}
        self.status_payload = status or {"source_id": "ima", "configured": True}
        self.calls: list[dict] = []

    def search(self, query: str, **kwargs) -> dict:
        self.calls.append({"query": query, **kwargs})
        return {**self.payload, "query": query}

    def status(self) -> dict:
        return self.status_payload


@pytest.fixture()
def stub(monkeypatch) -> _StubChannel:
    channel = _StubChannel()

    def fake_get():
        return channel

    monkeypatch.setattr(
        "find_yourself.api.routes.knowledge_ima.get_ima_channel", fake_get
    )
    return channel


HIT = {
    "media_id": "m42",
    "title": "新手入门02_八字怎么看日主强弱.md",
    "introduction": "日主强弱判断口诀……",
    "content": "正文全文：日主强弱判断口诀……",
    "content_truncated": False,
    "tags": ["八字"],
    "folder": "八字基础",
    "type": "7",
    "can_fetch_content": True,
    "can_preview": True,
    "preview_only": False,
    "origin_url": "",
    "src": f"ima://{KB}/m42",
}


# --- 鉴权 -------------------------------------------------------------------- #

def test_ima_search_requires_auth(client):
    r = client.post("/api/knowledge/ima/search", json={"query": "八字"})
    assert r.status_code == 401


def test_ima_status_requires_auth(client):
    r = client.get("/api/knowledge/ima/status")
    assert r.status_code == 401


# --- 检索 -------------------------------------------------------------------- #

def test_ima_search_returns_hits_with_src(client, headers, stub):
    stub.payload.update({"total": 1, "results": [HIT], "channel": "mcp"})
    r = client.post("/api/knowledge/ima/search", json={"query": "八字"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    hit = body["results"][0]
    assert hit["src"] == f"ima://{KB}/m42"
    assert hit["media_id"] == "m42"
    assert hit["content"].startswith("正文全文")
    assert stub.calls == [{"query": "八字", "page": 1, "page_size": 10, "type_": None,
                           "tag": None, "kb_id": None}]


def test_ima_search_passes_pagination_and_type_filter(client, headers, stub):
    client.post(
        "/api/knowledge/ima/search",
        json={"query": "紫微", "page": 2, "page_size": 5, "type": "7", "tag": "八字"},
        headers=headers,
    )
    call = stub.calls[0]
    assert call["page"] == 2 and call["page_size"] == 5
    assert call["type_"] == "7" and call["tag"] == "八字"


def test_ima_search_offline_cache_state_passthrough(client, headers, stub):
    stub.payload.update({
        "total": 1, "results": [HIT], "channel": "mcp+cache",
        "cached": True, "cache_time": "2026-10-07T08:00:00+00:00",
    })
    r = client.post("/api/knowledge/ima/search", json={"query": "八字"}, headers=headers)
    body = r.json()
    assert body["cached"] is True
    assert body["cache_time"] == "2026-10-07T08:00:00+00:00"
    assert body["channel"] == "mcp+cache"


def test_ima_search_empty_query_rejected(client, headers):
    r = client.post("/api/knowledge/ima/search", json={"query": ""}, headers=headers)
    assert r.status_code == 422


def test_ima_search_page_size_bounds(client, headers):
    r = client.post("/api/knowledge/ima/search",
                    json={"query": "x", "page_size": 999}, headers=headers)
    assert r.status_code == 422


# --- 状态 -------------------------------------------------------------------- #

def test_ima_status_returns_channel_report(client, headers, stub):
    stub.status_payload = {
        "source_id": "ima",
        "kb_id": KB,
        "configured": True,
        "channels": {"mcp": {"configured": True, "available": True}, "rest": {"configured": False}},
        "credentials_present": {"app_id": True, "api_key": True, "secret_key": True, "base_url": False},
        "detail": "ima MCP 通道可用",
    }
    r = client.get("/api/knowledge/ima/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["kb_id"] == KB
    assert body["channels"]["mcp"]["available"] is True
    # 只报「哪些字段已填」，绝不回显值。
    assert body["credentials_present"]["app_id"] is True
    assert all(isinstance(v, bool) for v in body["credentials_present"].values())


# --- G2 凭证配置（设置页 API） -------------------------------------------------- #

def test_configure_ima_credentials_roundtrip(client, headers):
    before = secret_store.get("ima")
    try:
        r = client.post(
            "/api/kb/sources/ima/configure",
            json={"app_id": "app-x", "api_key": "key-x", "secret_key": "sec-x"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] is True
        creds = secret_store.get("ima")
        assert creds.get("app_id") == "app-x"
        assert creds.get("api_key") == "key-x"
        assert creds.get("secret_key") == "sec-x"

        sources = client.get("/api/kb/sources", headers=headers).json()["sources"]
        ima_card = next(s for s in sources if s["source_id"] == "ima")
        assert ima_card["credentials_present"]["app_id"] is True
        assert ima_card["credentials_present"]["secret_key"] is True
        # 卡片只带布尔，不带值。
        assert "app-x" not in str(ima_card)
    finally:
        secret_store.forget("ima")
        if before:
            secret_store.set("ima", before)


def test_configure_ima_rejects_empty(client, headers):
    r = client.post("/api/kb/sources/ima/configure", json={}, headers=headers)
    assert r.status_code == 422
