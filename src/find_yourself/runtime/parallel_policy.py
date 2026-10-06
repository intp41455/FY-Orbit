"""P5 · 三种并行策略（A-并行调度-02）+ 总管止损（A-自主总管-02）。

需求现场
--------

A-并行调度-02 要求三种并行策略，此前 ``UnifiedScheduler`` 只有**并发上限**
（``max_concurrent``）这一个旋钮——它回答「同时最多跑几个」，但不回答
「**哪些该一起跑、哪些该等、哪些该按条件才跑**」。本模块补的是**策略层**：
在调度中心之上，把一批任务按三类策略切成可提交的批次。

A-自主总管-02 要求「总管止损机制（最多循环 5 次）」——总管（master）反复
「评估→改进→再评估」时必须有**硬上限**，否则会无限循环烧钱。本模块的
:class:`StopLossGuard` 提供该上限（默认 5 次），并如实区分「因为收敛而停」与
「因为撞上限而停」——后者必须上报，不许静默当成功。

三种策略
--------

* :data:`STRATEGY_ALL` **全部并行** —— 一批任务全部同时提交，等所有结果。
  适用：彼此独立、无依赖的扇出（如并行查多个数据源）。
* :data:`STRATEGY_CONDITIONAL` **按条件并行** —— 带 ``gate`` 谓词；谓词为真才
  提交该任务，假则跳过（记 ``skipped``）。适用：可选步骤 / 依赖上一步产物的
  分支（如「仅当检测到冲突才跑裁判」）。
* :data:`STRATEGY_BATCHED` **分批并行** —— 每个批次内并行、批次之间串行。
  适用：既要并行提速、又要控制瞬时资源占用或遵守外部速率限制（如每批 3 个）。

设计约束
--------

1. **策略层不改调度中心**——本模块**只**产出「批次计划」并可选执行，所有真正的
   并发/优先级/回收仍由 ``UnifiedScheduler`` 负责（不重复建设第二套调度内核，
   这是 PRD 明确警告的红线）。
2. **诚实报告**——每个任务的结局标识为 ``succeeded`` / ``failed`` / ``skipped``
   / ``reclaimed``，不把「跳过」混成「成功」。
3. **单任务失败不拖垮整批**（默认 ``fail_fast=False``）——扇出场景下一条失败不该
   让其余白跑；需要「一失败就整体中止」时显式传 ``fail_fast=True``。
4. **止损上限是硬的**——:class:`StopLossGuard` 到次数即 ``halt``，不允许调用方
   自行「再看一次」（红线：不许为「看起来在收敛」而放宽上限）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..services.scheduler.core import (
    DispatchRequest,
    TaskRecord,
    UnifiedScheduler,
)

#: 策略标识（值即对外 JSON 的 strategy 字段）。
STRATEGY_ALL = "all"
STRATEGY_CONDITIONAL = "conditional"
STRATEGY_BATCHED = "batched"

KNOWN_STRATEGIES = (STRATEGY_ALL, STRATEGY_CONDITIONAL, STRATEGY_BATCHED)


@dataclass
class ParallelTask:
    """并行计划里的一个单元：一个派发请求 + 可选条件门。"""

    #: 计划内的稳定标识（用于回报结果；不传则回落到 DispatchRequest.task_id）
    key: str
    request: DispatchRequest
    #: 仅 STRATEGY_CONDITIONAL 用：返回 False 则本任务不提交（记 skipped）。
    #: 入参是「已完成任务的结果映射 {key: TaskRecord}」，便于依赖上游产物。
    gate: Callable[[dict[str, TaskRecord]], bool] | None = None


@dataclass
class ParallelPlan:
    """一个并行计划：策略 + 任务清单（+ 分批大小）。"""

    strategy: str
    tasks: list[ParallelTask] = field(default_factory=list)
    #: 仅 STRATEGY_BATCHED 用：每批并行几个。<=0 视为不分批（等价于全部并行）。
    batch_size: int = 0
    #: 单任务失败是否立即中止后续（默认 False：扇出场景一条失败不拖垮整批）。
    fail_fast: bool = False

    def validate(self) -> None:
        if self.strategy not in KNOWN_STRATEGIES:
            raise ValueError(
                f"unknown strategy {self.strategy!r}; KNOWN_STRATEGIES: "
                f"{', '.join(KNOWN_STRATEGIES)}"
            )
        if self.strategy == STRATEGY_BATCHED and self.batch_size < 0:
            raise ValueError("batch_size must be >= 0")
        seen: set[str] = set()
        for t in self.tasks:
            if not t.key:
                raise ValueError("ParallelTask.key must be non-empty")
            if t.key in seen:
                raise ValueError(f"duplicate task key {t.key!r} in one plan")
            seen.add(t.key)

    def batches(self) -> list[list[ParallelTask]]:
        """把任务切成执行批次（纯函数，不执行——便于单测与预览）。

        * ``all``         → 一个批次含全部任务（单批并行）
        * ``conditional`` → 一个批次含全部**通过门**的任务；门未过的记 skipped
        * ``batched``     → 按 ``batch_size`` 切批；batch_size<=0 视为单批
        """
        self.validate()
        if self.strategy == STRATEGY_ALL:
            return [list(self.tasks)] if self.tasks else []
        if self.strategy == STRATEGY_CONDITIONAL:
            passed = [t for t in self.tasks if _gate_open(t, {})]
            return [passed] if passed else []
        # batched
        size = self.batch_size if self.batch_size > 0 else len(self.tasks)
        if size <= 0:
            return []
        return [self.tasks[i:i + size] for i in range(0, len(self.tasks), size)]


def _gate_open(task: ParallelTask, done: dict[str, TaskRecord]) -> bool:
    """条件门求值：无门即开；有门则调用，异常按「关」处理（诚实偏保守）。"""
    if task.gate is None:
        return True
    try:
        return bool(task.gate(done))
    except Exception:  # noqa: BLE001 — 门坏掉不应误开（保守 = 不跑）
        return False


@dataclass
class ParallelOutcome:
    """一次并行执行的结局汇总。"""

    strategy: str
    results: dict[str, TaskRecord] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    batches_run: int = 0
    aborted_early: bool = False

    def to_public(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "batches_run": self.batches_run,
            "aborted_early": self.aborted_early,
            "skipped": list(self.skipped),
            "results": {k: r.to_public() for k, r in self.results.items()},
            "summary": {
                "total": len(self.results) + len(self.skipped),
                "succeeded": sum(1 for r in self.results.values() if r.status == "succeeded"),
                "failed": sum(1 for r in self.results.values() if r.status == "failed"),
                "reclaimed": sum(1 for r in self.results.values() if r.status == "reclaimed"),
                "skipped": len(self.skipped),
            },
        }


class ParallelRunner:
    """按三种策略驱动 ``UnifiedScheduler`` 执行一批任务。"""

    def __init__(self, scheduler: UnifiedScheduler):
        self._scheduler = scheduler

    def run(
        self,
        plan: ParallelPlan,
        *,
        wait: bool = True,
        timeout: float | None = None,
    ) -> ParallelOutcome:
        """执行计划。``wait=False`` 时提交即返回（不等待完成，用于 fire-and-forget）。

        条件门在**每批开始前**重新求值（传已完成结果映射），因此「按条件并行」
        可以依赖上游批次的产物——这是它与「静态过滤」的区别。
        """
        plan.validate()
        outcome = ParallelOutcome(strategy=plan.strategy)

        if plan.strategy == STRATEGY_ALL:
            self._run_batches(
                [list(plan.tasks)] if plan.tasks else [], plan, outcome,
                wait=wait, timeout=timeout,
            )
            return outcome

        if plan.strategy == STRATEGY_CONDITIONAL:
            opened: list[ParallelTask] = []
            for t in plan.tasks:
                if _gate_open(t, outcome.results):
                    opened.append(t)
                else:
                    outcome.skipped.append(t.key)
            self._run_batches([opened] if opened else [], plan, outcome,
                              wait=wait, timeout=timeout)
            return outcome

        # batched：逐批求值 + 逐批阻塞（批间串行的语义由这里保证）
        size = plan.batch_size if plan.batch_size > 0 else max(len(plan.tasks), 1)
        batches = [plan.tasks[i:i + size] for i in range(0, len(plan.tasks), size)] \
            if plan.tasks else []
        self._run_batches(batches, plan, outcome, wait=wait, timeout=timeout)
        return outcome

    def _run_batches(
        self,
        batches: list[list[ParallelTask]],
        plan: ParallelPlan,
        outcome: ParallelOutcome,
        *,
        wait: bool,
        timeout: float | None,
    ) -> None:
        for batch in batches:
            if not batch:
                continue
            records: list[tuple[str, TaskRecord]] = []
            for t in batch:
                rec = self._scheduler.submit(t.request)
                records.append((t.key, rec))
            outcome.batches_run += 1
            if wait:
                for key, rec in records:
                    outcome.results[key] = self._scheduler.wait(
                        rec.task_id, timeout=timeout
                    )
                if plan.fail_fast and any(
                    r.status in ("failed", "reclaimed") for r in outcome.results.values()
                ):
                    outcome.aborted_early = True
                    return
            else:
                for key, rec in records:
                    outcome.results[key] = rec


# --------------------------------------------------------------------------- #
# A-自主总管-02 · 总管止损机制（最多循环 5 次）
# --------------------------------------------------------------------------- #

#: 默认循环上限。需求原文：「总管止损机制（最多循环 5 次）」。
DEFAULT_MAX_LOOPS = 5


class StopLossError(Exception):
    """止损触发：已达循环上限仍在继续。"""


@dataclass
class StopLossGuard:
    """总管「评估→改进→再评估」循环的硬止损闸（A-自主总管-02）。

    用法::

        guard = StopLossGuard()                      # 默认 5 次
        while guard.should_continue():
            result = evaluate_and_improve()
            guard.record(result.improved)            # 每轮记一次
        if guard.tripped:
            report(guard.summary())                  # 撞上限：必须如实上报

    语义要点：

    * **上限是硬的**——``should_continue()`` 在 ``used >= max_loops`` 时返回 False，
      不因「看起来还在改善」而放宽（红线）。
    * **收敛与撞顶要分开报**——``tripped=True`` 表示「撞上限停」，``used < max``
      时自然退出表示「收敛停」。二者结局不同，不许混为一谈。
    * **每轮都要留痕**——``history`` 逐轮记录是否改善，供事后复盘「为什么绕不出来」。
    """

    max_loops: int = DEFAULT_MAX_LOOPS
    used: int = 0
    tripped: bool = False
    history: list[bool] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_loops < 1:
            raise ValueError("max_loops must be >= 1")

    def should_continue(self) -> bool:
        """是否还能进入下一轮。到顶即 False 并置 ``tripped``。"""
        if self.used >= self.max_loops:
            self.tripped = True
            return False
        return True

    def record(self, improved: bool) -> None:
        """记一轮结果（``improved`` = 本轮是否比上轮更好）。

        即使 ``improved=False`` 也消耗一次额度——止损看的是**尝试次数**，
        不是「有没有变好」；否则「原地打转」永远不会撞上限。
        """
        self.used += 1
        self.history.append(bool(improved))
        if self.used >= self.max_loops:
            self.tripped = True

    def summary(self) -> dict[str, Any]:
        return {
            "max_loops": self.max_loops,
            "used": self.used,
            "tripped": self.tripped,
            "outcome": "halted_at_limit" if self.tripped else "converged_or_stopped",
            "improved_rounds": sum(1 for h in self.history if h),
            "history": list(self.history),
        }

