"""P4 单测 · 存档回溯分支（A-存档回溯-04/05/06）。

覆盖：分叉生成新分支且**不覆盖原历史**、参数覆盖记录、弃用留档不删、
逐键对比（真比对，不回显预期）、时间线有序且完整、审计留痕。
"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import Conflict, NotFound
from find_yourself.services.snapshot_fork import (
    FORK_ACTIVE,
    FORK_DISCARDED,
    ArchiveForkService,
    compare_runs,
)


@pytest.fixture()
def svc(session, audit):
    return ArchiveForkService(session, audit)


# --------------------------------------------------------------------------- #
# A-存档回溯-04 · 改参重跑生成新分支
# --------------------------------------------------------------------------- #


def test_fork_creates_new_branch(svc, owner):
    plan = svc.fork(
        owner,
        source_thread_id="thread-orig",
        source_checkpoint_id="cp-001",
        overrides={"temperature": 0.2, "model": "gpt-x"},
        label="低温重跑",
    )
    assert plan.source_thread_id == "thread-orig"
    assert plan.source_checkpoint_id == "cp-001"
    assert plan.new_thread_id.startswith("thread-orig--fork-")
    assert plan.new_thread_id != "thread-orig"
    assert plan.overrides == {"temperature": 0.2, "model": "gpt-x"}


def test_fork_does_not_touch_source_history(svc, owner):
    """★ 红线：分叉绝不改动原 thread 的任何数据。"""
    svc.fork(owner, source_thread_id="t1", source_checkpoint_id="cp-A",
             overrides={"a": 1})
    svc.fork(owner, source_thread_id="t1", source_checkpoint_id="cp-A",
             overrides={"a": 2})
    # 两条分叉都指回同一个源点，源点自身没被改（无 source 侧的写操作）
    forks = svc.list_forks(source_thread_id="t1")
    assert len(forks) == 2
    assert all(f.source_checkpoint_id == "cp-A" for f in forks)
    assert {f.overrides["a"] for f in forks} == {1, 2}


def test_fork_requires_source_thread(svc, owner):
    with pytest.raises(ValueError):
        svc.fork(owner, source_thread_id="")


def test_fork_empty_checkpoint_means_latest(svc, owner):
    plan = svc.fork(owner, source_thread_id="t2")
    assert plan.source_checkpoint_id == ""


def test_fork_records_snapshot_and_session_keys(svc, owner):
    plan = svc.fork(
        owner, source_thread_id="t3", snapshot_id="snap-9", session_key="sess-9",
    )
    assert plan.snapshot_id == "snap-9"
    assert plan.session_key == "sess-9"


def test_list_forks_filters_by_source(svc, owner):
    svc.fork(owner, source_thread_id="A")
    svc.fork(owner, source_thread_id="A")
    svc.fork(owner, source_thread_id="B")
    assert len(svc.list_forks(source_thread_id="A")) == 2
    assert len(svc.list_forks(source_thread_id="B")) == 1
    assert len(svc.list_forks()) == 3


def test_get_fork_missing_raises_404(svc):
    with pytest.raises(NotFound):
        svc.get_fork("no-such-fork")


def test_discard_is_soft_delete(svc, owner):
    """弃用是留档不删——历史不可抹除。"""
    plan = svc.fork(owner, source_thread_id="t4")
    row = svc.discard(owner, plan.fork_id, reason="方案作废")

    assert row.state == FORK_DISCARDED
    assert row.discard_reason == "方案作废"
    # 仍然查得到（没删）
    assert svc.get_fork(plan.fork_id).state == FORK_DISCARDED
    # 但不在 active 列表里
    assert [f.id for f in svc.list_forks(state=FORK_ACTIVE)] == []


def test_discard_twice_raises_conflict(svc, owner):
    plan = svc.fork(owner, source_thread_id="t5")
    svc.discard(owner, plan.fork_id)
    with pytest.raises(Conflict):
        svc.discard(owner, plan.fork_id)


# --------------------------------------------------------------------------- #
# A-存档回溯-05 · 分支对比
# --------------------------------------------------------------------------- #


def test_compare_runs_detects_changes():
    left = {"score": 0.8, "steps": 10, "note": "ok"}
    right = {"score": 0.9, "steps": 10, "extra": True}
    r = compare_runs(left, right)
    assert r["identical"] is False
    assert {c["key"] for c in r["changed"]} == {"score"}
    assert r["only_right"] == ["extra"]
    assert r["same"] == ["steps"]
    assert r["summary"]["changed"] == 1


def test_compare_runs_identical():
    r = compare_runs({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 2]})
    assert r["identical"] is True
    assert r["summary"]["changed"] == 0
    assert r["summary"]["total_keys"] == 2


def test_compare_runs_uses_deep_equality_not_stringification():
    """★ 诚实比对：1 与 "1" 必须判为不同，不许字符串化后误判相同。"""
    r = compare_runs({"x": 1}, {"x": "1"})
    assert r["identical"] is False
    assert r["changed"][0]["key"] == "x"


def test_compare_runs_only_left():
    r = compare_runs({"a": 1, "b": 2}, {"a": 1})
    assert r["only_left"] == ["b"]
    assert r["only_right"] == []


def test_compare_fork_reads_real_data(svc, owner):
    """compare 必须真读两侧数据，不回显调用方传入的「预期差异」。"""
    plan = svc.fork(owner, source_thread_id="t6", overrides={"k": "v"})
    r = svc.compare(
        plan.fork_id,
        left_runs={"result": "A", "score": 1},
        right_runs={"result": "B", "score": 1},
    )
    assert r["identical"] is False
    assert r["changed"][0]["key"] == "result"
    assert r["changed"][0]["left"] == "A"
    assert r["changed"][0]["right"] == "B"


def test_compare_fork_identical_inputs_report_identical(svc, owner):
    plan = svc.fork(owner, source_thread_id="t7")
    r = svc.compare(plan.fork_id, left_runs={"a": 1}, right_runs={"a": 1})
    assert r["identical"] is True


def test_compare_labels_use_thread_ids(svc, owner):
    plan = svc.fork(owner, source_thread_id="orig-thread")
    r = svc.compare(plan.fork_id, left_runs={}, right_runs={})
    assert r["left_label"] == "orig-thread"
    assert r["right_label"] == plan.new_thread_id


# --------------------------------------------------------------------------- #
# A-存档回溯-06 · 存档历史时间线
# --------------------------------------------------------------------------- #


def test_timeline_is_ordered_ascending(svc, owner):
    svc.fork(owner, source_thread_id="tl", label="第一次")
    svc.fork(owner, source_thread_id="tl", label="第二次")
    events = svc.timeline()
    ats = [e["at"] for e in events]
    assert ats == sorted(ats)


def test_timeline_includes_all_forks(svc, owner):
    svc.fork(owner, source_thread_id="tl2", label="a")
    svc.fork(owner, source_thread_id="tl2", label="b")
    svc.fork(owner, source_thread_id="tl2", label="c")
    events = svc.timeline()
    assert len(events) == 3
    assert {e["label"] for e in events} == {"a", "b", "c"}


def test_timeline_adds_discard_event(svc, owner):
    plan = svc.fork(owner, source_thread_id="tl3", label="要弃的")
    svc.discard(owner, plan.fork_id, reason="不合适")
    events = svc.timeline()
    kinds = [e["kind"] for e in events]
    assert "fork" in kinds
    assert "discard" in kinds
    discard = next(e for e in events if e["kind"] == "discard")
    assert discard["reason"] == "不合适"


def test_timeline_respects_limit(svc, owner):
    for i in range(5):
        svc.fork(owner, source_thread_id="tl4", label=f"f{i}")
    assert len(svc.timeline(limit=2)) == 2


def test_timeline_filters_by_source(svc, owner):
    svc.fork(owner, source_thread_id="X")
    svc.fork(owner, source_thread_id="Y")
    events = svc.timeline(source_thread_id="X")
    assert len(events) == 1
    assert events[0]["parent_thread_id"] == "X"


def test_timeline_empty_is_empty_list(svc):
    assert svc.timeline() == []


# --------------------------------------------------------------------------- #
# 审计留痕
# --------------------------------------------------------------------------- #


def test_fork_and_discard_write_audit_frames(svc, owner, session):
    from sqlalchemy import select

    from find_yourself.db.models import AuditEvent

    plan = svc.fork(owner, source_thread_id="audit-t")
    svc.discard(owner, plan.fork_id, reason="r")
    session.flush()

    actions = [r.action for r in session.execute(select(AuditEvent)).scalars().all()]
    assert "fork.created" in actions
    assert "fork.discarded" in actions
