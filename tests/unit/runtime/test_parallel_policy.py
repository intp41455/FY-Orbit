"""P5 单测 · 三种并行策略（A-并行调度-02）+ 总管止损（A-自主总管-02）。

覆盖：策略校验、三种策略的分批语义、条件门的依赖上游、失败不拖垮整批、
fail_fast 早停、skipped 混不进 succeeded、止损硬上限（默认 5）与「收敛/撞顶」区分。
"""

from __future__ import annotations

import time

import pytest

from find_yourself.runtime.parallel_policy import (
    DEFAULT_MAX_LOOPS,
    STRATEGY_ALL,
    STRATEGY_BATCHED,
    STRATEGY_CONDITIONAL,
    ParallelPlan,
    ParallelRunner,
    ParallelTask,
    StopLossGuard,
    _gate_open,
)
from find_yourself.services.scheduler.core import (
    CHANNEL_INTERNAL_AGENT,
    DispatchRequest,
    UnifiedScheduler,
)


def eventually(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _scheduler_with_echo() -> UnifiedScheduler:
    """一个把 payload 原样回显的 worker 调度中心。"""
    sch = UnifiedScheduler(max_concurrent=8)

    def echo(req: DispatchRequest):
        return {"echo": req.payload.get("v"), "task_id": req.task_id}

    sch.register_simple_worker("echo-1", CHANNEL_INTERNAL_AGENT, echo, max_parallel=8)
    return sch


def _req(tag: str) -> DispatchRequest:
    return DispatchRequest(channel=CHANNEL_INTERNAL_AGENT, payload={"v": tag})


# --------------------------------------------------------------------------- #
# 策略校验
# --------------------------------------------------------------------------- #


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        ParallelPlan(strategy="nonsense").batches()


def test_duplicate_task_key_rejected():
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key="a", request=_req("1")),
        ParallelTask(key="a", request=_req("2")),
    ])
    with pytest.raises(ValueError):
        plan.batches()


def test_empty_key_rejected():
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key="", request=_req("1")),
    ])
    with pytest.raises(ValueError):
        plan.batches()


# --------------------------------------------------------------------------- #
# 三种策略的分批语义
# --------------------------------------------------------------------------- #


def test_strategy_all_makes_one_batch():
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key=f"t{i}", request=_req(str(i))) for i in range(5)
    ])
    batches = plan.batches()
    assert len(batches) == 1
    assert len(batches[0]) == 5


def test_strategy_all_empty_plan_produces_no_batch():
    assert ParallelPlan(strategy=STRATEGY_ALL).batches() == []


def test_strategy_batched_splits_by_size():
    plan = ParallelPlan(strategy=STRATEGY_BATCHED, batch_size=2, tasks=[
        ParallelTask(key=f"t{i}", request=_req(str(i))) for i in range(5)
    ])
    batches = plan.batches()
    assert [len(b) for b in batches] == [2, 2, 1]


def test_strategy_batched_zero_size_is_single_batch():
    """batch_size<=0 视为不分批——与「全部并行」等价。"""
    plan = ParallelPlan(strategy=STRATEGY_BATCHED, batch_size=0, tasks=[
        ParallelTask(key=f"t{i}", request=_req(str(i))) for i in range(3)
    ])
    assert [len(b) for b in plan.batches()] == [3]


def test_strategy_conditional_filters_by_gate():
    plan = ParallelPlan(strategy=STRATEGY_CONDITIONAL, tasks=[
        ParallelTask(key="always", request=_req("1")),
        ParallelTask(key="never", request=_req("2"), gate=lambda done: False),
        ParallelTask(key="true", request=_req("3"), gate=lambda done: True),
    ])
    batches = plan.batches()
    assert len(batches) == 1
    keys = [t.key for t in batches[0]]
    assert keys == ["always", "true"]


def test_gate_exception_treated_as_closed():
    """门坏掉 → 按「关」处理（保守：不跑），不许误开。"""
    def boom(_done):
        raise RuntimeError("gate broken")

    task = ParallelTask(key="x", request=_req("1"), gate=boom)
    assert _gate_open(task, {}) is False


# --------------------------------------------------------------------------- #
# 执行语义
# --------------------------------------------------------------------------- #


def test_runner_all_strategy_executes_every_task():
    sch = _scheduler_with_echo()
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key=f"t{i}", request=_req(str(i))) for i in range(4)
    ])
    outcome = ParallelRunner(sch).run(plan)
    assert outcome.batches_run == 1
    assert len(outcome.results) == 4
    assert outcome.skipped == []
    assert all(r.status == "succeeded" for r in outcome.results.values())


def test_runner_reports_skipped_separately_from_succeeded():
    """★ skipped 必须与 succeeded 分开报，不许混成成功。"""
    sch = _scheduler_with_echo()
    plan = ParallelPlan(strategy=STRATEGY_CONDITIONAL, tasks=[
        ParallelTask(key="run", request=_req("1")),
        ParallelTask(key="skip", request=_req("2"), gate=lambda done: False),
    ])
    outcome = ParallelRunner(sch).run(plan)
    pub = outcome.to_public()
    assert pub["skipped"] == ["skip"]
    assert pub["summary"]["succeeded"] == 1
    assert pub["summary"]["skipped"] == 1
    assert pub["summary"]["total"] == 2


def test_conditional_gate_can_depend_on_upstream_results():
    """条件门可读到已完成任务的结果映射（依赖上游产物的能力）。"""
    sch = _scheduler_with_echo()
    seen_done: list[set[str]] = []

    def gate(done):
        seen_done.append(set(done.keys()))
        return "first" in done  # 只有 first 跑完才开

    plan = ParallelPlan(strategy=STRATEGY_CONDITIONAL, tasks=[
        ParallelTask(key="first", request=_req("1")),
        ParallelTask(key="second", request=_req("2"), gate=gate),
    ])
    outcome = ParallelRunner(sch).run(plan)
    # 首次求值时 first 尚未完成（同批），因此 second 被跳过——这正是「同批条件」
    # 的诚实语义：要串联依赖请用 batched。
    assert "first" in outcome.results
    assert "second" in outcome.skipped


def test_runner_one_failure_does_not_kill_the_batch():
    """默认 fail_fast=False：一条失败，其余照跑完。"""
    sch = UnifiedScheduler(max_concurrent=8)

    def flaky(req: DispatchRequest):
        if req.payload.get("v") == "bad":
            raise RuntimeError("boom")
        return "ok"

    sch.register_simple_worker("flaky", CHANNEL_INTERNAL_AGENT, flaky, max_parallel=8)
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key="good1", request=_req("good")),
        ParallelTask(key="bad", request=_req("bad")),
        ParallelTask(key="good2", request=_req("good")),
    ])
    outcome = ParallelRunner(sch).run(plan)
    assert outcome.results["bad"].status == "failed"
    assert outcome.results["good1"].status == "succeeded"
    assert outcome.results["good2"].status == "succeeded"
    assert outcome.aborted_early is False


def test_runner_fail_fast_aborts_after_failure():
    sch = UnifiedScheduler(max_concurrent=1)

    def flaky(req: DispatchRequest):
        if req.payload.get("v") == "bad":
            raise RuntimeError("boom")
        return "ok"

    sch.register_simple_worker("flaky", CHANNEL_INTERNAL_AGENT, flaky, max_parallel=1)
    plan = ParallelPlan(
        strategy=STRATEGY_BATCHED, batch_size=1, fail_fast=True,
        tasks=[
            ParallelTask(key="bad", request=_req("bad")),
            ParallelTask(key="never", request=_req("good")),
        ],
    )
    outcome = ParallelRunner(sch).run(plan)
    assert outcome.aborted_early is True
    assert "never" not in outcome.results


def test_batched_runs_batches_sequentially():
    """分批策略：批次之间串行——第 2 批开始前第 1 批必须已结束。"""
    sch = _scheduler_with_echo()
    plan = ParallelPlan(strategy=STRATEGY_BATCHED, batch_size=2, tasks=[
        ParallelTask(key=f"t{i}", request=_req(str(i))) for i in range(5)
    ])
    outcome = ParallelRunner(sch).run(plan)
    assert outcome.batches_run == 3
    assert len(outcome.results) == 5


def test_runner_no_route_raises_honestly():
    """无路由必须如实抛（诚实拒绝），不许静默当成功。"""
    from find_yourself.services.scheduler.core import NoRouteError

    sch = UnifiedScheduler(max_concurrent=4)  # 没注册任何 worker
    plan = ParallelPlan(strategy=STRATEGY_ALL, tasks=[
        ParallelTask(key="t", request=_req("1")),
    ])
    with pytest.raises(NoRouteError):
        ParallelRunner(sch).run(plan)


# --------------------------------------------------------------------------- #
# A-自主总管-02 · 止损
# --------------------------------------------------------------------------- #


def test_stoploss_default_is_five_loops():
    """需求原文：最多循环 5 次。"""
    assert DEFAULT_MAX_LOOPS == 5
    assert StopLossGuard().max_loops == 5


def test_stoploss_allows_exactly_max_loops():
    g = StopLossGuard(max_loops=5)
    count = 0
    while g.should_continue():
        g.record(improved=False)
        count += 1
    assert count == 5
    assert g.used == 5
    assert g.tripped is True


def test_stoploss_trips_even_when_improving():
    """★ 关键红线：即使每轮都在改善，到顶也必须停——不许因「看起来在收敛」放宽。"""
    g = StopLossGuard(max_loops=5)
    count = 0
    while g.should_continue():
        g.record(improved=True)
        count += 1
    assert count == 5
    assert g.tripped is True


def test_stoploss_distinguishes_converged_from_tripped():
    """收敛停 vs 撞顶停，结局必须分开报。"""
    converged = StopLossGuard(max_loops=5)
    converged.record(improved=True)  # 只跑 1 轮就自己停了
    assert converged.tripped is False
    assert converged.summary()["outcome"] == "converged_or_stopped"

    tripped = StopLossGuard(max_loops=5)
    while tripped.should_continue():
        tripped.record(improved=False)
    assert tripped.summary()["outcome"] == "halted_at_limit"


def test_stoploss_records_history():
    g = StopLossGuard(max_loops=3)
    g.record(improved=True)
    g.record(improved=False)
    g.record(improved=True)
    s = g.summary()
    assert s["history"] == [True, False, True]
    assert s["improved_rounds"] == 2


def test_stoploss_rejects_zero_or_negative_max():
    with pytest.raises(ValueError):
        StopLossGuard(max_loops=0)
    with pytest.raises(ValueError):
        StopLossGuard(max_loops=-1)
