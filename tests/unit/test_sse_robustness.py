"""Tests: 第 8 批 SSE 健壮性收口（GAP 帧、心跳帧、通知列表分页）。

准入用例：
1. publish > 100 条事件越过 ring buffer (maxlen=100)；
2. 客户端 Last-Event-ID 落后于最小可用 seq（中间事件发生溢出静默丢弃）；
3. 验证必须先下发 event: gap 帧通知客户端全量重拉，而不是静默丢弃。
"""

from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.db.base import Base
import find_yourself.db.models  # noqa: F401
import find_yourself.db.collaboration_models  # noqa: F401
from find_yourself.db.collaboration_models import Notification
from find_yourself.db.types import utcnow
from find_yourself.runtime.sse import TaskEventBus, TaskEvent
from find_yourself.services.actor import Actor
from find_yourself.services.collaboration import (
    CollaborationService,
    NOTIFICATION_DEFAULT_LIMIT,
    NOTIFICATION_MAX_LIMIT,
)


@pytest.mark.asyncio
async def test_reconnect_behind_buffer_minimum_emits_gap():
    """准入测试核心：当 Last-Event-ID 落后于环形缓冲最小 seq 时，必须发送 gap 帧。"""
    bus = TaskEventBus(history=100)
    channel = "collab-notifications:user-overflow"

    # 1. 连续发布 120 条事件（越过 maxlen=100，导致 1..20 淘汰，最小可用 seq 为 21）
    for i in range(1, 121):
        bus.publish(channel, "notification", {"idx": i})

    hist = list(bus._hist[channel])
    assert len(hist) == 100
    min_seq = hist[0].seq
    assert min_seq == 21, f"期望最小 seq 为 21，实际为 {min_seq}"

    # 2. 客户端持落后的 Last-Event-ID = 5 进行重连
    # 期望：必须首先收到 event: gap 帧告知数据丢失需全量重拉
    gen = bus.subscribe(channel, last_event_id=5)
    first_event = await anext(gen)

    assert first_event.event_type == "gap", (
        f"期望首帧为 'gap'，但实际收到了 '{first_event.event_type}' (seq={first_event.seq})，"
        f"发生了静默数据丢失！"
    )
    assert first_event.data.get("gap") is True
    assert first_event.data.get("min_seq") == min_seq


@pytest.mark.asyncio
async def test_idle_stream_emits_heartbeat_comment_frame():
    """空闲连接在达到 heartbeat_interval 时产生心跳注释帧 (: heartbeat\\n\\n)。"""
    bus = TaskEventBus(history=10)
    channel = "collab-notifications:heartbeat-test"

    gen = bus.subscribe(channel, heartbeat_interval=0.05)
    heartbeat_event = await anext(gen)

    assert heartbeat_event.event_type in (": heartbeat", "heartbeat")
    assert heartbeat_event.to_sse() == ": heartbeat\n\n"


def test_list_notifications_pagination_bounded():
    """B2 & 第 8 批：通知列表支持 limit / offset 分页，且受到安全上下限保护。"""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()

    # 为用户 u1 创建 60 条测试通知
    for i in range(60):
        n = Notification(
            id=f"nt-{i:03d}",
            owner_id="u1",
            kind="mention",
            record_kind="memory",
            record_id=f"m-{i}",
            comment_id=f"cm-{i}",
            summary=f"notif {i}",
            created_at=utcnow(),
        )
        session.add(n)
    session.commit()

    actor = Actor.owner("u1")
    svc = CollaborationService(session)

    # 默认分页（默认 50 条）
    p1 = svc.list_notifications(actor)
    assert len(p1) == NOTIFICATION_DEFAULT_LIMIT
    assert p1[0]["id"] == "nt-059"  # 倒序排列

    # offset = 50, limit = 20（取剩余 10 条）
    p2 = svc.list_notifications(actor, limit=20, offset=50)
    assert len(p2) == 10
    assert p2[0]["id"] == "nt-009"

    # 上限越界保护：传 limit=9999 会被自动截断到 NOTIFICATION_MAX_LIMIT (200)
    p3 = svc.list_notifications(actor, limit=9999, offset=0)
    assert len(p3) == 60  # 总共 60 条，不超过 200

    session.close()
