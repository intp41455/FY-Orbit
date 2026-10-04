"""P1-18 受限 DSL 画布 API 测试。

写端点（POST /runs）已补挂 csrf_protected、读端点挂 get_actor，因此本文件
改为通过共享 ``client`` fixture（完整 app 鉴权栈）访问；归档目录隔离仍由
``reset_store_for_tests`` 控制。/schema 是静态元数据，保持匿名可读。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from find_yourself.api.routes import dsl_canvas
from helpers import login_owner

_THREE_NODE_DOC = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
        {"id": "tpl", "type": "transform", "verb": "template",
         "params": {"template": "你好，{name}！你今年 {age} 岁。"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "tpl"}, {"from": "tpl", "to": "out"}],
}


def test_schema_endpoint(client: TestClient):
    r = client.get("/api/dsl-canvas/schema")
    assert r.status_code == 200
    body = r.json()
    assert body["node_types"] == ["input", "transform", "output"]
    assert body["schema"]["properties"]["version"]["const"] == "1"


def test_validate_ok_and_cycle(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate", json={"dsl": _THREE_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    assert r.json() == {"valid": True, "topological_order": ["src", "tpl", "out"]}

    cyclic = {"version": "1",
              "nodes": [{"id": "a", "type": "output"}, {"id": "b", "type": "output"}],
              "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}
    r = client.post("/api/dsl-canvas/validate", json={"dsl": cyclic}, headers=headers)
    assert r.status_code == 422
    assert "环" in r.json()["detail"]


def test_submit_and_fetch_run(client: TestClient, tmp_path):
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _THREE_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    body = r.json()
    run_id = body["run_id"]
    assert body["status"] == "succeeded"
    assert body["output"] == "你好，张三！你今年 34 岁。"

    # GET 结果与逐步日志
    r = client.get(f"/api/dsl-canvas/runs/{run_id}", headers=headers)
    assert r.status_code == 200
    fetched = r.json()
    assert [l["node_id"] for l in fetched["logs"]] == ["src", "tpl", "out"]
    assert fetched["logs"][2]["output"] == "你好，张三！你今年 34 岁。"

    # 归档文件真实落盘
    archived = list((tmp_path / "runs").glob("run-*.json"))
    assert len(archived) == 1

    r = client.get("/api/dsl-canvas/runs/missing-id", headers=headers)
    assert r.status_code == 404


def test_submit_invalid_dsl_422(client: TestClient):
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/runs",
                    json={"dsl": {"version": "9", "nodes": [], "edges": []}},
                    headers=headers)
    assert r.status_code == 422
