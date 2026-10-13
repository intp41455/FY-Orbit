"""G4-g · DSL 校验错误必须走统一错误信封（FROZEN_CONTRACT §1）。

缺陷：``POST /api/dsl-canvas/validate`` 命中 ``DslValidationError`` 时
``raise HTTPException(status_code=422, detail=str(exc))``，FastAPI 会把它序列化成
``{"detail": "..."}``，**绕过** ``api/errors.py`` 的统一信封
``{"error": {"code", "message", "details"}}``。

后果：前端 ``web/src/api/client.ts`` 只在响应体含 ``error`` 键时才走错误分支，
否则把整包当数据 → 前端读到的 ``error.message`` 恒为空、``details`` 永远读不到。

本文件锁定修复后的契约：``/validate`` 的 422 一律是统一信封，且不再出现裸
``detail`` 键；code 值对齐 ``api/errors.py`` 的既有约定（``validation_failed``）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
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

_CYCLIC_DOC = {
    "version": "1",
    "nodes": [{"id": "a", "type": "output"}, {"id": "b", "type": "output"}],
    "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
}


def test_validate_invalid_dsl_uses_unified_envelope(client: TestClient):
    """非法 DSL → 422 走统一信封（error.code/message/details），不再是裸 detail。"""
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate", json={"dsl": _CYCLIC_DOC},
                    headers=headers)
    assert r.status_code == 422
    body = r.json()
    # 旧契约的裸 detail 键必须消失
    assert "detail" not in body
    assert set(body) == {"error"}
    err = body["error"]
    # code 对齐 api/errors.py 既有约定（ValidationFailed.default_code）
    assert err["code"] == "validation_failed"
    # 真实可读的原因（环检测命中）
    assert err["message"] and "环" in err["message"]
    assert isinstance(err["details"], dict)


def test_validate_malformed_body_uses_unified_envelope(client: TestClient):
    """同端点其它 422 来源（请求体形态非法）也必须走统一信封。"""
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate", json={"nonsense": True},
                    headers=headers)
    assert r.status_code == 422
    body = r.json()
    assert "detail" not in body
    assert body["error"]["code"] == "validation_failed"


def test_validate_valid_dsl_still_returns_ok(client: TestClient):
    """回归护栏：合法 DSL 的成功路径不受信封改动影响。"""
    headers = login_owner(client)
    r = client.post("/api/dsl-canvas/validate", json={"dsl": _THREE_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    assert r.json() == {"valid": True,
                        "topological_order": ["src", "tpl", "out"]}
