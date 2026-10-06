"""P9 接口层单测 · 点哪评哪 HTTP 面（A-点哪评哪-05/06/07/08/09/10 + A-路线-03）。

覆盖：路由真的挂上了（不是 404）、未鉴权写端点被拒、双路线决策端点可达、
导出与热刷新端点的形状。**这是「锁二·路由注册」的回归护栏**——
如果 review 路由从 api_router 掉出去，这里会立刻红。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from find_yourself.api.app import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# 路由存在性（守护「锁二」）
# --------------------------------------------------------------------------- #

REVIEW_PATHS = [
    "/api/review/sessions",
    "/api/review/sessions/{session_id}",
    "/api/review/sessions/{session_id}/notes",
    "/api/review/notes/{note_id}",
    "/api/review/sessions/{session_id}/refresh",
    "/api/review/sessions/{session_id}/export",
    "/api/review/route",
    "/api/review/route/batch",
]


def test_review_routes_are_registered(client: TestClient):
    """★ 锁二护栏：8 条 review 路径全部出现在 OpenAPI schema 中。"""
    schema = client.app.openapi()  # type: ignore[attr-defined]
    paths = set(schema.get("paths", {}))
    missing = [p for p in REVIEW_PATHS if p not in paths]
    assert not missing, f"以下 review 路由未挂载：{missing}"


def test_review_route_is_not_404(client: TestClient):
    """路由存在但需鉴权 —— 未鉴权应是 401/403，而不是 404。"""
    r = client.post(
        "/api/review/route",
        json={"mode": "dom", "selector": "body > button", "tag": "button"},
    )
    assert r.status_code != 404, "review 路由掉了（404）—— 锁二回归"
    assert r.status_code in (401, 403)


def test_write_endpoints_require_auth(client: TestClient):
    """写端点未鉴权一律拒绝（不是 404）。"""
    r = client.post("/api/review/sessions", json={"page": "/chat"})
    assert r.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# 双路线决策端点（无副作用，但仍需鉴权）
# --------------------------------------------------------------------------- #


def test_route_batch_endpoint_exists(client: TestClient):
    r = client.post(
        "/api/review/route/batch",
        json=[{"mode": "dom", "selector": "#a", "tag": "button"}],
    )
    assert r.status_code != 404
    assert r.status_code in (401, 403)


def test_export_endpoint_exists(client: TestClient):
    r = client.get("/api/review/sessions/whatever/export")
    assert r.status_code != 404
    assert r.status_code in (401, 403)
