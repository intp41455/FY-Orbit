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

第四道闸门 · 冲突（B7）：``runtime/conflict_guard.py`` 把 Claw 六类冲突
检测接进本主干——

* ``pre_execution``（图启动前）：阻断级抛 ``ConflictDetected``（``contracts``
  子类，码 ``conflict_detected``，409）→ 图不启动、零花费、错误信封正常接管；
  可降级级**真实改写任务体**（丢弃自相矛盾的结论 / 脱敏人设禁用话术 /
  标记无归属动作）并留痕（审计帧 + ``degradations`` + ``conflict.detected`` 事件）。
* ``pre_action``（图内每个执行节点调用模型前，``runtime/graph.py``）：阻断级
  在**网关被调用之前**抛，动作与花费都不发生。

检查点：复用 T6 的持久化检查点（``compile_task_graph`` 默认落盘）——
内核崩溃重启后可从断点续跑。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ..audit import AuditService
from ..identity import Actor
from ..services.claw.conflicts import ConflictDetected
from .conflict_guard import (
    PHASE_PRE_EXECUTION,
    ConflictGuard,
    Degradation,
    get_conflict_guard,
    task_signal,
)
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
    #: 冲突闸门的降级记录（可降级级命中；与 state["degradations"] 同源）
    degradations: list[dict[str, Any]] = field(default_factory=list)

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
        conflict_guard: ConflictGuard | None = None,
    ):
        self.bus = bus if bus is not None else get_default_hook_bus()
        #: 冲突闸门（默认进程级单例：管辖权必须跨请求累积才能拦住 C1 争抢）
        self.conflict_guard = conflict_guard if conflict_guard is not None else get_conflict_guard()
        self.conflict_guard.bind(auditor=audit, actor=actor)
        self._graph = compile_task_graph(
            checkpointer=checkpointer, conflict_guard=self.conflict_guard
        )
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
        """唯一执行入口：前置校验（可阻断）→ 冲突闸门 → graph.stream 逐节点驱动 → 终态事件。"""
        trace: list[dict[str, Any]] = []
        body = dict(state or {})
        task_id = str(body.get("task_id") or f"kernel-{uuid.uuid4().hex[:12]}")
        cfg = config or {"configurable": {"thread_id": f"th-{task_id}"}}
        base = {"task_id": task_id, "thread_id": cfg.get("configurable", {}).get("thread_id", "")}
        owner_id = getattr(self.actor, "owner_id", "") or ""
        degradations: list[Degradation] = []

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

        # ---- 前置 3：conflict.detected（六类冲突闸门；阻断级拒绝执行）----
        try:
            body, degradations = self._conflict_gate(body, task_id=task_id, owner_id=owner_id)
        except ConflictDetected as exc:
            self._emit("conflict.blocked", {**base, **exc.details}, trace)
            self._on_conflict_blocked(exc, task_id, trace, base)
            raise
        if degradations:
            out = self._emit(
                "conflict.detected",
                {**base, "hits": [d.to_public() for d in degradations][:8]},
                trace,
            )
            if out.blocked:
                # 注册在 conflict.detected 上的 hook 可以把「降级执行」升级为整体拒绝
                return self._block(task_id, out.reason, trace, base)

        # ---- 前置 4：route.selected（可阻断；调用方显式指定初始路由时）----
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
        except ConflictDetected as exc:
            # 图内动作闸门拒绝执行：**不降级成 error 结果**，直接上抛让统一错误信封接管。
            self._emit("conflict.blocked", {**base, **exc.details}, trace)
            self._on_conflict_blocked(exc, task_id, trace, base)
            raise
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
        return KernelRunResult(
            state=final, trace=trace,
            degradations=[d.to_public() for d in degradations],
        )

    # ------------------------------------------------------------------
    # 冲突闸门

    def _conflict_gate(
        self, body: dict[str, Any], *, task_id: str, owner_id: str
    ) -> tuple[dict[str, Any], list[Degradation]]:
        """图启动前的冲突闸门：阻断级抛 ``ConflictDetected``；降级级改写任务体。

        改写是**真实生效**的：改完的 ``body`` 才是喂给图的那一份
        （丢弃相反结论、脱敏禁用话术、标记无归属动作），并把记录落进
        ``degradations`` 字段——不是只在旁边记一笔。
        """
        guard = self.conflict_guard
        signal = task_signal(body)
        records = guard.enforce(
            signal, phase=PHASE_PRE_EXECUTION, task_id=task_id, owner_id=owner_id,
            effect="task_downgraded",
        )
        if not records:
            return body, []
        claims = body.get("claims")
        if isinstance(claims, list):
            body["claims"] = guard.drop_contradicting_claims(records, claims)
        role = signal.agent_role
        if role and isinstance(body.get("goal"), str):
            body["goal"] = guard.redact_persona(records, body["goal"], role=role)
        body = guard.mark_unowned(records, body)
        body["degradations"] = [d.to_public() for d in records]
        self._audit(
            "kernel.run_degraded", task_id,
            {"count": len(records),
             "items": [d.to_public() for d in records][:8]},
        )
        return body, records

    def _on_conflict_blocked(
        self, exc: ConflictDetected, task_id: str,
        trace: list[dict[str, Any]], base: dict[str, Any],
    ) -> None:
        """阻断级冲突的留痕：任务显式 blocked，随后由调用方把异常上抛给错误信封。"""
        reason = f"conflict: {exc.message}"
        self._emit("task.blocked", {**base, "reason": reason[:200]}, trace)
        self._audit("kernel.run_blocked", task_id, {"reason": reason[:200],
                                                    **exc.details})

    def _block(
        self, task_id: str, reason: str, trace: list[dict[str, Any]],
        base: dict[str, Any],
    ) -> KernelRunResult:
        self._emit("task.blocked", {**base, "reason": reason[:200]}, trace)
        self._audit("kernel.run_blocked", task_id, {"reason": reason[:200]})
        return KernelRunResult(
            state=dict(base), blocked=True, block_reason=reason[:500], trace=trace
        )
