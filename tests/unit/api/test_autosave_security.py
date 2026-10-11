"""真实 HTTP 请求验证隔离、重建应用后持久化、版本冲突和 CSRF。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from find_yourself.api.app import create_app
from find_yourself.api.deps import csrf_protected, get_actor
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.services.actor import Actor


@pytest.fixture
def app_factory(tmp_path):
    url = f"sqlite:///{tmp_path / 'autosave.sqlite'}"
    engines = []

    def make(owner="alice", override_csrf=True):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        engines.append(engine)
        Base.metadata.create_all(engine)
        app = create_app(session_maker=sessionmaker(engine, expire_on_commit=False),
                         settings=Settings(environment="test", database_url=url,
                                           session_secret="autosave-test-secret-0123456789abcdef"))
        app.dependency_overrides[get_actor] = lambda: Actor.owner(owner, "csrf-test")
        if override_csrf:
            app.dependency_overrides[csrf_protected] = lambda: Actor.owner(owner)
        return TestClient(app)

    yield make
    for engine in engines:
        engine.dispose()


BASE = "/api/dsl-canvas/autosave"


def test_owner_isolation_all_operations_and_restart(app_factory):
    a = app_factory()
    body = {"canvas_id": "shared", "version": 1, "content": {"private": "alice"}}
    assert a.post(BASE + "/save", json=body).json()["saved"] is True
    b = app_factory("bob")
    assert b.get(BASE + "/load/shared").json() is None
    assert b.get(BASE + "/status/shared").json()["version"] == 0
    assert b.get(BASE + "/history/shared").json()["items"] == []
    assert b.delete(BASE + "/shared").json()["deleted"] is False
    body["content"] = {"private": "bob"}
    assert b.post(BASE + "/save", json=body).json()["saved"] is True
    # 新应用、新引擎和新连接读回磁盘；并非重用进程字典。
    restarted = app_factory()
    assert restarted.get(BASE + "/load/shared").json()["content"] == {"private": "alice"}


def test_versions_checksum_history_and_query_validation(app_factory):
    c = app_factory()
    body = {"canvas_id": "x", "version": 1, "content": {"x": 1}, "checksum": "untrusted"}
    assert c.post(BASE + "/save", json=body).json()["version"] == 1
    body["content"] = {"x": 2}
    assert c.post(BASE + "/save", json=body).json()["version"] == 2
    conflict = c.post(BASE + "/save", json=body).json()
    assert conflict["saved"] is False
    assert conflict["conflict"]["server_content"] == {"x": 2}
    saved = c.get(BASE + "/load/x").json()
    assert len(saved["checksum"]) == 64 and saved["checksum"] != "untrusted"
    assert c.get(BASE + "/history/x?limit=1").json()["items"][0]["version"] == 2
    assert c.get(BASE + "/history/x?limit=0").status_code == 422


def test_write_requires_csrf(app_factory):
    c = app_factory(override_csrf=False)
    assert c.post(BASE + "/save", json={"canvas_id": "x", "version": 1}).status_code == 403
    assert c.delete(BASE + "/x").status_code == 403


def test_run_and_debug_owner_isolation(app_factory):
    doc = {"version": "1", "nodes": [
        {"id": "in", "type": "input", "params": {"value": "private"}},
        {"id": "out", "type": "output", "params": {"format": "json"}},
    ], "edges": [{"from": "in", "to": "out"}]}
    a = app_factory()
    run = a.post("/api/dsl/runs", json=doc)
    assert run.status_code == 200, run.text
    rid = run.json()["run_id"]
    b = app_factory("bob")
    assert b.get(f"/api/dsl-canvas/runs/{rid}").status_code == 404
    assert {"run_id": rid} not in b.get("/api/dsl-canvas/runs").json()["items"]
    assert b.get(f"/api/dsl/runs/{rid}/state").status_code == 404
    for action in ("step-into", "step-over", "step-next", "continue", "reset"):
        response = b.post(f"/api/dsl/debug/runs/{rid}/node/in/{action}")
        # 未注册动作是 404；已注册动作须拒绝跨 owner。
        assert response.status_code == 404, (action, response.text)
    assert a.get(f"/api/dsl/runs/{rid}/state").status_code == 200


def test_hitl_events_are_scoped():
    import asyncio

    from find_yourself.api.routes.hitl import _broadcast_event, _sse_subscribers
    a, b = asyncio.Queue(), asyncio.Queue()
    _sse_subscribers["test-a"] = ("owner:alice", a)
    _sse_subscribers["test-b"] = ("owner:bob", b)
    try:
        _broadcast_event("interrupt_decided", {"id": "private"}, "owner:alice")
        assert a.get_nowait()["event"] == "interrupt_decided"
        assert b.empty()
    finally:
        _sse_subscribers.pop("test-a", None)
        _sse_subscribers.pop("test-b", None)
