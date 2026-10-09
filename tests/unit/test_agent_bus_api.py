"""HTTP tests: W7 Agent 通信总线 ``/api/bus``。

覆盖：发/拉闭环、``from_identity`` 不可伪造、CSRF/鉴权门禁、越权房间、
SSE 续传、handoff 计数、共享上下文登记与引用、后台触发不阻塞发消息。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.runtime.agent_bus import dm_room
from find_yourself.services.actor import Actor
from find_yourself.services.agent_teams import AgentTeamService

LOCAL_TOKEN = "dev-token-secret-w7"


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
        local_token=LOCAL_TOKEN,
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
    r = client.post("/auth/local/dev-token", json={"token": LOCAL_TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def _owner_id(client: TestClient, headers: dict) -> str:
    """真实 owner id 只能从服务端会话读，测试不能自己编一个。"""
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["owner_id"]


def _task_room(session_maker, owner_id: str) -> str:
    """任务房间沿用既有 task id；每条用例用独立 id，避免共享总线里的历史串味。"""
    task_id = f"task-{uuid4().hex[:8]}"
    s = session_maker()
    try:
        s.add(Task(
            id=task_id, owner_id=owner_id, root_task_id=task_id, goal="g",
            domain="work", status="running", idempotency_key=f"ik-{task_id}",
            deadline=utcnow(),
        ))
        s.commit()
    finally:
        s.close()
    return task_id


def _team_room(session_maker, owner_id: str, started=True) -> str:
    s = session_maker()
    try:
        teams = AgentTeamService(s)
        actor = Actor.owner(owner_id)
        team = teams.create_team(
            actor, name="工程团队", template_id="engineering",
            default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
            budget_ref={"root_budget_usd": 0.5, "member_reserve_cap_usd": 0.05},
        )
        s.commit()
        if started:
            teams.start_team(actor, team.id, expected_version=1)
            s.commit()
    finally:
        s.close()
    return team.id


def _post(client, headers, room, **body):
    return client.post(f"/api/bus/{room}/messages", json=body, headers=headers)


def _wait_for(predicate, timeout=6.0, interval=0.05):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


# ---------------------------------------------------------------------------
# 发 / 拉闭环
# ---------------------------------------------------------------------------
def test_post_then_get_roundtrip(client: TestClient, headers: dict, session_maker):
    room = _task_room(session_maker, _owner_id(client, headers))
    r = _post(client, headers, room, content="进度如何？")
    assert r.status_code == 201, r.text
    sent = r.json()["message"]
    assert sent["room"] == room
    assert sent["from_identity"].startswith("owner:")
    assert sent["kind"] == "text"

    got = client.get(f"/api/bus/{room}/messages", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["count"] == 1
    assert got.json()["items"][0]["content"] == "进度如何？"


def test_after_id_returns_only_newer_messages(client: TestClient, headers: dict, session_maker):
    room = _task_room(session_maker, _owner_id(client, headers))
    first = _post(client, headers, room, content="第一条").json()["message"]["id"]
    _post(client, headers, room, content="第二条")
    got = client.get(
        f"/api/bus/{room}/messages", params={"after_id": first}, headers=headers
    )
    items = got.json()["items"]
    assert [m["content"] for m in items] == ["第二条"]
    assert got.json()["next_after_id"] == items[-1]["id"]


def test_forged_from_identity_in_body_is_ignored(client: TestClient, headers: dict, session_maker):
    room = _task_room(session_maker, _owner_id(client, headers))
    r = _post(client, headers, room, content="我是谁？", from_identity="agent:boss")
    assert r.status_code == 201, r.text
    assert r.json()["message"]["from_identity"] != "agent:boss"
    assert r.json()["message"]["from_identity"].startswith("owner:")


# ---------------------------------------------------------------------------
# 鉴权 / CSRF / 越权
# ---------------------------------------------------------------------------
def test_unauthenticated_read_and_write_rejected(client: TestClient, session_maker):
    # 这两条用例不登录，房间归属谁无所谓（请求根本到不了可见性判定）
    room = _task_room(session_maker, "owner-1")
    assert client.get(f"/api/bus/{room}/messages").status_code in (401, 403)
    assert _post(client, {}, room, content="hi").status_code in (401, 403)


def test_write_without_csrf_header_rejected(client: TestClient, session_maker):
    room = _task_room(session_maker, "owner-1")
    r = _post(client, {}, room, content="hi")
    assert r.status_code in (401, 403)


def test_unknown_room_returns_404_not_an_empty_room(client: TestClient, headers: dict):
    r = _post(client, headers, "room-nope", content="hi")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "bus_room_not_found"


def test_dm_room_rejects_a_third_party(client: TestClient, headers: dict):
    room = dm_room("owner:someone-else", "agent:coder")
    r = _post(client, headers, room, content="我能进吗")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "bus_room_forbidden"


def test_global_room_is_reachable_for_an_owner(client: TestClient, headers: dict):
    r = _post(client, headers, "global", content="全体注意")
    assert r.status_code == 201, r.text
    got = client.get("/api/bus/global/messages", headers=headers)
    assert got.status_code == 200
    assert any(m["content"] == "全体注意" for m in got.json()["items"])


def test_room_schema_is_served_not_hardcoded_client_side(client: TestClient, headers: dict):
    r = client.get("/api/bus/rooms/schema", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_identity_is_server_derived"] is True
    assert "dm" in body["kinds"]


# ---------------------------------------------------------------------------
# SSE 续传
# ---------------------------------------------------------------------------
def test_sse_stream_replays_after_id(client: TestClient, headers: dict, session_maker):
    room = _task_room(session_maker, _owner_id(client, headers))
    _post(client, headers, room, content="错过的消息")
    with client.stream(
        "GET", f"/api/bus/{room}/stream",
        params={"after_id": 0, "max_events": 1}, headers=headers,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        seen = "\n".join(resp.iter_lines())
    assert "id: 1" in seen
    assert "event: message" in seen
    assert "错过的消息" in seen


def test_sse_stream_resumes_from_last_event_id_header(client: TestClient, headers: dict, session_maker):
    """断线重连用 ``Last-Event-ID``：只补发客户端没看过的那部分。"""
    room = _task_room(session_maker, _owner_id(client, headers))
    first_id = _post(client, headers, room, content="已看过").json()["message"]["id"]
    _post(client, headers, room, content="断线期间的消息")
    with client.stream(
        "GET", f"/api/bus/{room}/stream",
        params={"max_events": 1}, headers={**headers, "Last-Event-ID": str(first_id)},
    ) as resp:
        assert resp.status_code == 200
        seen = "\n".join(resp.iter_lines())
    assert "断线期间的消息" in seen
    assert "已看过" not in seen


def test_sse_stream_refuses_an_unauthorized_room(client: TestClient, headers: dict, session_maker):
    _task_room(session_maker, _owner_id(client, headers))
    # 他人私聊房间：可见性校验在开流之前，不会先建立连接再报错
    r = client.get(
        f"/api/bus/{dm_room('owner:someone-else', 'agent:coder')}/stream",
        params={"max_events": 1}, headers=headers,
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# handoff 徽标计数与共享上下文
# ---------------------------------------------------------------------------
def test_handoff_counts_endpoint(client: TestClient, headers: dict, session_maker):
    room = _team_room(session_maker, _owner_id(client, headers))
    _post(client, headers, room, content="交给审查", kind="handoff", mention="agent:reviewer")
    _post(client, headers, room, content="再交一次", kind="handoff", mention="agent:reviewer")
    r = client.get(f"/api/bus/{room}/handoffs", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert list(body["edges"].values()) == [2]


def test_context_register_then_reference(client: TestClient, headers: dict, session_maker):
    room = _team_room(session_maker, _owner_id(client, headers))
    added = client.post(
        f"/api/bus/{room}/context",
        json={"entries": [{"kind": "file_ref", "title": "需求", "ref": "kb://doc/42"}]},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    ctx_id = added.json()["items"][0]["id"]

    listed = client.get(f"/api/bus/{room}/context", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    msg = _post(client, headers, room, content="按这份文档来", refs=[ctx_id])
    assert msg.status_code == 201, msg.text
    assert msg.json()["message"]["refs"] == [ctx_id]

    bad = _post(client, headers, room, content="悬空引用", refs=["ctx-nope"])
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["code"] == "bus_unknown_context_ref"


# ---------------------------------------------------------------------------
# @ 触发：不阻塞发消息，且诚实交代结果
# ---------------------------------------------------------------------------
def test_mention_returns_immediately_and_replies_in_the_background(
    client: TestClient, headers: dict, session_maker
):
    """发消息请求本身不等模型；后台要么给出真实回复，要么给出诚实系统消息。"""
    room = _team_room(session_maker, _owner_id(client, headers))
    r = _post(client, headers, room, content="@编码专家 实现这个函数")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["triggered"] == ["implementer"], body
    assert body["scheduled"] is True

    def second_message():
        got = client.get(f"/api/bus/{room}/messages", headers=headers)
        items = got.json()["items"]
        return items[1] if len(items) >= 2 else None

    second = _wait_for(second_message)
    assert second is not None, "后台回复未到达房间"
    # 要么是被点名成员的真实回复，要么是诚实说明为何无法回应 —— 绝不静默
    assert second["from_identity"] in ("agent:implementer", "system"), second
    if second["from_identity"] == "system":
        assert second["content"].strip(), "系统消息必须说明原因，不能是空的"
