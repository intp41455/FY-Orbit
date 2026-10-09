"""可观测与成本聚合服务测试（A-可观测-02/03 · P2，A-成本仪表盘-02 · P3）。

本文件按「**不许编数据**」这一条主线组织。每条需求先验业务，再验它**拒绝**编造：

* :class:`TestLogsPanel` —— 五维筛选、分面、序号水位
* :class:`TestHonestyNoFabricatedClock` —— 日志时间轴是 ``seq``，``at`` 恒为 None
* :class:`TestTraceJump` —— 关联依据 ``message`` / ``entity`` / ``none`` 三种，
  并断言第三种给的是空数组 + 原因，**不是**空壳
* :class:`TestPerformance` —— 百分位样本门槛；吞吐缺日不补 0
* :class:`TestAgentStats` —— owner 收敛、活动窗口不足样本时为 None
* :class:`TestCostProgress` —— 进度条三段、上界余量 ≠ 预测、步均耗时算法
* :class:`TestOwnerScoping` —— 他人的任务/团队按 404 回

没有迁移需要测试：本模块**不建表**（见模块 docstring）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

import find_yourself.db.canvas_models  # noqa: F401  （注册 canvas_* 到 metadata）
import find_yourself.db.team_models  # noqa: F401  （注册 team_* / agent_instances）
from find_yourself.db.base import Base
from find_yourself.db.canvas_models import CanvasEvent, CanvasInstance
from find_yourself.db.models import BudgetReservation, Task, TaskEvent
from find_yourself.db.team_models import AgentInstance, TeamDefinition, TeamEvent
from find_yourself.db.types import utcnow
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.budget import BudgetLimits, BudgetService
from find_yourself.services.errors import NotFound, ValidationFailed
from find_yourself.services.observability import (
    MIN_SAMPLES_FOR_PERCENTILE,
    ObservabilityService,
)
from find_yourself.services.quality.logs import LogService

OWNER = "owner-1"
OTHER = "owner-2"


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
def audit(session) -> AuditService:
    return AuditService(session)


@pytest.fixture()
def budget(session, audit) -> BudgetService:
    return BudgetService(session, audit, limits=BudgetLimits(per_task_usd=__import__(
        "decimal").Decimal("1.00")))


@pytest.fixture()
def svc(session, audit, budget) -> ObservabilityService:
    return ObservabilityService(
        session, audit=audit, budget=budget, logs=LogService(session, audit=audit)
    )


# --------------------------------------------------------------------------- #
# 造数
# --------------------------------------------------------------------------- #
def _task(
    session: Session,
    task_id: str = "t-1",
    *,
    owner_id: str = OWNER,
    steps: int = 0,
    max_steps: int = 8,
    status: str = "running",
) -> Task:
    row = Task(
        id=task_id,
        owner_id=owner_id,
        goal="ship the thing",
        status=status,
        stage="execution",
        steps=steps,
        max_steps=max_steps,
        # deadline 与 idempotency_key 是 NOT NULL 且无默认值——省略会直接 NOT NULL 失败
        deadline=utcnow() + timedelta(days=1),
        idempotency_key=f"idem-task-{task_id}-{owner_id}",
    )
    session.add(row)
    session.flush()
    return row


def _task_events(session: Session, task_id: str, count: int, *, owner_id: str = OWNER,
                 kind: str = "status", to_status: str = "running",
                 step_ms: int = 1000) -> None:
    """造 ``count`` 条 task_events，相邻间隔 ``step_ms``，从 now 往回排。

    往**回**排是为了让「最后一个事件 = 最近」，与真实追加方向一致。
    """
    base = utcnow()
    for i in range(count):
        at = base - timedelta(milliseconds=step_ms * (count - 1 - i))
        session.add(
            TaskEvent(
                id=f"te-{task_id}-{i}",
                task_id=task_id,
                owner_id=owner_id,
                kind=kind,
                from_status=None,
                to_status=to_status,
                detail=None,
                created_at=at,
            )
        )
    session.flush()


def _reservation(session: Session, task_id: str, amount: str, state: str,
                 owner_id: str = OWNER) -> None:
    session.add(
        BudgetReservation(
            id=f"br-{task_id}-{state}-{amount}",
            task_id=task_id,
            period="task",
            scope="task",
            amount=amount,
            currency="USD",
            state=state,
            idempotency_key=f"idem-{task_id}-{state}-{amount}-{owner_id}",
        )
    )
    session.flush()


def _team(session: Session, team_id: str = "team-1", *, owner_id: str = OWNER) -> TeamDefinition:
    row = TeamDefinition(
        id=team_id,
        owner_id=owner_id,
        name="crew",
        # mode 有 CHECK：只接受 TEAM_MODES（team_models.py:47），不是随便一个词
        mode="product_native",
        members=[],
    )
    session.add(row)
    session.flush()
    return row


def _member(session: Session, team_id: str, role: str = "dev", *,
            owner_id: str = OWNER, steps: int = 0, max_steps: int = 8,
            state: str = "running", parent_task_id: str | None = "t-1") -> AgentInstance:
    row = AgentInstance(
        id=f"ai-{team_id}-{role}",
        team_id=team_id,
        role=role,
        title=f"{role} worker",
        state=state,
        session_id=f"sess-{team_id}-{role}",
        parent_task_id=parent_task_id,
        root_task_id=parent_task_id,
        steps=steps,
        max_steps=max_steps,
    )
    session.add(row)
    session.flush()
    return row


def _member_events(session: Session, member_id: str, count: int, *,
                   team_id: str = "team-1") -> None:
    base = utcnow()
    for i in range(count):
        session.add(
            TeamEvent(
                id=f"evt-{member_id}-{i}",
                team_id=team_id,
                seq=i + 1,
                event_type="member.step",
                agent_instance_id=member_id,
                created_at=base - timedelta(milliseconds=1000 * (count - 1 - i)),
            )
        )
    session.flush()


# --------------------------------------------------------------------------- #
# 需求 A-可观测-02 · 日志面板
# --------------------------------------------------------------------------- #
class TestLogsPanel:
    def test_lists_only_my_frames(self, svc, audit, owner):
        audit.append(owner, "task.created", target="task:t-1")
        audit.append(Actor.service("svc-x", "worker"), "other.frame")
        out = svc.logs_panel(owner)
        assert [i["action"] for i in out["items"]] == ["task.created"]

    def test_reports_the_seq_watermark_for_stream_resume(self, svc, audit, owner):
        audit.append(owner, "task.created", target="task:t-1")
        audit.append(owner, "task.created", target="task:t-2")
        out = svc.logs_panel(owner)
        # 客户端拿它当 SSE 起点，免掉「先开流后查历史」的漏段
        assert out["head_seq"] == out["items"][-1]["seq"]

    def test_facets_count_by_level_and_actor(self, svc, audit, owner):
        audit.append(owner, "task.failed", target="task:t-1")   # error
        audit.append(owner, "chat.question")
        out = svc.logs_panel(owner)
        assert out["facets"]["total"] == 2
        assert out["facets"]["levels"].get("error") == 1
        assert out["facets"]["actors"].get(OWNER) == 2

    def test_unknown_level_is_rejected(self, svc, owner):
        with pytest.raises(ValidationFailed) as exc:
            svc.logs_panel(owner, level="catastrophe")
        assert exc.value.code == "level_unknown"

    def test_filters_reach_the_underlying_log_service(self, svc, audit, owner):
        audit.append(owner, "task.failed", target="task:t-1")
        audit.append(owner, "chat.question")
        assert len(svc.logs_panel(owner, level="error")["items"]) == 1
        assert len(svc.logs_panel(owner, level="change")["items"]) == 1


class TestHonestyNoFabricatedClock:
    def test_time_axis_is_seq_and_at_is_never_a_fake_timestamp(self, svc, audit, owner):
        """🔴 ``audit_events`` 没有时间戳列。给 ``at`` 填一个数就是编造。"""
        audit.append(owner, "task.created", target="task:t-1")
        out = svc.logs_panel(owner)
        assert out["time_axis"] == "seq"
        assert all(i["at"] is None for i in out["items"])

    def test_seq_window_filter_is_inclusive(self, svc, audit, owner):
        audit.append(owner, "task.created", target="task:a")
        audit.append(owner, "task.created", target="task:b")
        audit.append(owner, "task.created", target="task:c")
        head = svc.logs_panel(owner)["head_seq"]
        assert len(svc.logs_panel(owner, since_seq=head - 1)["items"]) == 2


# --------------------------------------------------------------------------- #
# 需求 A-可观测-02 · 错误跳 trace
# --------------------------------------------------------------------------- #
class TestTraceJump:
    def test_correlates_by_message_id(self, svc, audit, owner):
        audit.append(owner, "chat.question", message_id="msg-1")
        audit.append(owner, "chat.answer", message_id="msg-1")
        audit.append(owner, "task.created", message_id="msg-2")

        # head_seq 是**最后**一帧（msg-2）；要问的是 msg-1 那一帧的 seq。
        frames = svc.logs_panel(owner)["items"]
        target_seq = next(f["seq"] for f in frames if f["action"] == "chat.question")
        out = svc.trace_for(owner, target_seq)
        assert out["basis"] == "message"
        assert out["correlation_key"] == "msg-1"
        assert out["total"] == 2
        assert out["reason"] is None

    def test_correlates_by_task_entity_with_real_timestamps(self, svc, session, owner):
        _task(session, "t-1")
        _task_events(session, "t-1", 3)
        audit = svc.audit
        audit.append(owner, "task.failed", target="task:t-1")

        head = svc.logs_panel(owner)["head_seq"]
        out = svc.trace_for(owner, head)
        assert out["basis"] == "entity"
        assert out["entities"]["task_id"] == "t-1"
        assert out["total"] == 3
        # 实体事件有真实墙钟时间——这正是它比审计帧强的地方
        assert all(i["at"] is not None for i in out["items"])

    def test_correlates_by_team_entity(self, svc, session, audit, owner):
        _team(session, "team-1")
        _member(session, "team-1")
        _member_events(session, "ai-team-1-dev", 2)
        audit.append(owner, "team.failed", target="team:team-1")

        head = svc.logs_panel(owner)["head_seq"]
        out = svc.trace_for(owner, head)
        assert out["basis"] == "entity"
        assert out["entities"]["team_id"] == "team-1"
        assert out["total"] == 2

    def test_unlinkable_frame_returns_none_basis_not_an_empty_shell(
        self, svc, audit, owner,
    ):
        """🔴 关联不上就说关联不上。这帧没有 message_id，target 也认不出实体。"""
        audit.append(owner, "budget.alert", target="???")
        head = svc.logs_panel(owner)["head_seq"]

        out = svc.trace_for(owner, head)
        assert out["basis"] == "none"
        assert out["items"] == []
        assert out["total"] == 0
        assert out["reason"] == "no_correlation_key"
        assert out["trace_id"] is None

    def test_unknown_seq_is_404(self, svc, owner):
        with pytest.raises(NotFound) as exc:
            svc.trace_for(owner, 99999)
        assert exc.value.code == "log_frame_not_found"

    def test_another_owners_frame_is_404_not_forbidden(self, svc, audit, owner):
        audit.append(Actor.owner(OTHER), "their.secret", target="task:x")
        with pytest.raises(NotFound):
            svc.trace_for(owner, 1)

    def test_never_presents_a_per_event_trace_id_as_a_correlation(self, svc, session,
                                                                  audit, owner):
        """🔴 ``canvas_events.trace_id`` 是每条事件现生成的，不是关联 id。

        把它当 correlation 返回就是让面板按一个恒不匹配的数字去跳 trace。
        """
        _task(session, "t-1")
        inst = CanvasInstance(id="ci-1", owner_id=OWNER, project_name="c")
        session.add(inst)
        # 先 flush 父行：canvas_events.instance_id 有 FK，但两者之间**没有声明
        # relationship**，SQLAlchemy 不会推断插入顺序，一起 flush 会撞 FK。
        session.flush()
        session.add(
            CanvasEvent(
                id="ce-1", instance_id="ci-1", seq=1, event_type="node.done",
                # task_id 无 FK，可为 None
                agent_id="a1", task_id=None, trace_id="tr-deadbeef",
            )
        )
        session.flush()
        audit.append(owner, "canvas.failed", target="canvas-instance:ci-1")

        head = svc.logs_panel(owner)["head_seq"]
        out = svc.trace_for(owner, head)
        assert out["trace_id"] is None
        # 事件本身带着它，但只是「这一条事件的 trace 标记」
        assert out["items"][0]["event_trace_id"] == "tr-deadbeef"


# --------------------------------------------------------------------------- #
# 需求 A-可观测-03 · 整体性能
# --------------------------------------------------------------------------- #
class TestPerformance:
    def test_percentiles_are_null_below_the_sample_gate(self, svc, session, owner):
        """🔴 两个数据点决定的 p95 是噪声，不是结论。"""
        _task(session, "t-1")
        _task_events(session, "t-1", 2)   # 只有 1 段耗时
        out = svc.performance(owner)
        gate = out["overall"]["stage_duration_ms"]
        assert gate["sample_count"] == 1
        assert gate["p50"] is None and gate["p95"] is None
        assert gate["partial"] is True
        assert gate["reason"]

    def test_percentiles_appear_once_samples_suffice(self, svc, session, owner):
        _task(session, "t-1")
        _task_events(session, "t-1", MIN_SAMPLES_FOR_PERCENTILE + 1)
        out = svc.performance(owner)
        gate = out["overall"]["stage_duration_ms"]
        assert gate["sample_count"] == MIN_SAMPLES_FOR_PERCENTILE
        assert gate["partial"] is False
        assert gate["p50"] == 1000.0
        assert gate["p95"] == 1000.0

    def test_empty_window_reports_zero_samples_not_zero_percentile(self, svc, owner):
        out = svc.performance(owner)
        gate = out["overall"]["combined_duration_ms"]
        assert gate["sample_count"] == 0
        assert gate["p50"] is None and gate["p95"] is None
        assert gate["partial"] is True

    def test_throughput_does_not_pad_missing_days_with_zero(self, svc, session, owner):
        """🔴 缺数据 ≠ 那天零完成。补 0 会让折线讲一个假故事。"""
        _task(session, "t-1")
        _task_events(session, "t-1", 1, to_status="completed")
        out = svc.performance(owner, days=7)["throughput"]
        assert out["sampled_days"] == 1
        assert out["expected_days"] == 7
        assert out["partial"] is True
        assert len(out["actual"]) == 1
        assert "不补 0" in out["missing_note"]

    def test_admits_there_is_no_persisted_latency_table(self, svc, owner):
        out = svc.performance(owner)
        assert "no_persisted_latency_table" in out["honesty"]
        assert "全站平均延迟" in out["honesty"]["no_persisted_latency_table"]

    def test_rejects_out_of_range_window(self, svc, owner):
        for bad in (0, 91):
            with pytest.raises(ValidationFailed) as exc:
                svc.performance(owner, days=bad)
            assert exc.value.code == "days_invalid"

    def test_task_and_agent_counts(self, svc, session, owner):
        _task(session, "t-1", status="running")
        _task(session, "t-2", status="completed")
        _team(session, "team-1")
        _member(session, "team-1", state="running")
        out = svc.performance(owner)
        assert out["tasks"]["by_status"] == {"running": 1, "completed": 1}
        assert out["agents"]["by_state"] == {"running": 1}


# --------------------------------------------------------------------------- #
# 需求 A-可观测-03 · 单 Agent 统计
# --------------------------------------------------------------------------- #
class TestAgentStats:
    def test_lists_members_with_steps_and_budget(self, svc, session, owner):
        _team(session, "team-1")
        _member(session, "team-1", steps=3, max_steps=8)
        out = svc.agent_stats(owner)
        assert out["total"] == 1
        item = out["items"][0]
        assert item["steps"] == 3 and item["max_steps"] == 8
        assert item["step_progress_pct"] == 37.5

    def test_active_span_is_null_below_two_events(self, svc, session, owner):
        """🔴 只有一个时间点就没有「跨度」。返回 0 会说「这个 Agent 一秒都没工作」。"""
        _team(session, "team-1")
        member = _member(session, "team-1")
        _member_events(session, member.id, 1)
        item = svc.agent_stats(owner)["items"][0]
        assert item["event_count"] == 1
        assert item["active_span_ms"] is None
        assert item["active_span_partial"] is True

    def test_active_span_uses_first_and_last_real_event(self, svc, session, owner):
        _team(session, "team-1")
        member = _member(session, "team-1")
        _member_events(session, member.id, 4)   # 间隔 1000ms，共 3 段
        item = svc.agent_stats(owner)["items"][0]
        assert item["active_span_ms"] == 3000.0
        assert item["active_span_partial"] is False
        assert item["active_from"] and item["active_to"]

    def test_never_counts_updated_at_as_work_duration(self, svc, session, owner):
        """``updated_at`` 被任何一次写都刷新，拿它当工作时长是虚的。"""
        _team(session, "team-1")
        member = _member(session, "team-1")
        member.updated_at = utcnow()
        session.flush()
        item = svc.agent_stats(owner)["items"][0]
        assert item["event_count"] == 0
        assert item["active_span_ms"] is None

    def test_scoped_to_the_owner(self, svc, session, owner):
        _team(session, "team-theirs", owner_id=OTHER)
        _member(session, "team-theirs", owner_id=OTHER)
        out = svc.agent_stats(owner)
        assert out["total"] == 0

    def test_team_filter_narrows(self, svc, session, owner):
        _team(session, "team-1")
        _team(session, "team-2")
        _member(session, "team-1", role="a")
        _member(session, "team-2", role="b")
        out = svc.agent_stats(owner, team_id="team-2")
        assert out["total"] == 1
        assert out["items"][0]["role"] == "b"

    def test_rejects_out_of_range_limit(self, svc, owner):
        for bad in (0, 501):
            with pytest.raises(ValidationFailed) as exc:
                svc.agent_stats(owner, limit=bad)
            assert exc.value.code == "limit_invalid"


# --------------------------------------------------------------------------- #
# 需求 A-成本仪表盘-02 · 进度条
# --------------------------------------------------------------------------- #
class TestCostProgress:
    def test_requires_an_explicit_target(self, svc, owner):
        """全局一个百分比没有意义：进度条得有明确的进度载体。"""
        with pytest.raises(ValidationFailed) as exc:
            svc.cost_progress(owner)
        assert exc.value.code == "target_required"

    def test_reports_current_step_total_and_progress(self, svc, session, owner):
        _task(session, "t-1", steps=2, max_steps=8)
        item = svc.cost_progress(owner, task_id="t-1")["items"][0]
        assert item["step_current"] == 2
        assert item["step_total"] == 8
        assert item["step_progress_pct"] == 25.0

    def test_progress_pct_is_null_when_no_step_cap_declared(self, svc, session, owner):
        """🔴 没有 max_steps 是「上限未知」，不是「0% 完成」。"""
        _task(session, "t-1", steps=0, max_steps=0)
        item = svc.cost_progress(owner, task_id="t-1")["items"][0]
        assert item["step_progress_pct"] is None
        assert item["step_progress_partial"] is True

    def test_remaining_upper_bound_is_not_labelled_a_prediction(self, svc, session, owner):
        """🔴 核心诚实性断言：上界余量精确，但它不是预测，字段名与 assumption 都说清。"""
        _task(session, "t-1", steps=2, max_steps=8)
        item = svc.cost_progress(owner, task_id="t-1")["items"][0]
        rem = item["remaining"]
        assert rem["remaining_steps_upper_bound"] == 6
        # 无历史样本 → 时间字段为 null，且 partial
        assert rem["estimated_remaining_ms"] is None
        assert rem["partial"] is True
        assert rem["reason"]

    def test_remaining_time_appears_only_with_enough_history(self, svc, session, owner):
        _task(session, "t-1", steps=4, max_steps=8)
        # N 条事件 = N-1 段耗时；要够门槛就得 N = MIN+1
        n = MIN_SAMPLES_FOR_PERCENTILE + 1
        _task_events(session, "t-1", n, step_ms=1000)
        rem = svc.cost_progress(owner, task_id="t-1")["items"][0]["remaining"]
        assert rem["partial"] is False
        # 已观测 (n-1)=5 段 × 1000ms = 5000ms ÷ 已用 4 步 = 1250ms/步；× 余量 4 步
        assert rem["estimated_remaining_ms"] == pytest.approx(5000.0)
        assert rem["lower_bound_ms"] == 0.0
        # 假设必须**跟着数字一起返回**，否则调用方无从判断这个估计怎么来的
        assert "前提是" in rem["assumption"]
        assert "1250.0" in rem["assumption"]

    def test_per_step_ms_uses_observed_elapsed_not_total_div_max_steps(
        self, svc, session, owner,
    ):
        """🔴 用「总耗时 ÷ max_steps」会把启动开销摊进分母，系统性低估。"""
        _task(session, "t-1", steps=1, max_steps=8)
        # 6 条事件 = 5 段 × 2000ms = 10000ms 已观测；已用 1 步 → 10000ms/步，
        # 而不是 10000/8
        _task_events(session, "t-1", MIN_SAMPLES_FOR_PERCENTILE + 1, step_ms=2000)
        rem = svc.cost_progress(owner, task_id="t-1")["items"][0]["remaining"]
        assert rem["partial"] is False
        # 10000ms/步 × 7 步余量
        assert rem["estimated_remaining_ms"] == pytest.approx(70000.0)
        assert "10000.0" in rem["assumption"] or "10000" in rem["assumption"]

    def test_at_the_step_cap_remaining_is_zero_not_null(self, svc, session, owner):
        _task(session, "t-1", steps=8, max_steps=8)
        rem = svc.cost_progress(owner, task_id="t-1")["items"][0]["remaining"]
        assert rem["remaining_steps_upper_bound"] == 0
        assert rem["estimated_remaining_ms"] == 0
        assert rem["partial"] is False

    def test_team_member_progress_refuses_to_fabricate_step_rate(self, svc, session, owner):
        """成员级事件与 max_steps 不一一对应，折算就是造假。

        这里给 ``parent_task_id=None``，让聚合走「成员级」分支——有 parent_task_id
        时它是一条真任务的进度，走的是有历史可测的那条路。
        """
        _team(session, "team-1")
        _member(session, "team-1", steps=3, max_steps=8, parent_task_id=None)
        item = svc.cost_progress(owner, team_id="team-1")["items"][0]
        assert item["remaining"]["remaining_steps_upper_bound"] == 5
        assert item["remaining"]["estimated_remaining_ms"] is None
        assert item["remaining"]["partial"] is True
        assert "不一一对应" in item["remaining"]["reason"]

    def test_cost_counts_only_live_reservation_states(self, svc, session, owner):
        """🔴 已 release 的额度还回去了，计入就是虚报花费。"""
        _task(session, "t-1")
        _reservation(session, "t-1", "0.25", "reserved")
        _reservation(session, "t-1", "0.50", "released")
        item = svc.cost_progress(owner, task_id="t-1")["items"][0]
        assert item["cost"]["spent_usd"] == 0.25
        assert item["cost"]["reserved_usd"] == 0.25
        assert item["cost"]["limit_usd"] == 1.0
        assert item["cost"]["usage_pct"] == 25.0

    def test_unknown_task_is_404(self, svc, owner):
        with pytest.raises(NotFound) as exc:
            svc.cost_progress(owner, task_id="nope")
        assert exc.value.code == "task_not_found"

    def test_unknown_team_is_404(self, svc, owner):
        with pytest.raises(NotFound) as exc:
            svc.cost_progress(owner, team_id="nope")
        assert exc.value.code == "team_not_found"

    def test_another_owners_task_is_404(self, svc, session, owner):
        _task(session, "t-theirs", owner_id=OTHER)
        with pytest.raises(NotFound):
            svc.cost_progress(owner, task_id="t-theirs")

    def test_another_owners_team_is_404(self, svc, session, owner):
        _team(session, "team-theirs", owner_id=OTHER)
        with pytest.raises(NotFound):
            svc.cost_progress(owner, team_id="team-theirs")
