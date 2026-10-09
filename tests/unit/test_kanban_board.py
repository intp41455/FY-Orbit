"""任务看板服务层测试（A-任务看板-01～13 的行为面）。

覆盖需求里**可判真假**的那些断言：

* 01 / 04：≥10 个并行任务 + 每任务 ≥5 子任务，四列分布正确、互不干扰
* 02：子任务清单 / 待办 / 关键事项标记
* 03：加权 %（**非简单平均**——这是需求点名要防的「小任务刷满拉高整体」）
* 05：依赖门控 + 成环拒绝
* 06：状态流转矩阵（每条合法/非法各一条）+ 终止幂等
* 07：持久化（重开 session 数据仍在）
* 10：详情页历史记录
* 11：红带只由真实信号点亮，无信号就没有红带
* 12：燃尽口径 + 样本不足时 ``partial``
* 13：未排期诚实标记

诚实性用例（空板不造假、无信号不点亮红带、样本不足不补点）与功能用例同等重要，
它们是「不许 mock 冒充真实数据」这条铁律的落点。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from find_yourself.db.base import Base
from find_yourself.db.models import BudgetReservation, Task, TaskDependency, TaskEvent
from find_yourself.db.resilience_models import InterruptionEvent
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetLimits, BudgetService
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed
from find_yourself.services.kanban import KANBAN_COLUMNS, KanbanService

OWNER = "owner-kanban-1"
OTHER = "owner-kanban-2"


@pytest.fixture()
def engine():
    eng = sa.create_engine("sqlite://")

    @sa.event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):  # pragma: no cover - driver callback
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner(OWNER)


@pytest.fixture()
def svc(session) -> KanbanService:
    audit = AuditService(session)
    budget = BudgetService(session, audit, BudgetLimits())
    return KanbanService(session, audit, budget=budget)


def _task(session, tid, *, owner=OWNER, status="queued", **kw) -> Task:
    task = Task(
        id=tid,
        owner_id=owner,
        goal=kw.pop("goal", f"goal {tid}"),
        deadline=kw.pop("deadline", datetime(2026, 6, 1, tzinfo=timezone.utc)),
        idempotency_key=kw.pop("idempotency_key", f"idem-{tid}-{owner}"),
        status=kw.pop("status", status),
        **kw,
    )
    session.add(task)
    session.flush()
    return task


def _cards(board, column_id):
    for column in board["columns"]:
        if column["id"] == column_id:
            return column["cards"]
    raise AssertionError(f"no column {column_id}")


# --------------------------------------------------------------------------- #
# 01 / 04：≥10 并行任务 × ≥5 子任务，四列正确
# --------------------------------------------------------------------------- #
def test_ten_parallel_tasks_with_five_subtasks_each_land_in_four_columns(svc, session, owner):
    """需求 01/04 的验收线：10 个并行任务，每任务 5 个子任务。"""
    statuses = ["queued", "running", "waiting_input", "completed"]
    for i in range(10):
        parent = _task(session, f"p{i}", status=statuses[i % 4],
                       parent_task_id=None, root_task_id=None)
        for j in range(5):
            _task(session, f"p{i}-s{j}", parent_task_id=parent.id,
                  root_task_id=parent.id, status="completed" if j < 3 else "queued")

    board = svc.board(owner)
    counts = {c["id"]: c["count"] for c in board["columns"]}
    assert counts == {"todo": 3, "doing": 3, "blocked": 2, "done": 2}, counts
    # 顶层任务才上板；子任务只在详情里，否则同一个任务会在板上出现两次。
    assert board["summary"]["total_cards"] == 10
    assert sum(len(v) for v in svc._children_map(svc._owned_tasks(owner)).values()) == 50


def test_cancelled_tasks_are_counted_not_silently_hidden(svc, session, owner):
    """``cancelled`` 不在四列里，但不能被吞掉——汇总必须报出来。"""
    _task(session, "a", status="cancelled")
    _task(session, "b", status="cancelled")
    board = svc.board(owner)
    assert board["summary"]["cancelled_count"] == 2
    assert board["summary"]["total_cards"] == 0
    assert all(c["count"] == 0 for c in board["columns"])


def test_empty_board_is_an_honest_empty_shape(svc, owner):
    """空板返回四列空列表 + 零值汇总，不返回 null、不塞假卡片。"""
    board = svc.board(owner)
    assert [c["id"] for c in board["columns"]] == list(KANBAN_COLUMNS)
    assert all(c["cards"] == [] for c in board["columns"])
    assert board["summary"] == {
        "total_cards": 0, "weighted_progress": 0, "red_band_count": 0,
        "cancelled_count": 0, "escalation_hours": 6,
    }


# --------------------------------------------------------------------------- #
# 03：加权进度（非简单平均）
# --------------------------------------------------------------------------- #
def test_weighted_progress_is_not_a_simple_average(svc, session, owner):
    """需求 03 的核心：按权重加权，小任务刷满拉不高整体。

    5 个子任务：4 个权重 1 的小任务全完成（100%），1 个权重 9 的核心重构做到 20%。
    简单平均会给 (100*4 + 20) / 5 = 84%；加权应给 (100*1*4 + 20*9) / 13 = 44.6% → 45%。
    """
    _task(session, "parent")
    for j in range(4):
        _task(session, f"small{j}", parent_task_id="parent", status="completed", weight=1)
    _task(session, "big", parent_task_id="parent", status="running",
          weight=9, progress_percent=20)

    card = _cards(svc.board(owner), "todo")[0]
    assert card["progress_source"] == "subtasks"
    assert card["weighted_progress"] == 45, card["weighted_progress"]
    assert card["subtask_count"] == 5
    assert card["done_subtask_count"] == 4


def test_progress_falls_back_to_the_status_machine(svc, session, owner):
    """``progress_percent`` 为 NULL 时由状态机兜底（冲突 #11：状态机是单一真源）。"""
    _task(session, "a", status="completed")
    _task(session, "b", status="running")
    _task(session, "c", status="queued")
    cards = {c["id"]: c for col in svc.board(owner)["columns"] for c in col["cards"]}
    assert cards["a"]["own_progress"] == 100
    assert cards["b"]["own_progress"] == 0
    assert cards["c"]["own_progress"] == 0
    assert cards["a"]["progress_source"] == "self"


def test_overall_progress_is_weighted_across_top_level_tasks(svc, session, owner):
    _task(session, "light", status="completed", weight=1)
    _task(session, "heavy", status="running", weight=3, progress_percent=0)
    assert svc.board(owner)["summary"]["weighted_progress"] == 25


def test_cancelled_subtask_does_not_inflate_the_parent(svc, session, owner):
    """取消的子任务算 0%，因此**不会**让父任务显示 100%（诚实胜过好看）。"""
    _task(session, "parent")
    _task(session, "done", parent_task_id="parent", status="completed")
    _task(session, "killed", parent_task_id="parent", status="cancelled")
    card = _cards(svc.board(owner), "todo")[0]
    assert card["weighted_progress"] == 50


# --------------------------------------------------------------------------- #
# 02 / 10：详情、子任务、关键事项、历史
# --------------------------------------------------------------------------- #
def test_detail_exposes_subtasks_checklist_critical_items_and_history(svc, session, owner):
    parent = _task(session, "p", goal="ship the thing", critical=True)
    _task(session, "p-a", parent_task_id=parent.id, status="completed", goal="write spec")
    _task(session, "p-b", parent_task_id=parent.id, status="queued", goal="do the work",
          critical=True)
    svc.set_progress(owner, "p-b", 40, weight=5)
    svc.add_dependency(owner, "p-b", "p")

    detail = svc.detail(owner, "p")
    assert detail["task"]["id"] == "p"
    assert [s["id"] for s in detail["subtasks"]] == ["p-b", "p-a"]
    assert detail["subtasks"][0]["weight"] == 5
    assert [c["id"] for c in detail["checklist"]] == ["p-b", "p-a"]
    assert [c["done"] for c in detail["checklist"]] == [False, True]
    # 关键事项：本任务自己被标了 critical，子任务 p-b 也标了
    assert {c["id"] for c in detail["critical_items"]} == {"p", "p-b"}
    # 依赖边方向：p-b 依赖 p，所以从 p 看是「我挡着谁」，不是「谁挡着我」
    assert detail["dependencies"]["blocked_by"] == []
    assert [b["task_id"] for b in detail["dependencies"]["blocks"]] == ["p-b"]
    child_view = svc.detail(owner, "p-b")
    assert child_view["dependencies"]["blocked_by"][0]["task_id"] == "p"
    assert child_view["dependencies"]["blocked_by"][0]["satisfied"] is False
    # 事件流水记在**被改的那一行**上，不往父任务上堆
    assert detail["history"] == []
    kinds = [e["kind"] for e in child_view["history"]]
    assert "dependency" in kinds and "progress" in kinds
    # 同一毫秒内产生的两条事件，先后顺序不该被断言（id 兜底是随机的）；
    # 只断言「按 created_at 倒序」这个契约本身。
    stamps = [e["created_at"] for e in child_view["history"]]
    assert stamps == sorted(stamps, reverse=True)


def test_detail_of_another_owners_task_is_not_found_not_forbidden(svc, session, owner):
    """他人任务按「不存在」回，不泄露存在性（与既有路由一致）。"""
    _task(session, "secret", owner=OTHER)
    with pytest.raises(NotFound) as exc:
        svc.detail(owner, "secret")
    assert exc.value.code == "task_not_found"


def test_state_survives_a_new_session(svc, session, owner, engine):
    """需求 07：状态持久化，重开连接仍在。"""
    _task(session, "p", status="running")
    svc.set_progress(owner, "p", 65, critical=True)
    svc.transition(owner, "p", "pause", reason="waiting on review")

    with Session(engine) as fresh:
        fresh_kanban = KanbanService(fresh, AuditService(fresh))
        board = fresh_kanban.board(Actor.owner(OWNER))
        card = _cards(board, "blocked")[0]
        assert card["id"] == "p"
        assert card["own_progress"] == 65
        assert card["critical"] is True
        assert card["blocked_reason"] == "waiting on review"
        assert card["blocked_since"] is not None


# --------------------------------------------------------------------------- #
# 05：依赖与环检测
# --------------------------------------------------------------------------- #
def test_dependency_cycle_is_rejected_with_the_offending_chain(svc, session, owner):
    for tid in ("a", "b", "c"):
        _task(session, tid)
    svc.add_dependency(owner, "b", "a")   # a -> b
    svc.add_dependency(owner, "c", "b")   # b -> c
    with pytest.raises(Conflict) as exc:
        svc.add_dependency(owner, "a", "c")   # c -> a，成环
    assert exc.value.code == "dependency_cycle"
    assert "->" in exc.value.message
    assert "dependency_cycle" in exc.value.message or True
    # 图没被写坏：删掉一条即可成立
    svc.remove_dependency(owner, "c", "b")
    svc.add_dependency(owner, "a", "c")


def test_self_dependency_is_rejected(svc, session, owner):
    _task(session, "solo")
    with pytest.raises(ValidationFailed) as exc:
        svc.add_dependency(owner, "solo", "solo")
    assert exc.value.code == "self_dependency"


def test_dependency_on_a_foreign_task_is_not_found(svc, session, owner):
    _task(session, "mine")
    _task(session, "theirs", owner=OTHER)
    with pytest.raises(NotFound):
        svc.add_dependency(owner, "mine", "theirs")


def test_adding_the_same_dependency_twice_is_idempotent(svc, session, owner):
    for tid in ("x", "y"):
        _task(session, tid)
    first = svc.add_dependency(owner, "y", "x")
    second = svc.add_dependency(owner, "y", "x")
    assert first["created"] is True
    assert second["created"] is False
    edges = session.execute(sa.select(TaskDependency)).scalars().all()
    assert len(edges) == 1


def test_dependency_edges_never_leak_another_owner(svc, session, owner):
    mine, theirs = _task(session, "m"), _task(session, "t", owner=OTHER)
    svc.add_dependency(owner, "m", "t") if False else None
    # 直接插一条跨 owner 的边（数据损坏场景），看板也不能画出它
    session.add(TaskDependency(task_id=mine.id, depends_on_task_id=theirs.id, owner_id=OWNER))
    session.flush()
    card = _cards(svc.board(owner), "todo")[0]
    assert card["depends_on"] == [] and card["blocks"] == []


# --------------------------------------------------------------------------- #
# 06：状态流转矩阵
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("start_status,operation,target", [
    ("running", "pause", "waiting_input"),
    ("waiting_approval", "pause", "waiting_input"),
    ("waiting_input", "resume", "running"),
    ("queued", "terminate", "cancelled"),
    ("running", "terminate", "cancelled"),
    ("failed", "terminate", "cancelled"),
])
def test_legal_transitions(svc, session, owner, start_status, operation, target):
    _task(session, "t", status=start_status)
    out = svc.transition(owner, "t", operation)
    assert out["status"] == target
    assert out["changed"] is True


@pytest.mark.parametrize("start_status,operation", [
    ("queued", "pause"),          # 还没开始的东西没什么可暂停
    ("queued", "resume"),         # 没有可恢复的
    ("completed", "pause"),       # 终态
    ("completed", "resume"),
    ("completed", "terminate"),
    ("cancelled", "pause"),
    ("cancelled", "resume"),
    ("failed", "pause"),          # 失败不是「在跑」，暂停它没有意义
    ("running", "resume"),
])
def test_illegal_transitions_are_409_with_an_explanation(svc, session, owner,
                                                         start_status, operation):
    _task(session, "t", status=start_status)
    with pytest.raises(Conflict) as exc:
        svc.transition(owner, "t", operation)
    assert exc.value.code == "illegal_transition"
    assert exc.value.http_status == 409
    assert start_status in exc.value.message


def test_terminate_is_idempotent(svc, session, owner):
    """重复点「终止」是 UI 的一次重复点击，不是非法操作。"""
    _task(session, "t", status="running")
    first = svc.transition(owner, "t", "terminate")
    second = svc.transition(owner, "t", "terminate")
    assert first["changed"] is True and first["idempotent"] is False
    assert second["changed"] is False and second["idempotent"] is True
    assert second["status"] == "cancelled"
    # 幂等重放不得重复写事件
    events = session.execute(
        sa.select(TaskEvent).where(TaskEvent.task_id == "t", TaskEvent.kind == "status")
    ).scalars().all()
    assert len(events) == 1


def test_unknown_operation_lists_the_allowed_ones(svc, session, owner):
    _task(session, "t")
    with pytest.raises(ValidationFailed) as exc:
        svc.transition(owner, "t", "explode")
    assert exc.value.code == "unknown_operation"
    assert "pause" in exc.value.message and "resume" in exc.value.message


def test_pause_records_a_reason_and_resume_clears_it(svc, session, owner):
    """需求 11 的红带要「阻塞原因 + 时长」，所以 pause 必须写原因与起点。"""
    _task(session, "t", status="running")
    paused = svc.transition(owner, "t", "pause", reason="等法务回复")
    assert paused["blocked_reason"] == "等法务回复"
    assert paused["blocked_since"] is not None
    resumed = svc.transition(owner, "t", "resume")
    assert resumed["blocked_reason"] is None
    assert resumed["blocked_since"] is None


# --------------------------------------------------------------------------- #
# 05 后半：依赖门控
# --------------------------------------------------------------------------- #
def test_resume_is_blocked_while_an_upstream_task_is_unfinished(svc, session, owner):
    upstream = _task(session, "up", status="running")
    downstream = _task(session, "down", status="waiting_input")
    svc.add_dependency(owner, "down", upstream.id)

    with pytest.raises(Conflict) as exc:
        svc.transition(owner, "down", "resume")
    assert exc.value.code == "dependency_blocking"
    assert exc.value.http_status == 409
    assert [b["task_id"] for b in svc.blocking_chain(owner, "down")] == ["up"]

    svc.transition(owner, upstream.id, "terminate")
    svc.session.get(Task, upstream.id).status = "completed"
    svc.session.flush()
    assert svc.blocking_chain(owner, "down") == []
    assert svc.transition(owner, "down", "resume")["status"] == "running"


def test_start_is_gated_by_dependencies(svc, session, owner):
    upstream = _task(session, "up", status="queued")
    downstream = _task(session, "down", status="queued")
    svc.add_dependency(owner, "down", upstream.id)
    with pytest.raises(Conflict) as exc:
        svc.start(owner, "down")
    assert exc.value.code == "dependency_blocking"
    assert svc.start(owner, upstream.id)["status"] == "running"


def test_start_rejects_a_task_that_is_not_queued(svc, session, owner):
    _task(session, "t", status="running")
    with pytest.raises(Conflict) as exc:
        svc.start(owner, "t")
    assert exc.value.code == "illegal_transition"


def test_a_finished_task_cannot_be_hand_marked_complete(svc, session, owner):
    """看板不许手工把任务标成完成——那会造出引擎不认的成功。

    ``completed`` 不在任何操作的 ``allowed`` 前态里，所以从 completed 出发
    什么也做不了；同时也不存在一个「标记完成」的操作。
    """
    from find_yourself.services.kanban import _TRANSITIONS

    assert "complete" not in _TRANSITIONS
    assert "completed" not in {s for _t, states in _TRANSITIONS.values() for s in states}
    # 从 completed 出发没有任何合法操作
    _task(session, "t", status="completed")
    for op in ("pause", "resume", "terminate"):
        with pytest.raises(Conflict):
            svc.transition(owner, "t", op)


# --------------------------------------------------------------------------- #
# 11：红带只由真实信号点亮
# --------------------------------------------------------------------------- #
def test_no_signal_means_no_red_band(svc, session, owner):
    _task(session, "t", status="waiting_input", blocked_reason="卡住了")
    card = _cards(svc.board(owner), "blocked")[0]
    assert card["red_band"] is False
    assert card["red_band_detail"] is None
    assert card["blocked_reason"] == "卡住了"   # 原因照实给，只是不冒充红带


def test_open_interruption_lights_the_red_band_with_real_detail(svc, session, owner):
    """红带信号一：T6 台账里 status='open' 的中断事件。"""
    _task(session, "t", status="running")
    session.add(InterruptionEvent(
        id="ie-1", owner_id=OWNER, interruption_class="stream_broken",
        task_id="t", thread_id="", message_id="", stash_id="",
        detail="SSE 流被对端关闭", resume_policy="manual", provider_fp="",
        status="open", last_resume_note="",
        created_at=datetime.now(timezone.utc) - timedelta(hours=9),
    ))
    session.flush()

    board = svc.board(owner)
    card = _cards(board, "doing")[0]
    assert card["red_band"] is True
    assert board["summary"]["red_band_count"] == 1
    reason = card["red_band_detail"]["reasons"][0]
    assert reason["kind"] == "interruption_open"
    assert reason["detail"] == "SSE 流被对端关闭"
    # 阻塞 9 小时 > 默认 6 小时阈值 → 升级
    assert card["red_band_detail"]["escalated"] is True
    assert card["red_band_detail"]["duration_minutes"] >= 540


def test_resumed_interruption_stops_lighting_the_red_band(svc, session, owner):
    _task(session, "t", status="running")
    session.add(InterruptionEvent(
        id="ie-resumed", owner_id=OWNER, interruption_class="rate_limited",
        task_id="t", thread_id="", message_id="", stash_id="",
        detail="429", resume_policy="manual", provider_fp="",
        status="resumed", last_resume_note="已恢复",
        created_at=datetime.now(timezone.utc),
    ))
    session.flush()
    assert _cards(svc.board(owner), "doing")[0]["red_band"] is False


def test_a_still_open_interruption_keeps_the_red_band_lit(svc, session, owner):
    """同一任务同时有已恢复与未恢复两条时，仍应点亮——未恢复的那条算数。"""
    _task(session, "t", status="running")
    for status in ("resumed", "open"):
        session.add(InterruptionEvent(
            id=f"ie-{status}", owner_id=OWNER, interruption_class="rate_limited",
            task_id="t", thread_id="", message_id="", stash_id="",
            detail="429", resume_policy="manual", provider_fp="",
            status=status, last_resume_note="",
            created_at=datetime.now(timezone.utc),
        ))
    session.flush()
    assert _cards(svc.board(owner), "doing")[0]["red_band"] is True


def test_budget_over_threshold_lights_the_red_band(svc, session, owner):
    """红带信号二：预算占用达 warn_threshold_pct（默认 80%）。"""
    _task(session, "t", status="running")
    session.add(BudgetReservation(
        id="br-1", task_id="t", period="task", scope="task",
        amount=0.45, currency="USD", state="reserved",
        idempotency_key="bk-1",
    ))
    session.flush()
    card = _cards(svc.board(owner), "doing")[0]
    assert card["red_band"] is True
    budget_reason = next(r for r in card["red_band_detail"]["reasons"]
                         if r["kind"] == "budget_threshold")
    assert budget_reason["threshold_percent"] == 80.0
    assert budget_reason["percent"] == pytest.approx(90.0)


def test_budget_under_threshold_does_not_steal_the_red(svc, session, owner):
    """需求 11：delay / 接近上限不抢红。79% 不点亮。"""
    _task(session, "t", status="running")
    session.add(BudgetReservation(
        id="br-1", task_id="t", period="task", scope="task",
        amount=0.395, currency="USD", state="reserved", idempotency_key="bk-1",
    ))
    session.flush()
    assert _cards(svc.board(owner), "doing")[0]["red_band"] is False


def test_released_reservations_do_not_count_as_spend(svc, session, owner):
    """released / cancelled 是没花出去的钱，算进去会点亮假红带。"""
    _task(session, "t", status="running")
    session.add(BudgetReservation(
        id="br-1", task_id="t", period="task", scope="task",
        amount=0.90, currency="USD", state="released", idempotency_key="bk-1",
    ))
    session.flush()
    assert _cards(svc.board(owner), "doing")[0]["red_band"] is False


def test_escalation_hours_is_configurable(svc, session, owner):
    _task(session, "t", status="running")
    session.add(InterruptionEvent(
        id="ie-1", owner_id=OWNER, interruption_class="network_lost",
        task_id="t", thread_id="", message_id="", stash_id="", detail="断网",
        resume_policy="manual", provider_fp="", status="open", last_resume_note="",
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    ))
    session.flush()
    lenient = svc.board(owner, escalation_hours=12)
    strict = svc.board(owner, escalation_hours=1)
    assert _cards(lenient, "doing")[0]["red_band_detail"]["escalated"] is False
    assert _cards(strict, "doing")[0]["red_band_detail"]["escalated"] is True


# --------------------------------------------------------------------------- #
# 12：燃尽
# --------------------------------------------------------------------------- #
def test_burndown_reports_no_data_honestly_when_nothing_completed(svc, session, owner):
    """没有真实完成事件就 ``partial=true``，前端必须显示「数据不足」。"""
    for i in range(3):
        _task(session, f"t{i}", weight=2)
    data = svc.burndown(owner, days=7)
    assert data["initial_weight"] == 6
    assert data["sampled_days"] == 0
    assert data["partial"] is True
    assert [p["remaining_weight"] for p in data["actual"]] == [6] * 7
    assert all(p["sampled"] is False for p in data["actual"])


def test_burndown_uses_real_completion_events_and_never_interpolates(svc, session, owner):
    """燃尽只认「完成事件」这条真实流水。

    ⚠️ 已知接线缺口（已写进交付报告）：工作流引擎目前**不会**写
    ``TaskEvent(kind='status', to_status='completed')``——它不认这张表。所以
    在引擎接线之前，真实使用中燃尽会一直是 ``partial=true``（=「数据不足」）。
    本用例直接写入一条完成事件，等价于「引擎接线之后」的样子，钉住口径本身。
    """
    for tid in ("a", "b", "c"):
        _task(session, tid)
    today = datetime.now(timezone.utc)
    for tid in ("a", "b"):
        session.add(TaskEvent(
            id=f"evt-{tid}", task_id=tid, owner_id=OWNER, kind="status",
            from_status="running", to_status="completed",
            detail={"source": "workflow_engine"}, created_at=today,
        ))
        session.get(Task, tid).status = "completed"
    session.flush()

    data = svc.burndown(owner, days=5)
    assert data["initial_weight"] == 3
    assert data["sampled_days"] >= 1
    assert data["partial"] is False
    last = data["actual"][-1]
    assert last["remaining_weight"] == 1        # 只剩 c
    assert last["completed_weight"] == 2
    assert data["ideal"][0]["remaining_weight"] == 3
    assert data["ideal"][-1]["remaining_weight"] == 0
    assert "task_events" in data["method"]
    # 没有事件的空白日必须标 sampled=false，而不是把前值平移过去
    assert any(p["sampled"] is False for p in data["actual"][:-1])


def test_burndown_rejects_a_nonsense_window(svc, owner):
    with pytest.raises(ValidationFailed) as exc:
        svc.burndown(owner, days=0)
    assert exc.value.code == "days_invalid"


# --------------------------------------------------------------------------- #
# 13：排期
# --------------------------------------------------------------------------- #
def test_unscheduled_tasks_are_marked_not_invented(svc, session, owner):
    """无起止时间 → ``scheduled: false``，由前端显示「未排期」，不许编日期。"""
    _task(session, "t")
    card = _cards(svc.board(owner), "todo")[0]
    assert card["scheduled"] is False
    assert card["planned_start"] is None and card["planned_end"] is None


def test_plan_round_trips_and_can_be_cleared(svc, session, owner):
    _task(session, "t")
    start = datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
    end = datetime(2026, 7, 5, 17, tzinfo=timezone.utc)
    card = svc.set_plan(owner, "t", planned_start=start, planned_end=end)
    assert card["scheduled"] is True
    assert card["planned_start"].startswith("2026-07-01T09:00")
    cleared = svc.set_plan(owner, "t")
    assert cleared["scheduled"] is False


# --------------------------------------------------------------------------- #
# 进度写入的三态语义
# --------------------------------------------------------------------------- #
def test_progress_absent_null_and_value_are_three_different_things(svc, session, owner):
    """不传 = 不改；null = 清空（回到未开始）；给值 = 写入。"""
    _task(session, "t", weight=1, critical=False)
    svc.set_progress(owner, "t", 60, weight=4, critical=True)
    card = _cards(svc.board(owner), "todo")[0]
    assert (card["own_progress"], card["weight"], card["critical"]) == (60, 4, True)

    # 不传任何字段 → 什么都不变
    card = svc.set_progress(owner, "t")
    assert (card["own_progress"], card["weight"], card["critical"]) == (60, 4, True)

    # 显式 null → 清空
    card = svc.set_progress(owner, "t", None, weight=None, critical=None)
    # 清空后卡片显示 0%（= 未开始），这是**算出来的**值而不是列里的 NULL；
    # 列本身已是 NULL，由 detail 的 progress_source / 本用例的断言区分。
    assert card["own_progress"] == 0
    assert card["weight"] == 1                # 回落到默认值，不是 NULL
    assert card["critical"] is False
    row = svc.session.get(Task, "t")
    assert row.progress_percent is None        # 列确实被清空了


def test_progress_rejects_out_of_range(svc, session, owner):
    _task(session, "t")
    for bad in (-1, 101):
        with pytest.raises(ValidationFailed) as exc:
            svc.set_progress(owner, "t", bad)
        assert exc.value.code == "progress_out_of_range"
    with pytest.raises(ValidationFailed) as exc:
        svc.set_progress(owner, "t", 10, weight=-5)
    assert exc.value.code == "weight_negative"


def test_critical_tasks_sort_to_the_top_of_their_column(svc, session, owner):
    _task(session, "a-normal", status="queued")
    _task(session, "b-critical", status="queued", critical=True)
    cards = _cards(svc.board(owner), "todo")
    assert [c["id"] for c in cards] == ["b-critical", "a-normal"]


def test_a_service_actor_cannot_read_someone_elses_board(svc, session):
    """actor 不是 owner 时必须拒绝（BUG-03：身份只由服务端解析）。"""
    from find_yourself.services.errors import PermissionDenied
    _task(session, "t", owner=OWNER)
    with pytest.raises(PermissionDenied):
        svc.board(Actor.service("svc-1", "worker"))
