"""P5 单测 · 自主任务认领（A-Agent运行时-11）。

覆盖：发布/查板、**原子认领**（并发只有一个赢家）、租约、心跳续租、结单、
过期回收、诚实返回 None（空板非错误）、审计留痕。
并发正确性一律用「多线程同时敲同一条」验证，不靠 sleep 碰运气。
"""

from __future__ import annotations

import threading
import time

import pytest

from find_yourself.runtime.claim import (
    CLAIM_CLAIMED,
    CLAIM_DONE,
    CLAIM_FAILED,
    CLAIM_PENDING,
    DEFAULT_LEASE_SECONDS,
    TaskBoard,
)
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService


def eventually(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture()
def board(session, audit):
    return TaskBoard(session, audit)


def test_publish_and_list_board(board, owner):
    board.publish(owner, title="任务A", payload={"k": 1}, priority=3)
    board.publish(owner, title="任务B", priority=7)

    all_rows = board.list_board()
    assert [r.title for r in all_rows] == ["任务A", "任务B"]
    assert all_rows[0].state == CLAIM_PENDING
    assert board.pending_count() == 2


def test_publish_rejects_empty_title(board, owner):
    with pytest.raises(ValueError):
        board.publish(owner, title="   ")


def test_claim_next_returns_none_on_empty_board(board, owner):
    """空板是正常态，不是错误——必须返回 None 而非抛异常。"""
    assert board.claim_next(owner) is None


def test_claim_next_respects_priority(board, owner):
    board.publish(owner, title="低优先", priority=9)
    board.publish(owner, title="高优先", priority=1)
    board.publish(owner, title="中优先", priority=5)

    first = board.claim_next(owner)
    assert first is not None
    assert first.title == "高优先"


def test_claim_next_fifo_within_same_priority(board, owner):
    board.publish(owner, title="先发的", priority=5)
    board.publish(owner, title="后发的", priority=5)
    first = board.claim_next(owner)
    assert first is not None and first.title == "先发的"


def test_claim_marks_claimed_and_sets_lease(board, owner):
    board.publish(owner, title="唯一任务")
    result = board.claim_next(owner, lease_seconds=120)
    assert result is not None
    assert result.claimed_by == "owner-1"
    rows = board.list_board(state=CLAIM_CLAIMED)
    assert len(rows) == 1
    assert rows[0].attempt == 1
    assert rows[0].lease_expires_at is not None


def test_claim_is_atomic_under_concurrency(session, audit, owner, tmp_path):
    """★ 并发正确性：多线程同时认领同一条，只有一个赢家。

    用**文件型 SQLite**（非 StaticPool 单连接）模拟「多个 agent 各自一条数据库
    连接」的真实拓扑——StaticPool 会把 8 个线程挤到同一条底层连接上，那验证的是
    连接复用而不是原子认领，测不出真问题。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_file = tmp_path / "claim_concurrency.sqlite"
    eng = create_engine(f"sqlite:///{db_file}", future=True)
    from find_yourself.db.base import Base
    import find_yourself.db.models  # noqa: F401
    import find_yourself.db.claim_models  # noqa: F401
    Base.metadata.create_all(eng)
    sm = sessionmaker(bind=eng, expire_on_commit=False, future=True)

    setup = sm()
    TaskBoard(setup, AuditService(setup)).publish(owner, title="抢手任务")
    setup.commit()
    setup.close()

    winners: list[str] = []
    lock = threading.Lock()
    start = threading.Event()

    def worker(name: str) -> None:
        start.wait()
        s = sm()
        try:
            res = TaskBoard(s, AuditService(s)).claim_next(
                Actor.owner(name), lease_seconds=60
            )
            s.commit()
            if res is not None:
                with lock:
                    winners.append(name)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(f"agent-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=10)

    assert len(winners) == 1, f"原子认领失败：{len(winners)} 个赢家 {winners}"


def test_renew_extends_lease_only_for_holder(board, owner):
    board.publish(owner, title="续租任务")
    res = board.claim_next(owner, lease_seconds=60)
    assert res is not None

    assert board.renew(owner, res.id, lease_seconds=600) is True
    # 非持有者不能续租
    other = Actor.owner("other-agent")
    assert board.renew(other, res.id) is False


def test_renew_rejects_non_claimed(board, owner):
    board.publish(owner, title="未认领")
    rows = board.list_board(state=CLAIM_PENDING)
    assert board.renew(owner, rows[0].id) is False


def test_complete_transitions_to_done(board, owner):
    board.publish(owner, title="结单任务")
    res = board.claim_next(owner)
    assert res is not None
    assert board.complete(owner, res.id, ok=True, note="干完了") is True
    assert len(board.list_board(state=CLAIM_DONE)) == 1


def test_complete_failure_transitions_to_failed(board, owner):
    board.publish(owner, title="失败任务")
    res = board.claim_next(owner)
    assert res is not None
    assert board.complete(owner, res.id, ok=False, note="炸了") is True
    assert len(board.list_board(state=CLAIM_FAILED)) == 1


def test_complete_rejects_non_holder(board, owner):
    board.publish(owner, title="他人任务")
    res = board.claim_next(owner)
    assert res is not None
    assert board.complete(Actor.owner("other"), res.id) is False


def test_reclaim_expired_returns_task_to_board(board, owner, session):
    """租约过期的 claimed 行必须能被放回 pending（持有者崩溃兜底）。"""
    from datetime import timedelta

    from find_yourself.db.types import utcnow

    board.publish(owner, title="会过期")
    res = board.claim_next(owner, lease_seconds=1)
    assert res is not None

    # 手工把租约推到过去（模拟时间流逝，不 sleep）
    from sqlalchemy import select
    from find_yourself.db.claim_models import TaskClaim

    row = session.execute(select(TaskClaim).where(TaskClaim.id == res.id)).scalars().one()
    row.lease_expires_at = utcnow() - timedelta(seconds=10)
    session.flush()

    reclaimed = board.reclaim_expired(owner)
    assert res.id in reclaimed
    assert board.pending_count() == 1
    assert board.list_board(state=CLAIM_PENDING)[0].claimed_by == ""


def test_reclaim_expired_skips_live_leases(board, owner):
    board.publish(owner, title="还活着")
    board.claim_next(owner, lease_seconds=3600)
    assert board.reclaim_expired(owner) == []
    assert len(board.list_board(state=CLAIM_CLAIMED)) == 1


def test_claim_by_capability_filter(board, owner):
    board.publish(owner, title="前端活", payload={"capability": "frontend"})
    board.publish(owner, title="后端活", payload={"capability": "backend"})

    from sqlalchemy import update
    from find_yourself.db.claim_models import TaskClaim

    board._session.execute(
        update(TaskClaim).where(TaskClaim.title == "后端活").values(capability="backend")
    )
    board._session.flush()

    res = board.claim_next(owner, capability="backend")
    assert res is not None
    assert res.title == "后端活"


def test_lease_default_is_positive():
    assert DEFAULT_LEASE_SECONDS > 0


def test_claim_writes_audit_frames(board, owner, session):
    from sqlalchemy import select
    from find_yourself.db.models import AuditEvent

    board.publish(owner, title="留痕任务")
    board.claim_next(owner)
    session.flush()

    actions = [
        r.action for r in session.execute(select(AuditEvent)).scalars().all()
    ]
    assert "claim.published" in actions
    assert "claim.claimed" in actions
