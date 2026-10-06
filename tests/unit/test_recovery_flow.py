"""T6-E/G5 + T6-G 恢复中心流程测试：扫描 / 策略门 / 续作 / 审计。

覆盖：V3（崩溃重启续作，服务级）、V5（换供应商必须确认，红线 6）、
处置矩阵端点、台账扫描端点、流式片段断点取回（V2/V3 的 API 半边）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Iterator, TypedDict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.resilience_models  # noqa: F401
import find_yourself.db.staging_models  # noqa: F401
from find_yourself.api.app import create_app
from find_yourself.config import Settings
from find_yourself.db.base import Base
from find_yourself.db.models import AuditEvent
from find_yourself.db.resilience_models import InterruptionEvent
from find_yourself.db.staging_models import WorkStash
from find_yourself.runtime.checkpoint_sqlite import SqliteCheckpointer
from find_yourself.runtime.interruption import record_interruption
from find_yourself.services.errors import Conflict
from find_yourself.services.recovery import RecoveryService, playbook_view
from find_yourself.services.stream_persistence import (
    get_segments,
    persist_stream,
)


# ---------------------------------------------------------------------------
# 服务级（复用根 conftest 的 session/audit/owner fixtures）
# ---------------------------------------------------------------------------

def _make_service(session, audit, **overrides) -> RecoveryService:
    base = {
        "model_provider": "openai",
        "model_base_url": "https://api.example",
        "model_name": "m1",
        "checkpoint_db_path": "",
        "owner_id": "owner-1",
        "recovery_autoresume": False,
    }
    base.update(overrides)
    settings = SimpleNamespace(**base)
    return RecoveryService(session, audit, settings=settings)


def test_scan_lists_open_events_with_effective_policy(session, audit, owner):
    record_interruption(session, audit, owner, cls="rate_limited", task_id="t1",
                        thread_id="th-1")
    record_interruption(session, audit, owner, cls="provider_reset", task_id="t2")
    session.commit()
    svc = _make_service(session, audit)
    scan = svc.scan(owner)
    assert scan["open_count"] == 2
    by_task = {i["task_id"]: i for i in scan["items"]}
    assert by_task["t1"]["resume"]["has_checkpoint"] is True
    assert by_task["t2"]["resume"]["policy"] == "confirm"


def test_resume_on_provider_change_requires_confirmation(session, audit, owner):
    """V5：供应商指纹变化 → 即使事件本身是 auto 类也强制升级为 confirm（红线 6）。"""
    event = record_interruption(session, audit, owner, cls="rate_limited",
                                detail="interrupted under old provider",
                                provider_fp="stale-fp")
    session.commit()
    svc = _make_service(session, audit)
    with pytest.raises(Conflict) as excinfo:
        svc.resume(owner, event.id)
    assert excinfo.value.code == "resume_confirmation_required"
    # 显式确认后放行（无 thread/message → 如实报告没有可续作产物）
    out = svc.resume(owner, event.id, confirm_provider_change=True)
    assert out["actions"] == []
    assert "no resumable artifacts" in out["note"]


def test_resume_manual_policy_refused(session, audit, owner):
    event = record_interruption(session, audit, owner, cls="agent_misuse",
                                detail="agent overwrote user file")
    session.commit()
    svc = _make_service(session, audit)
    with pytest.raises(Conflict) as excinfo:
        svc.resume(owner, event.id, confirm_provider_change=True)
    assert excinfo.value.code == "resume_policy_manual"


class _St(TypedDict, total=False):
    x: int
    status: str


_CALLS: list[str] = []


def _n_a(state: _St) -> dict:
    _CALLS.append("a")
    return {"x": state.get("x", 0) + 1}


def _n_b(state: _St) -> dict:
    _CALLS.append("b")
    return {"x": state["x"] + 10, "status": "completed"}


def test_resume_completed_graph_not_reinvoked(session, audit, owner, tmp_path):
    """V3 + 红线 2：检查点显示已完成 → 不再 invoke（零重复计费）。"""
    _CALLS.clear()
    db = str(tmp_path / "ck.db")
    g = StateGraph(_St)
    g.add_node("a", _n_a)
    g.add_node("b", _n_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    app = g.compile(checkpointer=SqliteCheckpointer(db))
    app.invoke({"x": 0}, config={"configurable": {"thread_id": "th-done"}})
    assert _CALLS == ["a", "b"]

    event = record_interruption(session, audit, owner, cls="process_killed",
                                thread_id="th-done")
    session.commit()
    svc = _make_service(session, audit, checkpoint_db_path=db)
    out = svc.resume(owner, event.id)
    assert out["resumed"] is True
    action = out["actions"][0]
    assert action["kind"] == "graph_resume"
    assert action["already_complete"] is True
    assert _CALLS == ["a", "b"]  # 没有任何节点被再次执行


def test_resume_without_checkpoint_keeps_open(session, audit, owner, tmp_path):
    event = record_interruption(session, audit, owner, cls="process_killed",
                                thread_id="th-never-ran")
    session.commit()
    svc = _make_service(session, audit, checkpoint_db_path=str(tmp_path / "nope.db"))
    out = svc.resume(owner, event.id)
    assert out["resumed"] is False
    row = session.get(InterruptionEvent, event.id)
    assert row.status == "open"
    assert "no checkpoint on disk" in row.last_resume_note


def test_playbook_view_shape():
    view = playbook_view()
    assert len(view["classes"]) == 7
    scenes = {c["scene"] for c in view["classes"]}
    assert {"S1", "S2", "S3", "S4", "S5", "S6"} <= scenes


# ---------------------------------------------------------------------------
# 流式落盘（T6-D）：中断 → 片段在盘上 + 台账 + 自动暂存
# ---------------------------------------------------------------------------

def test_persist_stream_break_records_everything(session, audit, owner):
    async def boom():
        yield 'data: {"event":"delta","text":"partial"}\n\n'
        raise ConnectionError("wire cut mid-stream")

    async def drive():
        got: list[str] = []
        with pytest.raises(ConnectionError):
            async for frame in persist_stream(
                boom(), session=session, audit=audit, actor=owner,
                message_id="msg-break", task_id="task-break",
            ):
                got.append(frame)
        return got

    got = asyncio.run(drive())
    assert len(got) == 1  # 已吐出的那一帧已在盘上

    segs = get_segments(session, "msg-break")
    assert len(segs) == 1 and segs[0].seq == 1

    event = session.query(InterruptionEvent).filter(
        InterruptionEvent.message_id == "msg-break").one()
    assert event.interruption_class == "network_lost"
    assert event.status == "open"
    assert event.stash_id

    stash = session.get(WorkStash, event.stash_id)
    assert stash is not None
    assert stash.stash_metadata.get("auto") is True
    assert stash.stash_metadata.get("interruption_class") == "network_lost"
    assert "partial" in stash.content

    frames = session.query(AuditEvent).filter(
        AuditEvent.action.in_(["interruption.recorded", "stash.auto_created"])
    ).all()
    assert len(frames) == 2


def test_persist_stream_clean_run_commits_all(session, audit, owner):
    async def ok_gen():
        for i in range(3):
            yield f'data: {{"i":{i}}}\n\n'

    async def drive():
        got: list[str] = []
        async for frame in persist_stream(
            ok_gen(), session=session, audit=audit, actor=owner,
            message_id="msg-ok", task_id="task-ok",
        ):
            got.append(frame)
        return got

    got = asyncio.run(drive())
    assert len(got) == 3
    segs = get_segments(session, "msg-ok")
    assert [s.seq for s in segs] == [1, 2, 3]
    assert session.query(InterruptionEvent).filter(
        InterruptionEvent.message_id == "msg-ok").count() == 0


# ---------------------------------------------------------------------------
# API 级：恢复中心三端点 + 流式片段取回
# ---------------------------------------------------------------------------

OWNER_EMAIL = "owner-t6@t6.test"
PASSWORD = "T6-recovery-pass-12345"


def _force_loopback(asgi_app):
    async def wrapper(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope["client"] = ("127.0.0.1", 12345)
        return await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    Base.metadata.create_all(eng)
    sm = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    settings = Settings(
        environment="test",
        session_secret="test-session-secret-that-is-long-enough-123456",
        database_url="sqlite://",
        local_token="dev-token-secret-t6",
        public_url="http://127.0.0.1:8000",
        artifacts_path=str(tmp_path / "artifacts"),
        recovery_autoresume=False,  # API 测试不跑启动续作
    )
    app = create_app(session_maker=sm, settings=settings)
    with TestClient(_force_loopback(app)) as c:
        yield c


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    assert client.post(
        "/auth/register",
        json={"email": OWNER_EMAIL, "password": PASSWORD, "consent_accepted": True},
    ).status_code == 200
    r = client.post("/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def test_recovery_playbook_endpoint(client, headers):
    r = client.get("/api/recovery/playbook", headers=headers)
    assert r.status_code == 200, r.text
    classes = r.json()["classes"]
    assert len(classes) == 7
    assert {c["scene"] for c in classes} >= {"S1", "S2", "S3", "S4", "S5", "S6"}


def test_recovery_interruptions_endpoint_empty(client, headers):
    r = client.get("/api/recovery/interruptions", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"open_count": 0, "items": []}


def test_recovery_resume_unknown_id_404(client, headers):
    r = client.post(
        "/api/recovery/interruptions/nope/resume",
        json={"confirm_provider_change": False},
        headers=headers,
    )
    assert r.status_code == 404, r.text


def test_streaming_chat_persists_segments_and_resume_reads_back(client, headers):
    """V2/V3 的 API 半边：整流 → 每帧已落盘；断点取回不重复不丢失。"""
    message_id = "msg-api-1"
    with client.stream(
        "POST", "/api/streaming/chat",
        json={"prompt": "hello resume", "model": "default", "message_id": message_id},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200, resp.read()
        lines = [line for line in resp.iter_lines()]

    # iter_lines 给的是「行」；SSE 帧以空行分隔，重组回「帧」再与落盘片段比对
    frames: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line == "":
            if buf:
                frames.append("\n".join(buf))
                buf = []
        else:
            buf.append(line)
    if buf:
        frames.append("\n".join(buf))
    frames = [f for f in frames if f.strip()]
    assert frames, "stream produced nothing"

    r = client.get(f"/api/streaming/{message_id}/segments", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == len(frames)          # 每一帧都在盘上
    assert data["last_seq"] == len(frames)
    assert [s["seq"] for s in data["segments"]] == list(range(1, len(frames) + 1))
    assert data["segments"][0]["frame"].strip() == frames[0].strip()
    assert "message_id" in data["segments"][0]["frame"]

    # after_seq 断点语义：取回其后的帧
    r2 = client.get(
        f"/api/streaming/{message_id}/segments?after_seq=1", headers=headers)
    assert r2.json()["count"] == len(frames) - 1
