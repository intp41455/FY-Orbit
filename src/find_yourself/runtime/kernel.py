"""A-Agent运行时-01 · 单循环内核（唯一执行主干）。

AC（gap-list）：「有唯一执行主干并接线到产品 API」。

接线前现场：``runtime/graph.py``（LangGraph StateGraph + 4 路由）是孤儿——
src 内只有 ``runtime/evaluation.py`` import 它，而 evaluation 自身无人调用；
产品真正在跑的是 ``services/chat_orchestration.py`` 的标记文本循环。

本模块把三处收敛为**同一主干**：

1. :class:`SingleLoopKernel` 是图执行的**唯一入口**——``graph.stream`` 驱动
   逐节点生命周期事件（Hook 总线）+ 前置可阻断校验 + 审计挂帧；
2. ``runtime/evaluation.py`` 的两处直接 ``app.invoke`` 全部改走内核；
3. 产品 API ``POST /api/runtime/run``（``api/routes/runtime.py``）把内核
   接线到 HTTP 面——图不再是孤儿。

前置阻断语义（确定性）：``task.received`` / ``task.validated`` /
``route.selected`` 三个事件发生在**图启动之前**，hook 阻断 = 图不执行 =
任务显式 ``blocked``。节点级事件为可观测级（见 hooks.py 模块注释）。

检查点：复用 T6 的持久化检查点（``compile_task_graph`` 默认落盘）——
内核崩溃重启后可从断点续跑。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ..services.actor import Actor
from ..services.audit import AuditService
from .graph import compile_task_graph
from .hooks import HookBus, HookOutcome, get_default_hook_bus


@dataclass
class KernelRunResult:
    """一次内核执行的完整结果：终态 + 阻断/错误信息 + hook 轨迹。"""

    state: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""
    error: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str | None:
        return self.state.get("status")


class SingleLoopKernel:
    """所有 LangGraph 图执行的唯一主干。"""

    def __init__(
        self,
        *,
        bus: HookBus | None = None,
        checkpointer: Any | None = None,
        audit: AuditService | None = None,
        actor: Actor | None = None,
    ):
        self.bus = bus if bus is not None else get_default_hook_bus()
        self._graph = compile_task_graph(checkpointer=checkpointer)
        self.audit = audit
        self.actor = actor

    # ------------------------------------------------------------------

    def _emit(
        self,
        event: str,
        payload: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> HookOutcome:
        outcome = self.bus.emit(event, payload)
        trace.append({"event": event, **outcome.summary()})
        return outcome

    def _audit(self, action: str, target: str, details: dict[str, Any]) -> None:
        if self.audit is None:
            return
        actor = self.actor if self.actor is not None else Actor.owner("kernel")
        try:
            self.audit.append(actor, action, target, details)
        except Exception:  # noqa: BLE001 —— 审计失败不炸主干，但 hook 轨迹里可见
            pass

    def run(
        self,
        state: dict[str, Any],
        *,
        config: dict[str, Any] | None = None,
    ) -> KernelRunResult:
        """唯一执行入口：前置校验（可阻断）→ graph.stream 逐节点驱动 → 终态事件。"""
        trace: list[dict[str, Any]] = []
        body = dict(state or {})
        task_id = str(body.get("task_id") or f"kernel-{uuid.uuid4().hex[:12]}")
        cfg = config or {"configurable": {"thread_id": f"th-{task_id}"}}
        base = {"task_id": task_id, "thread_id": cfg.get("configurable", {}).get("thread_id", "")}

        self._audit("kernel.run", task_id, {"goal": str(body.get("goal", ""))[:120]})

        # ---- 前置 1：task.received（可阻断）----
        out = self._emit("task.received", base, trace)
        if out.blocked:
            return self._block(task_id, out.reason, trace, base)

        # ---- 前置 2：task.validated（可阻断；基础校验 fail-closed）----
        goal = str(body.get("goal") or "").strip()
        if not goal:
            reason = "goal is required"
            self._emit("task.failed", {**base, "error": reason}, trace)
            self._audit("kernel.run_failed", task_id, {"error": reason})
            return KernelRunResult(state=body, error=reason, trace=trace)
        out = self._emit(
            "task.validated", {**base, "goal_len": len(goal)}, trace
        )
        if out.blocked:
            return self._block(task_id, out.reason, trace, base)

        # ---- 前置 3：route.selected（可阻断；调用方显式指定初始路由时）----
        initial_route = str(body.get("route") or "").strip()
        if initial_route:
            out = self._emit(
                "route.selected", {**base, "route": initial_route}, trace
            )
            if out.blocked:
                self._emit(
                    "route.blocked", {**base, "route": initial_route,
                                      "reason": out.reason}, trace
                )
                return self._block(task_id, f"route blocked: {out.reason}", trace, base)

        # ---- 主循环：graph.stream 逐节点驱动 ----
        final = dict(body)
        last_node = ""
        try:
            for chunk in self._graph.stream(body, cfg, stream_mode="updates"):
                for node_name, update in chunk.items():
                    if node_name == "__end__":
                        continue
                    last_node = node_name
                    if isinstance(update, dict):
                        final.update(update)
                        new_route = str(update.get("route") or "")
                        if new_route and new_route != initial_route:
                            self._emit(
                                "route.changed",
                                {**base, "route": new_route, "node": node_name},
                                trace,
                            )
                    self._emit(
                        "node.completed",
                        {**base, "node": node_name}, trace,
                    )
        except Exception as exc:  # noqa: BLE001 —— 诚实上报，绝不静默
            error = f"{type(exc).__name__}: {exc}"[:500]
            self._emit(
                "node.failed", {**base, "node": last_node, "error": error}, trace
            )
            self._emit("task.failed", {**base, "error": error}, trace)
            self._audit("kernel.run_failed", task_id, {"error": error, "node": last_node})
            return KernelRunResult(state=final, error=error, trace=trace)

        # ---- 终态 ----
        status = final.get("status")
        if status == "completed":
            self._emit("task.completed", {**base, "status": status}, trace)
            self._audit(
                "kernel.run_completed", task_id,
                {"status": status, "steps": final.get("step_count")},
            )
        else:
            self._emit(
                "task.failed", {**base, "status": status}, trace
            )
            self._audit("kernel.run_failed", task_id, {"status": status})
        return KernelRunResult(state=final, trace=trace)

    def _block(
        self, task_id: str, reason: str, trace: list[dict[str, Any]],
        base: dict[str, Any],
    ) -> KernelRunResult:
        self._emit("task.blocked", {**base, "reason": reason[:200]}, trace)
        self._audit("kernel.run_blocked", task_id, {"reason": reason[:200]})
        return KernelRunResult(
            state=dict(base), blocked=True, block_reason=reason[:500], trace=trace
        )
