"""P1-18 受限 DSL 画布 API 测试。

写端点（POST /runs）已补挂 csrf_protected、读端点挂 get_actor，因此本文件
改为通过共享 ``client`` fixture（完整 app 鉴权栈）访问；归档目录隔离仍由
``reset_store_for_tests`` 控制。/schema 是静态元数据，保持匿名可读。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from find_yourself.api.routes import dsl_canvas
from find_yourself.services.dsl_canvas import NODE_TYPES
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
    # A-画布搭建器-01：节点库扩到 16 类，此处对齐单一真源 NODE_TYPES。
    assert body["node_types"] == list(NODE_TYPES)
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


# ---------------------------------------------------------------------------
# P1 · 收集式 IR 校验端点（POST /validate-ir）
# ---------------------------------------------------------------------------

def test_validate_ir_ok_returns_empty_diagnostics(client: TestClient):
    """合法文档 → valid=true 且 diagnostics 为空数组。"""
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate-ir", json={"dsl": _THREE_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body == {"valid": True, "diagnostics": []}


def test_validate_ir_collects_all_diagnostics_at_once(client: TestClient):
    """核心价值（相对旧 fail-fast）：两个节点各带一个非法字段 → 一次返回 2 条诊断。

    旧 ``POST /validate`` 只报第一个错；本端点必须把 a、b 两处都报出来，
    且 field_path 精确到具体字段。
    """
    headers = login_owner(client)
    doc = {"version": "1",
           "nodes": [
               {"id": "a", "type": "transform", "verb": "template",
                "params": {"template": "x", "bogus": 1}},
               {"id": "b", "type": "output", "params": {"format": "json", "nope": 2}},
           ],
           "edges": []}
    r = client.post("/api/dsl-canvas/validate-ir", json={"dsl": doc}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    diags = body["diagnostics"]
    assert len(diags) == 2  # 一次全量，绝不只报第一条
    assert {(d["node_id"], d["field_path"], d["code"]) for d in diags} == {
        ("a", "params.bogus", "extra_field"),
        ("b", "params.nope", "extra_field"),
    }
    for d in diags:
        assert set(d) == {"node_id", "field_path", "code", "message"}


def test_validate_ir_bad_payload_422(client: TestClient):
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate-ir", json={"nonsense": True},
                    headers=headers)
    assert r.status_code == 422
