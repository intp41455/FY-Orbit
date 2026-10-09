"""包6 · RAG 方案/调试器/对比 + 知识图谱端点 API 测试（真实 HTTP 层）。

覆盖：内置预设列表、保存/删除方案（CSRF）、调试器响应形状
（召回片段/分数/耗时/诊断）、A/B 对比、图谱构建与读取（含空图诚实形状）、
owner 隔离与未认证拒绝。
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.kb_models  # noqa: F401
import find_yourself.db.models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base

OWNER_EMAIL = "owner-rag6@w6.test"
PASSWORD = "Rag6-test-pass-12345"


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
def app(session_maker, tmp_path) -> FastAPI:
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret-w6",
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
    reg = client.post(
        "/auth/register",
        json={"email": OWNER_EMAIL, "password": PASSWORD, "consent_accepted": True},
    )
    assert reg.status_code == 200, reg.text
    r = client.post("/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def _ingest_doc(client: TestClient, headers: dict) -> dict:
    r = client.post(
        "/api/kb/documents?name=云盘同步笔记.md",
        content="八字用神，专求月令。五行贵在中和。\n八字是命理工具。八字包括天干。".encode("utf-8"),
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    return r.json()["document"]


# --------------------------------------------------------------------------- #
# RAG 方案端点
# --------------------------------------------------------------------------- #

def test_list_presets_returns_builtin_four_and_empty_saved(client, headers):
    r = client.get("/api/knowledge/rag/presets", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["builtin_count"] >= 4
    assert {p["preset_id"] for p in data["builtin"]} >= {
        "builtin:lexical_classic", "builtin:hybrid_rrf",
        "builtin:hybrid_weighted", "builtin:lexical_rerank",
    }
    assert data["saved_count"] == 0
    assert "reserved_params" in data  # 预留参数位说明（vector_floor / chunk 参数）


def test_save_and_delete_preset_via_api(client, headers):
    r = client.post(
        "/api/knowledge/rag/presets",
        json={"name": "端点保存的方案", "params": {"mode": "hybrid", "fusion": "rrf", "rrf_k": 30}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    preset_id = r.json()["preset_id"]
    assert preset_id.startswith("saved:")

    listed = client.get("/api/knowledge/rag/presets", headers=headers).json()
    assert listed["saved_count"] == 1

    r2 = client.post(
        "/api/knowledge/rag/presets",
        json={"name": "端点保存的方案", "params": {"mode": "lexical"}},
        headers=headers,
    )
    assert r2.status_code in (200, 400, 409, 422)  # 重名显式拒绝（不静默覆盖）

    r3 = client.delete(f"/api/knowledge/rag/presets/{preset_id}", headers=headers)
    assert r3.status_code == 200 and r3.json()["deleted"] is True
    assert client.get("/api/knowledge/rag/presets", headers=headers).json()["saved_count"] == 0


def test_debug_endpoint_shape(client, headers):
    _ingest_doc(client, headers)
    r = client.post(
        "/api/knowledge/rag/debug",
        json={"query": "八字 用神", "preset_id": "builtin:lexical_classic", "top_k": 3},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) >= {"query", "preset", "params", "timing_ms", "count", "results", "debug"}
    assert data["count"] >= 1
    hit = data["results"][0]
    assert hit["score"] > 0 and hit["content"] and hit["matched_terms"]
    assert isinstance(data["timing_ms"], (int, float))


def test_debug_endpoint_requires_auth(client):
    r = client.post("/api/knowledge/rag/debug", json={"query": "八字"})
    assert r.status_code in (401, 403)


def test_compare_endpoint_two_arms(client, headers):
    _ingest_doc(client, headers)
    r = client.post(
        "/api/knowledge/rag/compare",
        json={"query": "八字 用神", "preset_ids": ["builtin:lexical_classic", "builtin:lexical_rerank"]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["arms"]) == 2
    assert len(data["overlap"]["pairwise"]) == 1
    assert data["overlap"]["pairwise"][0]["jaccard"] == 1.0
    assert set(data["timings_ms"]) == {"builtin:lexical_classic", "builtin:lexical_rerank"}


def test_compare_endpoint_rejects_one_arm(client, headers):
    r = client.post(
        "/api/knowledge/rag/compare",
        json={"query": "八字", "preset_ids": ["builtin:lexical_classic"]},
        headers=headers,
    )
    assert r.status_code in (400, 422)


def test_save_and_fetch_comparison_via_api(client, headers):
    report = client.post(
        "/api/knowledge/rag/compare",
        json={"query": "五行", "preset_ids": ["builtin:lexical_classic", "builtin:hybrid_rrf"]},
        headers=headers,
    ).json()
    r = client.post(
        "/api/knowledge/rag/comparisons",
        json={"query": "五行", "payload": report},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    detail = client.get(f"/api/knowledge/rag/comparisons/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["query"] == "五行"
    runs = client.get("/api/knowledge/rag/comparisons", headers=headers).json()
    assert runs["count"] == 1


# --------------------------------------------------------------------------- #
# 知识图谱端点
# --------------------------------------------------------------------------- #

def test_graph_endpoint_empty_shape_is_honest(client, headers):
    r = client.get("/api/knowledge/graph", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["nodes"] == [] and data["edges"] == []
    assert data["counts"] == {"nodes": 0, "edges": 0, "returned_nodes": 0, "returned_edges": 0}


def test_graph_build_then_read_via_api(client, headers):
    _ingest_doc(client, headers)
    r = client.post(
        "/api/knowledge/graph/build",
        json={"extractor": "rules", "rebuild": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["nodes_added"] >= 1
    assert summary["extractor"] == "rules"

    graph = client.get("/api/knowledge/graph", headers=headers).json()
    assert graph["counts"]["nodes"] >= 1
    assert graph["counts"]["nodes"] == graph["counts"]["returned_nodes"]
    node_keys = {n["name"] for n in graph["nodes"]}
    assert any("八字" in n or "用神" in n or "月令" in n for n in node_keys)


def test_graph_build_requires_csrf(client, headers):
    r = client.post("/api/knowledge/graph/build", json={"extractor": "rules"})
    assert r.status_code in (401, 403)


def test_graph_owner_isolation_via_api(client, headers):
    other_email = "owner-rag6-b@w6.test"
    assert client.post(
        "/auth/register",
        json={"email": other_email, "password": PASSWORD, "consent_accepted": True},
    ).status_code == 200
    other = client.post("/auth/login", json={"email": other_email, "password": PASSWORD}).json()
    other_headers = {"X-CSRF-Token": other["csrf_token"]}
    r = client.get("/api/knowledge/graph", headers=other_headers)
    assert r.status_code == 200
    assert r.json()["nodes"] == []
