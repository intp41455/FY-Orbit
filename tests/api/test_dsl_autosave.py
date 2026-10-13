"""DSL 画布 autosave 路由可注册 + history 端点行为。

背景（2026-10-14）：`dsl_autosave.py:231` 曾把 `limit: int = Field(...)`
直接写在 FastAPI 函数签名上，导致 import 期 AssertionError、
整个 api 应用无法实例化（tests/api 全体收集失败）。本文件钉死：
1) create_app 可实例化且 autosave 路由已挂载；
2) GET /history/{canvas_id} 的 limit 是合法 query 参数（带边界）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner


def test_history_route_is_registered(client: TestClient):
    # create_app 返回值可能再包一层挂载，直接数 app.routes 不可靠；
    # 以 openapi 路径表为准（与真实 HTTP 路由同源）。
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/dsl-canvas/autosave/history/{canvas_id}" in paths


def test_history_defaults_to_empty_for_unknown_canvas(client: TestClient):
    headers = login_owner(client)
    r = client.get("/api/dsl-canvas/autosave/history/no-such-canvas", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["items"] == []


def test_history_limit_is_a_bounded_query_param(client: TestClient):
    # Field 误用时本路由根本不会注册；这里验证 limit 走 query 且越界被拒。
    headers = login_owner(client)
    r = client.get("/api/dsl-canvas/autosave/history/any?limit=0", headers=headers)
    assert r.status_code == 422
    r = client.get("/api/dsl-canvas/autosave/history/any?limit=101", headers=headers)
    assert r.status_code == 422
    r = client.get("/api/dsl-canvas/autosave/history/any?limit=10", headers=headers)
    assert r.status_code == 200
