"""调度器 ↔ Agent 总线：周期回收 + 状态桥。

为什么需要这个模块
------------------
:class:`~find_yourself.services.scheduler.core.UnifiedScheduler` 早就实现了
``reclaim_stale()``（把超 deadline 仍在跑的任务标记 ``reclaimed`` 并释放并发
名额）和 ``subscribe()``（状态监听器），但**生产代码里零调用者**——只有测试
调它们。后果是两条：

1. **超时任务永不释放并发名额**：Python 杀不死线程，执行单元卡死时名额一直占着，
   并发上限被逐渐吃光，调度器从「慢」变成「死」。
2. **用户看不到任务进展**：用户在总控那头等的是「做完没」，但任务状态只活在
   调度器内存里，总线房间一片安静。

本模块把这两条孤儿能力接上：起一个后台节拍线程定期收尸，并把任务状态变化
（终态、回收）播到对应房间。

诚实原则
--------
* **播报失败绝不打断调度**：总线是通知通道，通知挂了任务照跑，只是用户看不见。
* **没有 room 就静默跳过**：不是所有任务都有房间（内部批处理任务就没有），
  硬编一个房间等于凭空捏造一个用户从没订阅过的会话。
* **只播终态**：中间态（dispatched/running）变化太频繁，逐条播会淹掉用户真正
  该关心的信息。回收是例外——它代表「出事了」，必须立刻说。

任务怎么找到自己的房间
----------------------
靠 :attr:`DispatchRequest.meta` 里的 ``room`` 键。``meta`` 本来就是给调用方
放自定义标注用的（实测已在用 ``parent_task_id`` / ``capability``），所以这里
约定 ``meta["room"]`` 为总控为该任务指定的播报房间，``meta["owner_id"]`` 用于
@提醒。这是**加法**不是改动：不传就跳过，既有调用方一律不受影响。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ..services.scheduler.core import (
    TASK_FAILED,
    TASK_RECLAIMED,
    TASK_SUCCEEDED,
    TERMINAL_STATES,
    TaskRecord,
    UnifiedScheduler,
)
from .agent_bus import OWNER_PREFIX, SYSTEM_IDENTITY, AgentBus

logger = logging.getLogger(__name__)

#: 默认回收节拍（秒）。10 秒的取舍：太短会空转烧 CPU，太长则用户干等名额释放。
DEFAULT_RECLAIM_INTERVAL = 10.0

#: 终态 → 中文措辞。给用户看的是「发生了什么」，不是内部状态机名。
_TERMINAL_TEXT: dict[str, str] = {
    TASK_SUCCEEDED: "已完成",
    TASK_FAILED: "失败",
    TASK_RECLAIMED: "超时被回收",
    "cancelled": "已取消",
    "rejected": "被拒绝",
}

#: 走完整闭环的总控角色。与 templates/scaffold.CONTROLLER_ID 同值，
#: 但 runtime 层不能反向 import services/templates（会成环），故各自定义、
#: 由测试钉住一致性。
COORDINATOR_ROLE = "coordinator"


def _room_of(record: TaskRecord) -> str:
    """取出该任务应播报的房间；没有就返回空串（调用方跳过）。"""
    meta = record.meta if isinstance(record.meta, dict) else {}
    return str(meta.get("room") or "").strip()


def _owner_of(record: TaskRecord) -> str:
    meta = record.meta if isinstance(record.meta, dict) else {}
    return str(meta.get("owner_id") or "").strip()


class SchedulerBusBridge:
    """把调度器状态变化桥到总线的听桥器 + 周期回收器。

    用法（app lifespan）::

        bridge = SchedulerBusBridge.attach(scheduler, bus, interval=10.0)
        # …应用运行…
        bridge.close()      # 必须在 shutdown 时调用，否则线程泄漏

    :meth:`close` 幂等：重复调用不报错，但会让节拍线程真正退出。
    """

    def __init__(
        self,
        scheduler: UnifiedScheduler,
        bus: AgentBus | None = None,
        *,
        interval: float = DEFAULT_RECLAIM_INTERVAL,
    ) -> None:
        self._scheduler = scheduler
        self._bus = bus
        # 保留 0（= 只桥不回收），其余夹到 [1, 3600]。用 max(1.0, ...) 会把
        # 「关闭节拍」这个语义吃掉，所以这里只夹上界。
        self._interval = min(float(interval), 3600.0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._reclaimed_total = 0
        self._published_total = 0
        self._publish_failures = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    @classmethod
    def attach(
        cls,
        scheduler: UnifiedScheduler,
        bus: AgentBus | None = None,
        *,
        interval: float = DEFAULT_RECLAIM_INTERVAL,
    ) -> "SchedulerBusBridge | None":
        """接线：注册状态监听 + 起节拍线程。

        ``interval <= 0`` 表示**只要状态桥、不要周期回收**（返回的实例仍然
        监听任务状态，但不退化成忙等）。此时返回的对象仍需 :meth:`close`。
        """
        bridge = cls(scheduler, bus, interval=interval)
        bridge._unsubscribe = scheduler.subscribe(bridge.on_task_event)
        if interval <= 0:
            logger.info("scheduler bus bridge attached (status bridge only, no beat)")
            return bridge
        bridge._thread = threading.Thread(
            target=bridge._beat, name="fy-scheduler-reclaim", daemon=True
        )
        bridge._thread.start()
        logger.info("scheduler bus bridge attached (reclaim every %.1fs)", bridge._interval)
        return bridge

    def close(self) -> None:
        """停节拍并退订。**幂等** —— 重复调用安全。"""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # join 有上限：不因为节拍卡住而拖死应用关停。
            thread.join(timeout=2.0)
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:  # noqa: BLE001 — 退订失败不该阻断关停
                logger.debug("scheduler bridge unsubscribe failed", exc_info=True)
            self._unsubscribe = None

    def __enter__(self) -> "SchedulerBusBridge":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 节拍
    # ------------------------------------------------------------------ #

    def _beat(self) -> None:
        """节拍循环：每次醒来收一次尸。

        ``wait`` 而非 ``sleep``：这样 :meth:`close` 能在 2 秒内立刻唤醒退出，
        不必干等一整个间隔。
        """
        while not self._stop.wait(self._interval):
            try:
                self.reclaim_once()
            except Exception as exc:  # noqa: BLE001 — 节拍线程必须活下去
                logger.warning("scheduler reclaim beat failed: %s", exc, exc_info=True)

    def reclaim_once(self) -> list[str]:
        """跑一次回收并把结果播到总线。返回被回收的 task_id 列表。

        单独暴露是为了可测：不用等真节拍就能断言「超时任务确实被收、确实被播」。
        """
        reclaimed_ids = self._scheduler.reclaim_stale()
        if not reclaimed_ids:
            return []
        self._reclaimed_total += len(reclaimed_ids)
        for tid in reclaimed_ids:
            record = self._scheduler.get_task(tid)
            if record is not None:
                self.announce_reclaimed(record)
        return reclaimed_ids

    # ------------------------------------------------------------------ #
    # 状态桥
    # ------------------------------------------------------------------ #

    def on_task_event(self, record: TaskRecord) -> None:
        """调度器状态监听器 —— 只播终态与回收。

        被调度器在持锁状态下调用，所以这里**绝不能**再做可能阻塞或重入的操作：
        只做字符串拼装 + 一次非阻塞 ``bus.publish``。
        """
        if self._bus is None:
            return
        status = str(getattr(record, "status", "") or "")
        # 回收已被 reclaim_stale 单独播过（带原因），这里跳过避免双播
        if status not in TERMINAL_STATES or status == TASK_RECLAIMED:
            return
        room = _room_of(record)
        if not room:
            return
        self._publish_terminal(record, status, room)

    def announce_reclaimed(self, record: TaskRecord) -> None:
        """把一次回收播到总线。理由固定为「超时」，因为回收只有这一个触发条件。"""
        if self._bus is None:
            return
        room = _room_of(record)
        if not room:
            return
        self._publish_terminal(record, TASK_RECLAIMED, room)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _publish_terminal(self, record: TaskRecord, status: str, room: str) -> None:
        word = _TERMINAL_TEXT.get(status, status)
        content = (
            f"[任务{word}] {record.action}（{record.channel}）"
            f"，任务号 {record.task_id}"
        )
        detail = str(getattr(record, "error", "") or "").strip()
        if detail:
            content += f"，原因：{detail[:200]}"
        owner_id = _owner_of(record)
        try:
            self._bus.publish(
                room,
                from_identity=SYSTEM_IDENTITY,
                kind="system",
                content=content,
                mention=f"{OWNER_PREFIX}{owner_id}" if owner_id else None,
                refs=[record.task_id],
            )
            self._published_total += 1
        except Exception as exc:  # noqa: BLE001 — 通知失败不阻塞调度
            self._publish_failures += 1
            logger.warning(
                "scheduler bus publish failed for task %s: %s", record.task_id, exc
            )

    def stats(self) -> dict[str, Any]:
        """运行统计 —— 供 /ready 或诊断端点如实暴露，不隐藏失败。"""
        return {
            "attached": self._thread is not None,
            "interval_seconds": self._interval,
            "reclaimed_total": self._reclaimed_total,
            "published_total": self._published_total,
            "publish_failures": self._publish_failures,
            "has_bus": self._bus is not None,
            "listening": self._unsubscribe is not None,
        }
