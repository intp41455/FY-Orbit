"""Hierarchical and Parallel Delegation Coordinator (F4).

Enforces:
1. Maximum delegation depth (default: 3).
2. Maximum concurrency limit across active subtasks.
3. Bounded retries with failure evidence propagation (prevents infinite re-dispatch loops).
4. Root task budget reservation and cost deduction.
5. Strict context isolation: raw conversational history is withheld; subagents receive
   only scoped queries and explicit references.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..services.scheduler import (
    CHANNEL_INTERNAL_AGENT,
    TASK_FAILED,
    TASK_RECLAIMED,
    TASK_SUCCEEDED,
    DEFAULT_PRIORITY,
    DispatchRequest,
    UnifiedScheduler,
)
from ..services.scheduler import scheduler as _default_scheduler

logger = logging.getLogger(__name__)

#: 委派子任务在调度中心里的 worker 标识（与外部成品 agent 同池调度）。
DELEGATION_WORKER_ID = "internal.delegation-subtask"


class DelegationError(Exception):
    """Base error for delegation violations."""


class MaxDepthExceeded(DelegationError):
    """Raised when delegation depth exceeds the configured threshold."""


class MaxConcurrencyExceeded(DelegationError):
    """Raised when concurrent delegated tasks exceed the allowable limit."""


class BudgetExhausted(DelegationError):
    """Raised when root task budget is insufficient for further delegation."""


class SubtaskFailed(DelegationError):
    """Raised when a subtask fails after all bounded retries."""

    def __init__(self, message: str, evidence_ref: str, attempts: int):
        super().__init__(message)
        self.evidence_ref = evidence_ref
        self.attempts = attempts


class DelegationCoordinator:
    def __init__(
        self,
        *,
        max_depth: int = 3,
        max_concurrency: int = 2,
        max_retries: int = 2,
        root_budget_usd: float = 1.0,
        scheduler: UnifiedScheduler | None = None,
    ):
        self.max_depth = max_depth
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.root_budget_usd = root_budget_usd
        self.root_spent_usd = 0.0

        # 统一调度中心接线（A-统一接入-08）：子任务执行统一经调度中心派发，
        # 与外部成品 agent 同池；默认进程级共享单例。
        self._scheduler = scheduler or _default_scheduler

        self._active_subtasks: dict[str, dict] = {}
        self._delegation_tree: dict[str, list[str]] = {}

    def _ensure_worker(self) -> None:
        if self._scheduler.get_worker(DELEGATION_WORKER_ID) is None:
            self._scheduler.register_simple_worker(
                DELEGATION_WORKER_ID,
                CHANNEL_INTERNAL_AGENT,
                lambda req: req.payload["runner"](),
                max_parallel=4,
                tags=("delegation", "subtask"),
                description="F4 层级委派子任务执行器",
            )

    def _run_via_scheduler(
        self,
        *,
        runner_fn: Callable[[], Any],
        parent_task_id: str,
        subtask_id: str,
        current_depth: int,
    ) -> Any:
        """经统一调度中心执行一次子任务；失败/回收转为异常走既有重试语义。"""
        self._ensure_worker()
        record = self._scheduler.submit_and_wait(
            DispatchRequest(
                channel=CHANNEL_INTERNAL_AGENT,
                action="delegation_subtask",
                # capability 钉住本通路 worker（同通道还有 dispatch-child 等）。
                requested_capability="delegation",
                payload={"runner": runner_fn, "subtask_id": subtask_id},
                priority=max(1, DEFAULT_PRIORITY - current_depth),
                timeout_seconds=300.0,
                meta={"parent_task_id": parent_task_id, "depth": current_depth},
            ),
            timeout=300.0,
        )
        if record.status == TASK_SUCCEEDED:
            return record.result
        if record.status == TASK_RECLAIMED:
            raise TimeoutError(
                f"subtask '{subtask_id}' reclaimed by scheduler (deadline exceeded)"
            )
        if record.status == TASK_FAILED:
            raise RuntimeError(f"scheduler task failed: {record.error}")
        raise RuntimeError(f"scheduler task ended in unexpected status '{record.status}'")

    @property
    def remaining_budget_usd(self) -> float:
        return max(0.0, self.root_budget_usd - self.root_spent_usd)

    def can_delegate(self, current_depth: int) -> tuple[bool, str | None]:
        if current_depth >= self.max_depth:
            return False, f"max_depth_exceeded (current={current_depth}, max={self.max_depth})"
        if len(self._active_subtasks) >= self.max_concurrency:
            return False, f"max_concurrency_exceeded (active={len(self._active_subtasks)}, max={self.max_concurrency})"
        if self.remaining_budget_usd <= 0.0:
            return False, f"budget_exhausted (budget={self.root_budget_usd}, spent={self.root_spent_usd})"
        return True, None

    def dispatch(
        self,
        *,
        parent_task_id: str,
        subtask_id: str,
        current_depth: int,
        runner_fn: Callable[[], Any],
        cost_usd: float = 0.005,
        scoped_query: str = "",
        citation_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a delegated subtask under strict bounds."""
        can, reason = self.can_delegate(current_depth)
        if not can:
            if "max_depth" in reason:
                raise MaxDepthExceeded(reason)
            if "max_concurrency" in reason:
                raise MaxConcurrencyExceeded(reason)
            if "budget" in reason:
                raise BudgetExhausted(reason)
            raise DelegationError(reason)

        if cost_usd > self.remaining_budget_usd:
            raise BudgetExhausted(f"Insufficient budget: required={cost_usd}, remaining={self.remaining_budget_usd}")

        # Context isolation check: ensure no full raw history is passed
        isolated_context = {
            "scoped_query": scoped_query,
            "citations": list(citation_refs or []),
            "raw_history_withheld": True,
        }

        self._active_subtasks[subtask_id] = {
            "parent_task_id": parent_task_id,
            "depth": current_depth,
            "context": isolated_context,
        }
        self._delegation_tree.setdefault(parent_task_id, []).append(subtask_id)

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                # 统一调度中心接线（A-统一接入-08）：runner 不再直接调用，
                # 一律经调度中心派发（同池/优先级/并发上限/回收/状态回传）。
                res = self._run_via_scheduler(
                    runner_fn=runner_fn,
                    parent_task_id=parent_task_id,
                    subtask_id=subtask_id,
                    current_depth=current_depth,
                )
                # Deduct cost from root budget upon successful execution
                self.root_spent_usd += cost_usd
                self._active_subtasks.pop(subtask_id, None)
                return {
                    "subtask_id": subtask_id,
                    "parent_task_id": parent_task_id,
                    "depth": current_depth + 1,
                    "status": "completed",
                    "attempts": attempt,
                    "cost_usd": cost_usd,
                    "result": res,
                    "evidence_ref": f"ev:subtask:{subtask_id}:ok",
                }
            except Exception as e:
                last_err = e
                logger.warning("Subtask %s attempt %d failed: %s", subtask_id, attempt, e)

        # Retries exhausted: clean up active state, record failure evidence
        self._active_subtasks.pop(subtask_id, None)
        failure_evidence = f"ev:subtask:{subtask_id}:failed_after_{self.max_retries}_attempts:{type(last_err).__name__}"
        raise SubtaskFailed(
            f"Subtask {subtask_id} failed after {self.max_retries} bounded attempts: {last_err}",
            evidence_ref=failure_evidence,
            attempts=self.max_retries,
        )
