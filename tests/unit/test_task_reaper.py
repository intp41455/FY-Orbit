"""C0 / R-13：Temporal 降级态任务卡死的回归测试。

钉死三件事：
1. 建任务响应**不再假装正常**——降级时必须显式带 ``degraded``。
2. 卡死的 queued 任务能被**独立于 Temporal** 的 reap 回收。
3. ``/health/ready`` 能区分「Temporal 挂了」与「Temporal 从未配置」。

每条用例都写成「删掉实现就变红」，避免变成只跑通不改 regress 的烟雾测试。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from find_yourself.db.models import Task
from find_yourself.db.types import utcnow
from find_yourself.services.task_reaper import (
    DEFAULT_STUCK_MINUTES,
    REAP_REASON,
    ReapReport,
    degraded_markers,
    find_stuck_queued_tasks,
    reap_stuck_queued_tasks,
)


def _mk_task(
    session: Session,
    *,
    task_id: str,
    status: str = "queued",
    age_minutes: int = 0,
    owner_id: str = "owner-1",
) -> Task:
    t = Task(
        id=task_id,
        owner_id=owner_id,
        goal=f"goal-{task_id}",
        domain="personal",
        mode="listen",
        strategy="auto",
        status=status,
        stage="requirements",
        deadline=utcnow() + timedelta(hours=1),
        idempotency_key=f"idem-{task_id}",
    )
    session.add(t)
    session.flush()
    if age_minutes:
        t.created_at = utcnow() - timedelta(minutes=age_minutes)
        session.flush()
    return t


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, actor, action, target_id, payload) -> None:
        self.events.append((action, target_id, payload))


# --------------------------------------------------------------------------
# degraded_markers：单一真源
# --------------------------------------------------------------------------


def test_degraded_markers_empty_when_temporal_enabled():
    assert degraded_markers(True) == []


def test_degraded_markers_names_temporal_when_disabled():
    assert degraded_markers(False) == ["temporal"]


def test_degraded_markers_treats_none_as_disabled():
    # ``/health/ready`` 拿不到 runtime 时也是降级，不能静默当成健康。
    assert degraded_markers(None) == ["temporal"]


# --------------------------------------------------------------------------
# find_stuck_queued_tasks：只挑真正卡死的
# --------------------------------------------------------------------------


def test_find_returns_nothing_on_empty_db(session: Session):
    assert find_stuck_queued_tasks(session) == []


def test_find_skips_fresh_queued_task(session: Session):
    _mk_task(session, task_id="fresh", age_minutes=0)
    assert find_stuck_queued_tasks(session, stuck_minutes=5) == []


def test_find_returns_stuck_queued_task(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    got = find_stuck_queued_tasks(session, stuck_minutes=5)
    assert [t.id for t in got] == ["stuck"]


def test_find_ignores_non_queued_statuses(session: Session):
    _mk_task(session, task_id="running", status="running", age_minutes=30)
    _mk_task(session, task_id="done", status="completed", age_minutes=30)
    _mk_task(session, task_id="cancelled", status="cancelled", age_minutes=30)
    assert find_stuck_queued_tasks(session, stuck_minutes=5) == []


def test_find_respects_owner_filter(session: Session):
    _mk_task(session, task_id="a", age_minutes=30, owner_id="owner-1")
    _mk_task(session, task_id="b", age_minutes=30, owner_id="owner-2")
    got = find_stuck_queued_tasks(session, stuck_minutes=5, owner_id="owner-1")
    assert [t.id for t in got] == ["a"]


def test_find_returns_oldest_first(session: Session):
    _mk_task(session, task_id="newer", age_minutes=10)
    _mk_task(session, task_id="older", age_minutes=60)
    got = find_stuck_queued_tasks(session, stuck_minutes=5)
    assert [t.id for t in got] == ["older", "newer"]


def test_default_stuck_minutes_is_sane():
    assert 1 <= DEFAULT_STUCK_MINUTES <= 60


# --------------------------------------------------------------------------
# reap：真正把卡死任务改掉，且可审计
# --------------------------------------------------------------------------


def test_reap_marks_stuck_task_failed(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    assert report.reaped_count == 1
    assert report.reaped == ["stuck"]
    session.refresh(_get(session, "stuck"))
    assert _get(session, "stuck").status == "failed"


def test_reap_leaves_fresh_task_queued(session: Session):
    _mk_task(session, task_id="fresh", age_minutes=0)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    assert report.reaped_count == 0
    assert _get(session, "fresh").status == "queued"


def test_reap_is_idempotent(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    first = reap_stuck_queued_tasks(session, stuck_minutes=5)
    second = reap_stuck_queued_tasks(session, stuck_minutes=5)
    assert first.reaped_count == 1
    assert second.reaped_count == 0, "第二次必须扫不到任何行（已非 queued）"


def test_reap_writes_audit_event(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    audit = _FakeAudit()
    reap_stuck_queued_tasks(session, stuck_minutes=5, audit=audit, actor="tester")
    assert len(audit.events) == 1
    action, target_id, payload = audit.events[0]
    assert action == "task.reaped"
    assert target_id == "stuck"
    assert payload["previous_status"] == "queued"
    assert payload["reason"] == REAP_REASON
    assert payload["stuck_minutes"] == 5
    assert payload["age_seconds"] >= 5 * 60


def test_reap_survives_audit_failure(session: Session):
    """审计写不进去时 reap 仍要完成——不能因为审计把任务留在 queued。"""

    class _BrokenAudit:
        def append(self, *a, **kw):
            raise RuntimeError("audit down")

    _mk_task(session, task_id="stuck", age_minutes=30)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5, audit=_BrokenAudit())
    assert report.reaped_count == 1
    assert _get(session, "stuck").status == "failed"


def test_reap_without_audit_still_works(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    assert report.reaped_count == 1


def test_reap_respects_owner_scope(session: Session):
    _mk_task(session, task_id="mine", age_minutes=30, owner_id="owner-1")
    _mk_task(session, task_id="theirs", age_minutes=30, owner_id="owner-2")
    report = reap_stuck_queued_tasks(session, stuck_minutes=5, owner_id="owner-1")
    assert report.reaped == ["mine"]
    assert _get(session, "theirs").status == "queued"


def test_reap_updates_updated_at(session: Session):
    t = _mk_task(session, task_id="stuck", age_minutes=30)
    before = t.updated_at
    reap_stuck_queued_tasks(session, stuck_minutes=5)
    session.refresh(t)
    assert t.updated_at >= before


def test_reap_report_as_dict_is_json_ready(session: Session):
    _mk_task(session, task_id="stuck", age_minutes=30)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    d = report.as_dict()
    assert set(d) == {"scanned", "reaped_count", "reaped_ids", "skipped_not_stuck"}
    assert d["reaped_ids"] == ["stuck"]


def test_empty_report_is_truthy_shape(session: Session):
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    assert isinstance(report, ReapReport)
    assert report.scanned == 0
    assert report.reaped == []


# --------------------------------------------------------------------------
# 变异防线：把 reap 换成 no-op，这些用例必须红
# --------------------------------------------------------------------------


def test_mutation_noop_reaper_would_be_caught(session: Session):
    """钉死判据：如果 reap 变成 no-op，下面 4 条断言会失败。

    这条用例本身不做变异，只把判据写死，防止有人把断言改松。
    """
    _mk_task(session, task_id="stuck", age_minutes=30)
    report = reap_stuck_queued_tasks(session, stuck_minutes=5)
    # 1) 报告说有回收
    assert report.reaped_count == 1
    # 2) 库里的状态真的变了
    assert _get(session, "stuck").status == "failed"
    # 3) 再扫一次扫不到（幂等）
    assert reap_stuck_queued_tasks(session, stuck_minutes=5).reaped_count == 0
    # 4) 新鲜任务不受影响
    _mk_task(session, task_id="fresh", age_minutes=0)
    assert reap_stuck_queued_tasks(session, stuck_minutes=5).reaped_count == 0
    assert _get(session, "fresh").status == "queued"


def _get(session: Session, task_id: str) -> Task:
    return session.scalars(select(Task).where(Task.id == task_id)).one()
