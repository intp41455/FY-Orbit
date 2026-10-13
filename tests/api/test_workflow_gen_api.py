"""workflow_gen HTTP 层的「身份不得缺席」测试。

背景（2026-10-14 现场复核）：``api/routes/workflow_gen.py:26-27`` 的
docstring 明写「鉴权：所有端点都要身份（``get_actor``）；写操作再加
CSRF/Origin（``csrf_protected``）」，但 ``/export``、``/graph/from-dsl``、
``/graph/to-dsl`` 三个函数签名里**没有任何 actor 依赖** —— 契约自相矛盾。

本文件钉死：三个端点未登录一律 401（身份在场），且登录后 200。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from helpers import login_owner

_MINIMAL_DSL = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]},
        },
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "out"}],
}


def test_export_requires_authentication(client: TestClient):
    assert client.post("/api/workflow/export",
                       json={"dsl": _MINIMAL_DSL}).status_code == 401


def test_graph_from_dsl_requires_authentication(client: TestClient):
    assert client.post("/api/workflow/graph/from-dsl",
                       json={"dsl": _MINIMAL_DSL}).status_code == 401


def test_graph_to_dsl_requires_authentication(client: TestClient):
    assert client.post("/api/workflow/graph/to-dsl",
                       json={"nodes": [], "edges": []}).status_code == 401


def test_export_with_identity_succeeds(client: TestClient):
    headers = login_owner(client)
    resp = client.post("/api/workflow/export", headers=headers,
                       json={"dsl": _MINIMAL_DSL, "filename": "t.py"})
    assert resp.status_code == 200


def test_graph_from_dsl_with_identity_succeeds(client: TestClient):
    headers = login_owner(client)
    resp = client.post("/api/workflow/graph/from-dsl", headers=headers,
                       json={"dsl": _MINIMAL_DSL})
    assert resp.status_code == 200
    assert "graph" in resp.json()
