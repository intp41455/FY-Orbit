"""快照与分叉的跨 owner 攻击回归。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from find_yourself.api.app import create_app
from find_yourself.api.deps import csrf_protected, get_actor
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import Conversation, Message
from find_yourself.services.actor import Actor


@pytest.fixture
def clients(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshots.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    def make(owner):
        identity = owner if isinstance(owner, Actor) else Actor.owner(owner)
        app = create_app(session_maker=factory, settings=Settings(
            environment="test", database_url="sqlite://",
            session_secret="snapshot-test-0123456789abcdef0123456789"))
        app.dependency_overrides[get_actor] = lambda: identity
        app.dependency_overrides[csrf_protected] = lambda: identity
        return TestClient(app)
    make.session_factory = factory
    yield make
    engine.dispose()


def test_snapshot_namespace_filters_runs_and_survives_restart(clients):
    alice, bob = clients("alice"), clients("bob")
    doc = {"version": "1", "nodes": [{"id": "in", "type": "input",
           "params": {"value": "alice-private"}}], "edges": []}
    response = alice.post("/api/dsl/runs", json=doc)
    assert response.status_code == 200, response.text
    rid = response.json()["run_id"]
    a = alice.post("/api/session-state/snapshot", json={"session_key": "same"}).json()["snapshot"]
    b = bob.post("/api/session-state/snapshot", json={"session_key": "same"}).json()["snapshot"]
    assert rid in [r["run_id"] for r in a["state"]["dsl_runs"]["runs"]]
    assert rid not in [r["run_id"] for r in b["state"]["dsl_runs"]["runs"]]
    assert a["state"]["tool_registry"]["tools"] == []
    restarted = clients("alice")
    restored = restarted.post("/api/session-state/restore?session_key=same")
    assert restored.status_code == 200, restored.text
    assert restored.json()["snapshot"]["owner_subject"] == "owner:alice"
    assert bob.get("/api/session-state/windows/same").status_code == 404
    assert bob.post("/api/session-state/restore?session_key=alice-only").status_code == 404


def test_fork_list_compare_discard_timeline_are_owner_scoped(clients):
    a, b = clients("alice"), clients("bob")
    fork = a.post("/api/session-state/forks", json={"source_thread_id": "alice-task"}).json()["fork"]
    fid = fork["fork_id"]
    assert b.get("/api/session-state/forks").json()["forks"] == []
    assert b.get("/api/session-state/timeline").json()["events"] == []
    assert b.post(f"/api/session-state/forks/{fid}/compare").status_code == 404
    assert b.post(f"/api/session-state/forks/{fid}/discard").status_code == 404
    assert a.post(f"/api/session-state/forks/{fid}/compare").status_code == 200


def test_foreign_conversation_cannot_enter_snapshot(clients):
    with clients.session_factory() as db:
        db.add(Conversation(id="private-conv", owner_id="alice", domain="personal"))
        db.flush()
        db.add(Message(id="private-msg", conversation_id="private-conv", role="user",
                       content="private-text", client_message_id="client-1"))
        db.commit()
    body = {"session_key": "conversation", "conversation_id": "private-conv"}
    assert clients("bob").post("/api/session-state/snapshot", json=body).status_code == 404
    response = clients("alice").post("/api/session-state/snapshot", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["snapshot"]["state"]["memory_window"]["recent_messages"][0]["content"] == "private-text"


def test_service_cannot_capture_or_restore_owner_state(clients):
    service = clients(Actor.service("worker", "worker"))
    assert service.post("/api/session-state/snapshot", json={"session_key": "x"}).status_code == 403
    assert service.post("/api/session-state/restore?session_key=x").status_code == 403
