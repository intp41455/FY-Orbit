"""dsl_sdk 测试包公共装置：内存 SQLite + 真实 app（与 test_verb_set.py 同款）。

外加：仓库根入 sys.path（让测试进程能 import 顶层的 ``find_yourself_dsl``
运行库做镜像校验/漂移护栏）与共享 run 存档的隔离复位。
"""

from __future__ import annotations

import sys
from pathlib import Path
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
from find_yourself.db.types import TZDateTime

# 顶层 find_yourself_dsl/（B4 自包含运行库）在仓库根；测试进程 import 它做
# 语义/契约镜像校验。src 布局下 editable 安装只带 src/，故显式补仓库根。
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LOCAL_TOKEN = "dev-token-secret-dsl-sdk"


def _tz_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=__import__("datetime").timezone.utc)
    return value


TZDateTime.process_result_value = _tz_result_value  # type: ignore[method-assign]


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
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
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token=LOCAL_TOKEN,
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
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


@pytest.fixture(autouse=True)
def _isolated_shared_registry():
    """隔离进程级注册表与共享 run 存档（自定义节点/reducer/运行存档）。"""
    from find_yourself.api.routes import dsl_canvas as canvas_routes
    from find_yourself.services import dsl_sdk as sdk

    custom_snapshot = dict(sdk.CUSTOM_NODE_REGISTRY)
    reducer_snapshot = dict(sdk.REDUCER_REGISTRY)
    entries_snapshot = {m: dict(v) for m, v in sdk._MODE_ENTRIES.items()}
    canvas_routes.reset_store_for_tests()
    try:
        yield
    finally:
        sdk.CUSTOM_NODE_REGISTRY.clear()
        sdk.CUSTOM_NODE_REGISTRY.update(custom_snapshot)
        sdk.REDUCER_REGISTRY.clear()
        sdk.REDUCER_REGISTRY.update(reducer_snapshot)
        sdk._MODE_ENTRIES.clear()
        sdk._MODE_ENTRIES.update(entries_snapshot)
        canvas_routes.reset_store_for_tests()
