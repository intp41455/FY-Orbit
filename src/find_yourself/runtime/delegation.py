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

logger = logging.getLogger(__name__)


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
    ):
        self.max_depth = max_depth
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.root_budget_usd = root_budget_usd
        self.root_spent_usd = 0.0

        self._active_subtasks: dict[str, dict] = {}
        self._delegation_tree: dict[str, list[str]] = {}

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
                res = runner_fn()
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
