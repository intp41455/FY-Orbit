"""P1-18 受限 DSL 画布 API 测试。

写端点（POST /runs）已补挂 csrf_protected、读端点挂 get_actor，因此本文件
改为通过共享 ``client`` fixture（完整 app 鉴权栈）访问；归档目录隔离仍由
``reset_store_for_tests`` 控制。/schema 是静态元数据，保持匿名可读。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from helpers import login_owner

from find_yourself.api.routes import dsl_canvas
from find_yourself.services.dsl_canvas import (
    HTTP_METHODS,
    INPUT_KINDS,
    KNOWLEDGE_MODES,
    NODE_PARAMS_SCHEMAS,
    NODE_TYPES,
    OUTPUT_FORMATS,
    TRIGGER_KINDS,
)

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


def test_schema_endpoint_publishes_node_params_schema(client: TestClient):
    """G4-j：/schema 必须下发节点级参数契约（NODE_PARAMS_SCHEMAS 单一真源）。

    前端 11 个参数枚举据此真去重——不在这里发布，前端就只能手抄一份镜像。
    """
    r = client.get("/api/dsl-canvas/schema")
    assert r.status_code == 200
    body = r.json()
    assert "node_params_schema" in body, "缺少 node_params_schema（前端无法真去重）"
    # 值就是后端单一真源（同一对象，逐字下发，非另抄一份）。
    assert body["node_params_schema"] == NODE_PARAMS_SCHEMAS
    # 抽查节点级枚举确实随 schema 下发。
    assert body["node_params_schema"]["input"]["properties"]["kind"]["enum"] \
        == list(INPUT_KINDS)
    assert body["node_params_schema"]["output"]["properties"]["format"]["enum"] \
        == list(OUTPUT_FORMATS)
    assert body["node_params_schema"]["knowledge_retrieval"]["properties"]["mode"]["enum"] \
        == list(KNOWLEDGE_MODES)
    assert body["node_params_schema"]["http_request"]["properties"]["method"]["enum"] \
        == list(HTTP_METHODS)
    assert body["node_params_schema"]["trigger"]["properties"]["kind"]["enum"] \
        == list(TRIGGER_KINDS)
    # 既有键一个都不能少（不重命名、不删除）。
    for key in ("schema", "node_types", "transform_verbs", "verb_catalog",
                "aggregate_ops", "merge_ops", "output_formats"):
        assert key in body


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
    assert "环" in r.json()["error"]["message"]


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


def test_submit_with_stream_returns_202(client: TestClient):
    """G2：``stream:true`` → 立即 202 {run_id, status:"running"}，后台线程执行。"""
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/runs",
                    json={"dsl": _THREE_NODE_DOC, "stream": True}, headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["run_id"]


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


# ---------------------------------------------------------------------------
# G4-d · 画布内联调参即时重算（POST /preview）
#
# 核心命题：改参数后要能**立刻**看到重算结果，而且这次重算**不许留下痕迹**。
# 落库是 POST /runs 的专属副作用（_store.save 同时写内存环形缓冲与归档目录，
# max_in_memory=200），若预览也走那条路，用户每敲一个键就冲掉一条真实 run 记录。
# ---------------------------------------------------------------------------

_APPROVAL_DOC = {
    "version": "1",
    "nodes": [
        {"id": "src", "type": "input",
         "params": {"kind": "literal", "value": [{"name": "张三", "age": 34}]}},
        {"id": "apv", "type": "transform", "verb": "approval",
         "params": {"op": "memory.upsert", "target_id": "t-1"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [{"from": "src", "to": "apv"}, {"from": "apv", "to": "out"}],
}


def test_preview_recomputes_with_edited_params(client: TestClient):
    """改 template 参数 → 立刻返回按新参数重算的 output。"""
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    doc = json.loads(json.dumps(_THREE_NODE_DOC))
    doc["nodes"][1]["params"]["template"] = "您好，{name}！您今年 {age} 岁。"
    r = client.post("/api/dsl-canvas/preview", json={"dsl": doc}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["output"] == "您好，张三！您今年 34 岁。"
    assert body["preview_only"] is True
    # 逐步日志同样返回（属性面板要按节点显示中间结果）。
    assert [l["node_id"] for l in body["logs"]] == ["src", "tpl", "out"]


def test_preview_does_not_persist_a_run(client: TestClient, tmp_path):
    """预览**不落库**：run 列表不新增、归档目录不落文件。

    这是 /preview 与 /runs 的唯一区别，也是它存在的全部理由。
    """
    dsl_canvas.reset_store_for_tests(archive_dir=str(tmp_path / "runs"))
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/preview", json={"dsl": _THREE_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200, r.text

    listed = client.get("/api/dsl-canvas/runs", headers=headers).json()
    assert listed == {"items": []}
    assert list((tmp_path / "runs").glob("run-*.json")) == []


def test_preview_is_same_source_as_run(client: TestClient):
    """同源自证：同一份文档，/preview 与 /runs 的 output 与逐步日志逐字段相等。

    预览必须是真实执行器的**同一套**语义，而不是另写一条「近似重算」的旁路——
    后者会让用户在预览里看到能跑通、点运行却报错。
    """
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    preview = client.post("/api/dsl-canvas/preview", json={"dsl": _THREE_NODE_DOC},
                          headers=headers).json()
    real = client.post("/api/dsl-canvas/runs", json={"dsl": _THREE_NODE_DOC},
                       headers=headers).json()
    assert preview["output"] == real["output"]
    assert preview["status"] == real["status"] == "succeeded"
    # 时间戳是两次执行的天然差异，比时间戳才有意义。
    volatile = {"started_at", "finished_at", "run_id", "created_at"}
    assert [{k: v for k, v in log.items() if k not in volatile}
            for log in preview["logs"]] == \
           [{k: v for k, v in log.items() if k not in volatile}
            for log in real["logs"]]


def test_preview_with_approval_node_returns_202_suspended(client: TestClient):
    """含 approval 的图：预览挂起是控制流信号，返 202 而非 500。

    DslSuspended 会穿透 run_dsl 直接抛出，不显式捕获就是 500。
    """
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/preview", json={"dsl": _APPROVAL_DOC},
                    headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "suspended"
    assert body["preview_only"] is True
    assert body["suspended"]["node_id"] == "apv"
    assert [o["value"] for o in body["suspended"]["options"]] == ["approve", "reject"]


def test_preview_invalid_dsl_422(client: TestClient):
    """非法文档 422；params 封闭白名单在预览路径上同样生效（绝不 500）。"""
    dsl_canvas.reset_store_for_tests()
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/preview",
                    json={"dsl": {"version": "9", "nodes": [], "edges": []}},
                    headers=headers)
    assert r.status_code == 422

    doc = json.loads(json.dumps(_THREE_NODE_DOC))
    doc["nodes"][1]["params"]["bogus"] = 1
    r = client.post("/api/dsl-canvas/preview", json={"dsl": doc}, headers=headers)
    assert r.status_code == 422
    assert "bogus" in r.json()["error"]["message"]
