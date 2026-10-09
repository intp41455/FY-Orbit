"""路由别名契约：别名路径必须留在schema 外，但运行时必须仍然可达。

背景（为什么需要这条守卫）：
`api/routes/profiles.py` 里`confirm_speakers` 同时挂在两条路径上——
连字符版（前端 web/src/api/profiles.ts:99 用）和下划线版（测试
tests/api/test_profiles_api.py:53 用）。FastAPI 的 operationId 由**函数名**
生成，两条路径同函数=> operationId 重复 => 每次生成 OpenAPI schema 都告警。

修法是给下划线那条加 `include_in_schema=False`，而不是删别名。这带来两个
必须同时成立的约束，任何一条被破坏都会静默出问题：

1. 别名**不能**出现在 OpenAPI schema 里  → 否则告警回来，前端 SDK 生成也会错
2. 别名在**运行时必须仍然可达**        → 否则测试/前端直接404

只断言其中一条，都会漏掉另一半。这条守卫两条一起断。
"""

from __future__ import annotations

import warnings
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from find_yourself.api.app import create_app

HYPHEN_PATH = "/api/profiles/imports/{id}/confirm-speakers"
UNDERSCORE_PATH = "/api/profiles/imports/{id}/confirm_speakers"


@pytest.fixture(scope="module")
def schema_and_client():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = create_app()
        schema = app.openapi()
    dup_warnings = [w for w in caught if "Duplicate Operation ID" in str(w.message)]
    return schema, TestClient(app), dup_warnings


def test_openapi_has_no_duplicate_operation_id(schema_and_client):
    """整个 app 的 schema 不得有任何 operationId 重复。"""
    schema, _client, _ = schema_and_client
    seen: Counter = Counter()
    for ops in schema.get("paths", {}).values():
        for op in ops.values():
            if isinstance(op, dict) and "operationId" in op:
                seen[op["operationId"]] += 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    assert duplicates == {}, f"OpenAPI 出现重复 operationId: {duplicates}"


def test_generating_schema_emits_no_duplicate_operation_warning(schema_and_client):
    """生成 schema 本身不应抛 Duplicate Operation ID 告警。

    这条比上一条更直接：它盯的是 FastAPI 自己发出的告警，而不是我们
    自己重新数一遍operationId。两者都要—— 前者防「我们数错了」，
    后者防「FastAPI 的判定方式变了」。
    """
    _schema, _client, dup_warnings = schema_and_client
    messages = [str(w.message) for w in dup_warnings]
    assert messages == [], f"生成 schema 时 FastAPI 告警: {messages}"


def test_hyphen_alias_is_documented(schema_and_client):
    """连字符版是对外契约（前端在用），必须留在 schema 里。"""
    schema, _client, _ = schema_and_client
    assert HYPHEN_PATH in schema["paths"], "连字符版被误删出schema，前端会失去契约"


def test_underscore_alias_is_hidden_from_schema(schema_and_client):
    """下划线版只给内部/测试用，必须**不在** schema 里（否则就是重复来源）。"""
    schema, _client, _ = schema_and_client
    assert UNDERSCORE_PATH not in schema["paths"], (
        "下划线别名又回到 schema 里了 —— 要么改回include_in_schema=False，"
        "要么统一前端与测试的写法后把别名删掉"
    )


@pytest.mark.parametrize("path", [HYPHEN_PATH, UNDERSCORE_PATH])
def test_both_aliases_still_reachable_at_runtime(schema_and_client, path):
    """两条路径运行时都必须仍然注册。

    判据用「不是 404」而不是「返回 200」：未鉴权时两条都应被 csrf/鉴权
    拦下（实测401）。只要路由还在，就一定能产生鉴权响应；一旦被摘掉，
    就会变成 404 —— 那才是真正的回归。
    """
    _schema, client, _ = schema_and_client
    runtime_path = path.replace("{id}", "probe-id")
    resp = client.post(runtime_path, json={"mappings": {}})
    assert resp.status_code != 404, f"{runtime_path} 已从路由表消失（404）"
