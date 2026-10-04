"""Unit tests: W7 Agent 通信总线 — 房间可见性、身份派生、环形截断、@ 触发。

对应任务书 §4 验收：越权房间 403/404、伪造 from 被忽略、@ 触发真实执行通道、
500 条环形截断、SSE 断线续传、共享上下文引用。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import find_yourself.db.models
import find_yourself.db.team_models
from find_yourself.db.base import Base
from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.runtime.agent_bus import AgentBus, dm_room, identity_key
from find_yourself.runtime.gateway import ModelGateway
from find_yourself.services.actor import Actor
from find_yourself.services.agent_teams import AgentTeamService
from find_yourself.services.bus_service import AgentBusService, identity_of
from find_yourself.services.errors import NotFound, PermissionDenied, ValidationFailed


@pytest.fixture()
def sm():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False, future=True)


@pytest.fixture()
def session(sm):
    s = sm()
    s.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=ON"))
    yield s
    s.close()


def _task(session, owner_id, task_id):
    session.add(Task(
        id=task_id, owner_id=owner_id, root_task_id=task_id, goal="g", domain="work",
        status="running", idempotency_key=f"ik-{task_id}", deadline=utcnow(),
    ))
    session.commit()
    return task_id


def _team(session, owner_id, started=True):
    teams = AgentTeamService(session)
    actor = Actor.owner(owner_id)
    team = teams.create_team(
        actor, name="工程团队", template_id="engineering",
        default_binding={"provider_id": "local-synthetic", "model_id": "mock-deterministic"},
        budget_ref={"root_budget_usd": 0.5, "member_reserve_cap_usd": 0.05},
    )
    session.commit()
    if started:
        teams.start_team(actor, team.id, expected_version=1)
        session.commit()
    return team


class _FakeCallResult:
    """打桩的模型返回（只替换最外层模型调用，执行通道仍是真实链路）。"""

    text = "收到，我来实现这个函数。"
    usage = {"prompt_tokens": 12, "completion_tokens": 9}
    settled_amount = Decimal(0)
    provider_request_id = "mock-req-test"


def _patch_gateway(monkeypatch):
    def fake_complete(self, actor, *, task_id, model, prompt, target_domain="personal",
                      personal_source_ids=None, grants=None, max_tokens=1024,
                      timeout_seconds=30.0):
        return _FakeCallResult()

    monkeypatch.setattr(ModelGateway, "complete", fake_complete)


# ---------------------------------------------------------------------------
# 身份派生
# ---------------------------------------------------------------------------
def test_identity_is_derived_from_actor_never_from_client():
    assert identity_of(Actor.owner("owner-1")) == "owner:owner-1"
    assert identity_of(Actor.service("worker-9", "worker")) == "agent:worker-9"
    # 服务身份没有 service_id 时退到 kind，绝不产生空身份冒充他人
    assert identity_of(Actor.service("", "agent")) == "agent:agent"


def test_owner_cannot_publish_system_messages(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    with pytest.raises(PermissionDenied) as exc:
        svc.send(Actor.owner("o1"), "global", content="hi", kind="system")
    assert exc.value.http_status == 403
    assert exc.value.code == "bus_system_requires_service"


def test_service_identity_may_publish_system_messages(session):
    bus = AgentBus()
    svc = AgentBusService(session, bus=bus, auto_reply=False)
    _task(session, "o1", "task-1")
    actor = Actor.service("worker-1", "worker", task_id="task-1")
    out = svc.send(actor, "task-1", content="构建完成", kind="system")
    assert out["message"]["from_identity"] == "agent:worker-1"
    assert out["message"]["kind"] == "system"


# ---------------------------------------------------------------------------
# 房间可见性
# ---------------------------------------------------------------------------
def test_owner_sees_own_task_room(session):
    bus = AgentBus()
    svc = AgentBusService(session, bus=bus, auto_reply=False)
    _task(session, "o1", "task-1")
    out = svc.send(Actor.owner("o1"), "task-1", content="进度如何？")
    assert out["message"]["room"] == "task-1"
    assert svc.list_messages(Actor.owner("o1"), "task-1")["count"] == 1


def test_other_owners_task_room_is_not_found(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    _task(session, "o1", "task-1")
    with pytest.raises(NotFound):
        svc.send(Actor.owner("o2"), "task-1", content="偷看")


def test_service_bound_to_the_task_may_use_its_room(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    _task(session, "o1", "task-1")
    actor = Actor.service("worker-1", "worker", task_id="task-1")
    assert svc.send(actor, "task-1", content="worker 汇报")["message"]["room"] == "task-1"


def test_service_bound_elsewhere_is_denied(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    _task(session, "o1", "task-1")
    _task(session, "o1", "task-2")
    actor = Actor.service("worker-1", "worker", task_id="task-2")
    with pytest.raises(PermissionDenied) as exc:
        svc.send(actor, "task-1", content="越界")
    assert exc.value.http_status == 403


def test_global_room_is_owner_only(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    assert svc.send(Actor.owner("o1"), "global", content="全体注意")["message"]["room"] == "global"
    with pytest.raises(PermissionDenied):
        svc.send(Actor.service("worker-1", "worker"), "global", content="广播")


def test_dm_room_is_symmetric_and_closed_to_third_parties(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    room = dm_room("owner:o1", "agent:coder")
    a = Actor.owner("o1")
    b = Actor.service("coder", "agent")
    # 「A 找 B」与「B 找 A」落到同一房间
    assert dm_room("agent:coder", "owner:o1") == room
    svc.send(a, room, content="在吗")
    svc.send(b, room, content="在")
    assert svc.list_messages(a, room)["count"] == 2
    assert svc.list_messages(b, room)["count"] == 2
    with pytest.raises(PermissionDenied):
        svc.list_messages(Actor.owner("o2"), room)


def test_unknown_room_is_not_found_not_empty(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    with pytest.raises(NotFound):
        svc.list_messages(Actor.owner("o1"), "room-that-does-not-exist")


# ---------------------------------------------------------------------------
# 消息校验
# ---------------------------------------------------------------------------
def test_overlong_content_is_rejected_not_truncated(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    with pytest.raises(ValidationFailed) as exc:
        svc.send(Actor.owner("o1"), "global", content="x" * 8001)
    assert exc.value.code == "bus_content_too_long"


def test_unknown_message_kind_is_rejected(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    with pytest.raises(ValidationFailed):
        svc.send(Actor.owner("o1"), "global", content="hi", kind="whisper")


def test_dangling_context_ref_is_rejected(session):
    svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
    with pytest.raises(ValidationFailed) as exc:
        svc.send(Actor.owner("o1"), "global", content="看这个", refs=["ctx-does-not-exist"])
    assert exc.value.code == "bus_unknown_context_ref"


# ---------------------------------------------------------------------------
# 环形历史与 SSE 续传
# ---------------------------------------------------------------------------
def test_history_is_a_500_message_ring():
    bus = AgentBus(history_limit=500)
    for i in range(520):
        bus.publish("r", from_identity="owner:o1", kind="text", content=str(i))
    hist = bus.history("r")
    assert len(hist) == 500
    # 最旧的 20 条被丢掉，序号连续递增
    assert hist[0].content == "20"
    assert hist[-1].content == "519"
    assert bus.last_id("r") == 520


def test_after_id_returns_only_newer_messages():
    bus = AgentBus()
    for i in range(3):
        bus.publish("r", from_identity="owner:o1", kind="text", content=str(i))
    assert [m.content for m in bus.history("r", after_id=1)] == ["1", "2"]


async def test_sse_subscribe_replays_after_last_event_id():
    bus = AgentBus()
    bus.publish("r", from_identity="owner:o1", kind="text", content="old")
    bus.publish("r", from_identity="owner:o1", kind="text", content="missed-1")
    bus.publish("r", from_identity="owner:o1", kind="text", content="missed-2")

    seen: list[str] = []

    async def consume():
        async for msg in bus.subscribe("r", after_id=1):
            seen.append(msg.content)
            if len(seen) >= 3:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.publish("r", from_identity="owner:o1", kind="text", content="live")
    await asyncio.wait_for(task, timeout=2)
    # 断线期间的两条被补发，实时消息随后到达
    assert seen == ["missed-1", "missed-2", "live"]


def test_sse_frame_carries_the_id_for_resume():
    bus = AgentBus()
    msg = bus.publish("r", from_identity="owner:o1", kind="text", content="hi")
    frame = msg.to_sse()
    assert frame.startswith(f"id: {msg.id}\n")
    assert "event: message" in frame


# ---------------------------------------------------------------------------
# @ 触发 Agent 真实参与
# ---------------------------------------------------------------------------
async def test_mention_triggers_the_real_execution_channel(sm, monkeypatch):
    _patch_gateway(monkeypatch)
    session = sm()
    try:
        team = _team(session, "o1")
        bus = AgentBus()
        svc = AgentBusService(
            session, bus=bus, session_maker=sm, background=False,
        )
        out = svc.send(
            Actor.owner("o1"), team.id,
            content="@编码专家 请把这个函数实现一下",
        )
        assert out["triggered"] == ["implementer"], out
        assert out["message"]["mention"] == "agent:implementer"
        await svc.drain()
        items = bus.history(team.id)
        assert [m.from_identity for m in items] == ["owner:o1", "agent:implementer"]
        assert items[1].content == _FakeCallResult.text
        assert items[1].mention == "owner:o1"
    finally:
        session.close()


def test_mention_by_role_and_by_title_both_resolve(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
        a = svc.send(Actor.owner("o1"), team.id, content="hi", mention="agent:reviewer")
        b = svc.send(Actor.owner("o1"), team.id, content="hi", mention="编码专家")
        assert a["message"]["mention"] == "agent:reviewer"
        assert b["message"]["mention"] == "agent:implementer"
    finally:
        session.close()


def test_unknown_mention_is_rejected_not_ignored(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
        with pytest.raises(ValidationFailed) as exc:
            svc.send(Actor.owner("o1"), team.id, content="hi", mention="agent:cto")
        assert exc.value.code == "bus_unknown_mention"
    finally:
        session.close()


async def test_mention_in_a_draft_team_room_does_not_trigger_a_model(sm):
    """团队未启动（绑定未冻结）时，触发失败要在房间内留痕，而不是静默。"""
    session = sm()
    try:
        team = _team(session, "o1", started=False)
        bus = AgentBus()
        svc = AgentBusService(session, bus=bus, session_maker=sm, background=False)
        out = svc.send(Actor.owner("o1"), team.id, content="@coordinator 开始吧")
        assert out["triggered"] == ["coordinator"]
        await svc.drain()
        kinds = [(m.kind, m.from_identity) for m in bus.history(team.id)]
        assert ("system", "system") in kinds
        # 绝不出现一个冒充成员发言的回答
        assert not any(m.from_identity == "agent:coordinator" for m in bus.history(team.id))
        system_msg = next(m for m in bus.history(team.id) if m.kind == "system")
        # 明确说清失败原因（团队未启动 → 成员实例/绑定不存在），不静默吞掉
        assert any(
            token in system_msg.content
            for token in ("not_found", "binding_not_frozen", "model_not_configured")
        )
    finally:
        session.close()


async def test_without_model_credentials_the_reply_is_an_honest_system_message(sm):
    """没有模型配置时：回复位是「模型未配置，无法回应」，不伪造模型输出。"""
    session = sm()
    try:
        team = _team(session, "o1")
        bus = AgentBus()
        svc = AgentBusService(session, bus=bus, session_maker=sm, background=False)
        svc.send(Actor.owner("o1"), team.id, content="@implementer 写个函数")
        await svc.drain()
        msgs = bus.history(team.id)
        assert len(msgs) == 2
        assert msgs[1].kind == "system"
        assert msgs[1].from_identity == "system"
        assert "模型未配置，无法回应" in msgs[1].content
        assert "model_not_configured" in msgs[1].content
    finally:
        session.close()


def test_auto_reply_can_be_disabled_for_a_room(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        bus = AgentBus()
        svc = AgentBusService(session, bus=bus, session_maker=sm, auto_reply=False)
        out = svc.send(Actor.owner("o1"), team.id, content="@implementer hi")
        # 仍然解析出成员（前端可据此提示），但不调度模型
        assert out["triggered"] == []
        assert out["message"]["mention"] == "agent:implementer"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# handoff 连线徽标计数
# ---------------------------------------------------------------------------
def test_handoff_counts_are_grouped_by_edge(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        bus = AgentBus()
        svc = AgentBusService(session, bus=bus, auto_reply=False)
        actor = Actor.owner("o1")
        svc.send(actor, team.id, content="交给审查", kind="handoff", mention="agent:reviewer")
        svc.send(actor, team.id, content="再交一次", kind="handoff", mention="agent:reviewer")
        svc.send(actor, team.id, content="普通发言", kind="text", mention="agent:reviewer")
        out = svc.handoffs(actor, team.id)
        assert out["edges"] == {"o1>reviewer": 2}
        assert out["total"] == 2
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 共享上下文
# ---------------------------------------------------------------------------
def test_context_is_registered_then_referenced_by_a_message(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        bus = AgentBus()
        svc = AgentBusService(session, bus=bus, auto_reply=False)
        actor = Actor.owner("o1")
        added = svc.add_context(actor, team.id, [
            {"kind": "file_ref", "title": "需求文档", "ref": "kb://doc/42"},
            {"kind": "text", "title": "约定", "content": "函数必须带类型注解"},
        ])
        session.commit()
        assert added["count"] == 2
        ctx = svc.list_context(actor, team.id)
        assert ctx["count"] == 2
        assert ctx["items"][0]["added_by"] == "owner:o1"

        ref_id = added["items"][0]["id"]
        out = svc.send(actor, team.id, content="按这份文档来", refs=[ref_id])
        assert out["message"]["refs"] == [ref_id]
    finally:
        session.close()


def test_context_entries_are_owner_scoped(sm):
    session = sm()
    try:
        team = _team(session, "o1")
        svc = AgentBusService(session, bus=AgentBus(), auto_reply=False)
        svc.add_context(Actor.owner("o1"), team.id, [{"kind": "text", "content": "私密"}])
        session.commit()
        # 另一个 owner 连房间都进不去（NotFound），更读不到上下文
        with pytest.raises(NotFound):
            svc.list_context(Actor.owner("o2"), team.id)
    finally:
        session.close()


def test_identity_key_strips_the_prefix():
    assert identity_key("agent:implementer") == "implementer"
    assert identity_key("owner:o1") == "o1"
    assert identity_key("system") == "system"
