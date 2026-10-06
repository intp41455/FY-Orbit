"""A-Agent运行时-07 · 生命周期 Hook 总线（循环外）。

AC（gap-list）：Hook 总线（循环外）+ 事件清单 + 可确定性阻断。

* **事件清单**：``HOOK_EVENTS`` 25 个生命周期事件，权威真源；每个事件标注
  ``wired``（内核/服务已真实 emit）或 ``reserved``（清单先行，emit 挂点随后续
  切片接线）——**绝不虚报已接线**。
* **确定性阻断**：emit 同步、按 priority 升序逐个调用；hook 返回 ``False`` /
  ``{"blocked": True}`` / :class:`HookDecision`(blocked=True) 即阻断并停止后续
  hook——调用方（内核）在**图启动前的前置事件**上遵守阻断，直接拒绝执行。
  节点级事件（``node.completed`` 等）由 ``graph.stream`` 驱动，属**可观测级**
  （节点已在图内执行完毕，阻断不可回溯），内核不在其上阻断——诚实边界。
* **异常隔离**：hook 抛异常不炸主干——记入轨迹继续；``once`` 钩子触发一次即
  注销（无论成败）。

进程内单例（``get_default_hook_bus``）：重启即清空——持久化 hook 注册表是
后续切片（先落清单与阻断语义，不假装已持久化）。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

#: 权威事件清单（25 个，AC 要求 20+）。wired=True 的才允许声称已接线。
HOOK_EVENTS: tuple[str, ...] = (
    # —— 内核前置（可阻断）——
    "task.received",          # wired: kernel.run 收到任务
    "task.validated",         # wired: 基本校验通过、图即将启动
    "route.selected",         # wired: 初始路由已选定（可阻断）
    # —— 内核主干 ——
    "route.blocked",          # wired: route hook 阻断后
    "route.changed",          # wired: 图内 planning 改判路由
    "node.completed",         # wired: 每个图节点完成（可观测级）
    "node.failed",            # wired: 图执行异常时最后所在节点
    "task.blocked",           # wired: 任一前置阻断生效
    "task.completed",         # wired: 图执行完成且 status=completed
    "task.failed",            # wired: 图执行异常/校验失败
    # —— 预留（清单先行，emit 挂点随后续切片接线）——
    "tool.pre", "tool.post",
    "budget.reserved", "budget.settled",
    "checkpoint.saved",
    "hitl.requested", "hitl.resolved",
    "interruption.recorded", "interruption.resume_requested", "interruption.resumed",
    "snapshot.pre_write", "snapshot.restored",
    "stash.auto_created",
    "task.paused", "task.resumed",
)

#: 已真实接线的事件（emit 挂点存在于代码中）——诚实清单的另一半
WIRED_EVENTS: frozenset[str] = frozenset({
    "task.received", "task.validated", "route.selected", "route.blocked",
    "route.changed", "node.completed", "node.failed",
    "task.blocked", "task.completed", "task.failed",
})

HookFn = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class HookDecision:
    """hook 的返回值协议：False / {"blocked": True} / 本类都表示阻断。"""

    blocked: bool = False
    reason: str = ""


@dataclass
class HookRegistration:
    id: str
    event: str
    priority: int
    once: bool
    fn: HookFn


@dataclass
class HookOutcome:
    """一次 emit 的确定性结果：是否被阻断 + 完整触发轨迹。"""

    event: str
    blocked: bool = False
    reason: str = ""
    fired: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "blocked": self.blocked,
            "reason": self.reason[:200],
            "fired_count": len(self.fired),
            "errors": [f for f in self.fired if not f.get("ok")][:5],
        }


def _normalize(result: Any) -> HookDecision:
    if result is False:
        return HookDecision(blocked=True, reason="hook returned False")
    if result is None or result is True:
        return HookDecision()
    if isinstance(result, HookDecision):
        return result
    if isinstance(result, dict):
        return HookDecision(
            blocked=bool(result.get("blocked")), reason=str(result.get("reason", ""))
        )
    return HookDecision()


class HookBus:
    """同步、确定性、可阻断的进程内生命周期钩子总线。"""

    def __init__(self) -> None:
        self._reg: dict[str, list[HookRegistration]] = {}
        self._lock = threading.Lock()

    def register(
        self, event: str, fn: HookFn, *, priority: int = 100, once: bool = False
    ) -> str:
        if event not in HOOK_EVENTS:
            raise ValueError(
                f"unknown hook event {event!r}; see HOOK_EVENTS ({len(HOOK_EVENTS)} events)"
            )
        if not callable(fn):
            raise ValueError("hook fn must be callable")
        hid = uuid.uuid4().hex[:12]
        with self._lock:
            self._reg.setdefault(event, []).append(
                HookRegistration(id=hid, event=event, priority=priority, once=once, fn=fn)
            )
        return hid

    def unregister(self, hook_id: str) -> bool:
        with self._lock:
            for event, regs in self._reg.items():
                for r in regs:
                    if r.id == hook_id:
                        regs.remove(r)
                        return True
        return False

    def listeners(self, event: str) -> list[dict[str, Any]]:
        """注册视图（不含 fn 本体——fn 不可序列化进 API 响应）。"""
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown hook event {event!r}")
        with self._lock:
            regs = sorted(self._reg.get(event, []), key=lambda r: r.priority)
        return [
            {"id": r.id, "event": r.event, "priority": r.priority, "once": r.once}
            for r in regs
        ]

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> HookOutcome:
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown hook event {event!r}")
        with self._lock:
            regs = sorted(self._reg.get(event, []), key=lambda r: r.priority)
        out = HookOutcome(event=event)
        body = dict(payload or {})
        for r in regs:
            try:
                decision = _normalize(r.fn(body))
                out.fired.append({"id": r.id, "ok": True})
                if decision.blocked:
                    out.blocked = True
                    out.reason = decision.reason
                    if r.once:
                        self.unregister(r.id)
                    break
            except Exception as exc:  # noqa: BLE001 —— 异常隔离：不炸主干
                out.fired.append(
                    {"id": r.id, "ok": False,
                     "error": f"{type(exc).__name__}: {exc}"[:200]}
                )
            finally:
                if r.once:
                    self.unregister(r.id)
        return out


_DEFAULT_BUS: HookBus | None = None
_BUS_LOCK = threading.Lock()


def get_default_hook_bus() -> HookBus:
    """进程内默认单例。重启即清空（诚实边界：持久化注册表是后续切片）。"""
    global _DEFAULT_BUS
    with _BUS_LOCK:
        if _DEFAULT_BUS is None:
            _DEFAULT_BUS = HookBus()
        return _DEFAULT_BUS


def reset_default_hook_bus() -> None:
    """测试专用：清空默认单例。"""
    global _DEFAULT_BUS
    with _BUS_LOCK:
        _DEFAULT_BUS = HookBus()
