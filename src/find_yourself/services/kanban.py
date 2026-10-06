"""任务看板服务层（A-任务看板-01～13）。

这一层把 ``tasks`` 表的既有状态机**投影**成看板，而不是另建一套状态。
需求原文写的是「待办 / 进行中 / 阻塞 / 完成」四列，而
``db.models.TASK_STATUSES`` 是 ``queued / running / waiting_input /
waiting_approval / completed / failed / cancelled``。**刻意不改那个枚举**——
它是冻结契约的一部分，被 CHECK 约束、被工作流引擎、被预算服务共同引用。
改成看板的四态会波及全仓。因此这里做**列映射**（见 ``_STATUS_TO_COLUMN``）：
看板列是投影，状态机是单一真源（需求 03 的追加说明与冲突 #11 的裁定都要求
「进度由任务状态机单一来源驱动」）。

进度语义（需求 03）
--------------------
「逐子任务加权完成百分比」——按子任务 ``weight`` 加权，**非简单平均**，这样
「改文案的小任务」刷满也拉不高整体。父任务的加权进度**现算不落库**：
冗余缓存必然与子任务漂移，而漂移的缓存给出的就是错答案。

单个任务的进度优先取显式 ``progress_percent``（允许表达「做到一半」）；
为 NULL 时回落到状态机：``completed`` → 100，其余 → 0。

阻塞与红带（需求 11）
--------------------
红带只由**两类真实信号**点亮，绝不造假数据：

1. T6 抗中断台账里 ``status='open'`` 的中断事件（``interruption_events``）；
2. 预算占用达到 ``BudgetLimits.warn_threshold_pct``（默认 80%）。

延期（delay）不点亮红带——需求明确「delay 用黄/橙区分，不抢红」，所以这里
只产出 ``red_band``，延期语义留给前端用 ``--ui-st-*`` 的黄/橙档表达。

依赖（需求 05）
----------------
依赖边存在 ``task_dependencies``，成环检测在这里做（跨行图性质，DB CHECK
表达不了）。检测范式照 ``services/agent_teams.py::_assert_no_cycle``：三色
DFS，并把出错的那条链原样拼进错误消息，用户能直接看出该删哪条边。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    TASK_STATUSES,
    BudgetReservation,
    Task,
    TaskDependency,
    TaskEvent,
)
from ..db.resilience_models import InterruptionEvent
from .actor import Actor
from .audit import AuditService
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed

#: 看板四列，顺序即前端列顺序。需求 01 的「待办/进行中/阻塞/完成」。
KANBAN_COLUMNS: tuple[str, ...] = ("todo", "doing", "blocked", "done")

#: ``Task.status`` → 看板列。``None`` = 不上板。
#:
#: - ``waiting_input`` / ``waiting_approval`` 都算「阻塞」：它们都在等人，
#:   对用户而言就是卡住了，需求 11 的红带也正该点亮这类。
#: - ``failed`` 归入「阻塞」而不是单开一列：需求 11 要求 failed 用**强红**，
#:   与 blocked 同属红带语义。
#: - ``cancelled`` **不上板**：四列里没有它。但它也不会被悄悄吞掉——
#:   汇总里单列 ``cancelled_count``，详情页仍可查到。
_STATUS_TO_COLUMN: dict[str, str | None] = {
    "queued": "todo",
    "running": "doing",
    "waiting_input": "blocked",
    "waiting_approval": "blocked",
    "failed": "blocked",
    "completed": "done",
    "cancelled": None,
}

#: 计入预算占用的预留状态。``released`` / ``cancelled`` 是钱没花出去的，
#: 算进去会把真实占用虚高，触发假红带。
_BUDGET_LIVE_STATES = ("reserved", "settled", "unknown")

#: 红带升级阈值：阻塞超过这个小时数即标 ``escalated``（需求 11「超 N 小时升级」）。
DEFAULT_ESCALATION_HOURS = 6

#: 看板操作 → (目标状态, 允许的前置状态)。**显式**写死，不靠 if 链推断。
#:
#: - ``pause`` 落到 ``waiting_input``：看板没有独立的「暂停」列，而
#:   ``waiting_input`` 的语义就是「在等人」，且它已经在 ``_STATUS_TO_COLUMN``
#: 里映射到 blocked 列——暂停的任务因此自动出现在阻塞列，不需要新造状态。
#: - ``terminate`` 覆盖所有非终态；已 cancelled 由方法内幂等护栏处理。
#: - ``completed`` / ``failed`` 不在任何 ``allowed`` 里：这两个状态由工作流
#:   引擎判定，**看板不允许手工把任务标成「完成」**——那会造出引擎不认的成功。
_TRANSITIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pause": ("waiting_input", ("running", "waiting_approval")),
    "resume": ("running", ("waiting_input",)),
    "terminate": ("cancelled", (
        "queued", "running", "waiting_input", "waiting_approval", "failed",
    )),
}


def _allowed_from(status: str) -> set[str]:
    """Which operations are legal from ``status`` (used in 409 messages)."""
    return {op for op, (_target, allowed) in _TRANSITIONS.items() if status in allowed}


class _Unset:
    """Sentinel distinguishing "field absent" from "field explicitly cleared"."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


def _clamp_pct(value: int) -> int:
    return max(0, min(100, int(value)))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _aware(moment: datetime | None) -> datetime | None:
    """Attach UTC to naive datetimes coming back from SQLite.

    SQLite drops tzinfo on the way out, so a naive value would crash the
    subtraction below. PG keeps it. Normalising here keeps one code path.
    """
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class KanbanService:
    """Read + mutate the kanban projection of an owner's task tree."""

    def __init__(self, session: Session, audit: AuditService, *, budget=None):
        self.session = session
        self.audit = audit
        self.budget = budget

    # ------------------------------------------------------------------
    # 内部：取数
    # ------------------------------------------------------------------
    def _owned_tasks(self, actor: Actor) -> list[Task]:
        """该 owner 名下、且不是别人的子任务的所有任务行。

        子任务与父任务同为 ``tasks`` 行（``parent_task_id`` 自关联），看板把
        **顶层任务**作为卡片、子任务作为详情里的清单——否则一条任务会在板上
        出现两次（父一次、子又是一次）。
        """
        if not actor.owner_id:
            raise PermissionDenied("owner_required", "Kanban requires an owner actor")
        rows = self.session.execute(
            select(Task).where(Task.owner_id == actor.owner_id)
        ).scalars().all()
        return list(rows)

    def _children_map(self, tasks: list[Task]) -> dict[str, list[Task]]:
        by_parent: dict[str, list[Task]] = {}
        for task in tasks:
            if task.parent_task_id:
                by_parent.setdefault(task.parent_task_id, []).append(task)
        return by_parent

    def _effective_progress(self, task: Task) -> int:
        """本行进度 0-100。显式值优先，否则回落到状态机（需求 03）。"""
        if task.progress_percent is not None:
            return _clamp_pct(task.progress_percent)
        return 100 if task.status == "completed" else 0

    def _weighted_progress(self, task: Task, children: list[Task]) -> int:
        """按子任务 weight 加权的完成百分比，非简单平均（需求 03）。

        没有子任务时就是本行进度。权重和为 0（理论上不该发生，因为 ``weight``
        有 NOT NULL DEFAULT 1）时退回本行进度，而不是除零。
        """
        if not children:
            return self._effective_progress(task)
        total_weight = sum(max(0, int(c.weight or 0)) for c in children)
        if total_weight <= 0:
            return self._effective_progress(task)
        acc = sum(self._effective_progress(c) * max(0, int(c.weight or 0)) for c in children)
        return _clamp_pct(round(acc / total_weight))

    # ------------------------------------------------------------------
    # 内部：红带信号（真实数据，绝不造假）
    # ------------------------------------------------------------------
    def _open_interruptions(self, owner_id: str) -> dict[str, list[InterruptionEvent]]:
        rows = self.session.execute(
            select(InterruptionEvent).where(
                InterruptionEvent.owner_id == owner_id,
                InterruptionEvent.status == "open",
            )
        ).scalars().all()
        grouped: dict[str, list[InterruptionEvent]] = {}
        for row in rows:
            if row.task_id:
                grouped.setdefault(row.task_id, []).append(row)
        return grouped

    def _budget_usage_pct(self, task_ids: list[str]) -> dict[str, float]:
        """每个任务树的预算占用百分比，数据源是真实的 ``budget_reservations``。"""
        if not task_ids or self.budget is None:
            return {}
        limits = getattr(self.budget, "limits", None)
        per_task = getattr(limits, "per_task_usd", None)
        if not per_task or Decimal(per_task) <= 0:
            return {}
        rows = self.session.execute(
            select(BudgetReservation.task_id, func.sum(BudgetReservation.amount))
            .where(
                BudgetReservation.task_id.in_(task_ids),
                BudgetReservation.state.in_(_BUDGET_LIVE_STATES),
            )
            .group_by(BudgetReservation.task_id)
        ).all()
        spent = {tid: float(total or 0) for tid, total in rows}
        return {tid: (amount / float(per_task)) * 100.0 for tid, amount in spent.items()}

    def _red_bands(
        self,
        actor: Actor,
        tasks: list[Task],
        escalation_hours: int,
    ) -> dict[str, dict[str, Any]]:
        """task_id → 红带详情。只收真实信号，没有信号就没有红带。"""
        by_id = {t.id: t for t in tasks}
        interruptions = self._open_interruptions(actor.owner_id)
        warn_pct = float(
            getattr(getattr(self.budget, "limits", None), "warn_threshold_pct", 80) or 80
        )
        usage = self._budget_usage_pct(list(by_id))
        now = datetime.now(timezone.utc)
        cutoff = timedelta(hours=escalation_hours)

        bands: dict[str, dict[str, Any]] = {}
        for task_id, events in interruptions.items():
            task = by_id.get(task_id)
            if task is None:
                continue
            oldest = min((_aware(e.created_at) for e in events), default=None)
            age = (now - oldest) if oldest else None
            bands[task_id] = {
                "reasons": [{
                    "kind": "interruption_open",
                    "detail": events[0].detail or events[0].interruption_class,
                    "count": len(events),
                    "since": _iso(oldest),
                }],
                "since": _iso(oldest),
                "duration_minutes": int(age.total_seconds() // 60) if age else None,
                "escalated": bool(age and age > cutoff),
                "escalation_hours": escalation_hours,
            }

        for task_id, pct in usage.items():
            if pct < warn_pct:
                continue   # 未到阈值不点亮（延期/接近上限不抢红，需求 11）
            task = by_id.get(task_id)
            if task is None:
                continue
            band = bands.setdefault(task_id, {
                "reasons": [], "since": None, "duration_minutes": None,
                "escalated": False, "escalation_hours": escalation_hours,
            })
            band["reasons"].append({
                "kind": "budget_threshold",
                "detail": f"{pct:.1f}% of the per-task budget "
                          f"(threshold {warn_pct:.0f}%)",
                "percent": round(pct, 1),
                "threshold_percent": warn_pct,
            })
            band["escalated"] = True
        return bands

    # ------------------------------------------------------------------
    # 读：看板
    # ------------------------------------------------------------------
    def board(
        self,
        actor: Actor,
        *,
        escalation_hours: int = DEFAULT_ESCALATION_HOURS,
    ) -> dict[str, Any]:
        """整块看板：列 + 卡片 + 汇总。

        空板返回四列的空列表与零值汇总——**诚实的空形状**，不返回 null，
        也不用假卡片填坑。
        """
        tasks = self._owned_tasks(actor)
        children_of = self._children_map(tasks)
        red_bands = self._red_bands(actor, tasks, escalation_hours)

        deps_down: dict[str, list[str]] = {}
        deps_up: dict[str, list[str]] = {}
        if tasks:
            owned = {t.id for t in tasks}
            rows = self.session.execute(
                select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
                .where(TaskDependency.owner_id == actor.owner_id)
            ).all()
            for downstream, upstream in rows:
                # 只画两端都归该 owner 的边，否则会泄露他人任务 id
                if downstream in owned and upstream in owned:
                    deps_down.setdefault(downstream, []).append(upstream)
                    deps_up.setdefault(upstream, []).append(downstream)

        columns: dict[str, list[dict[str, Any]]] = {c: [] for c in KANBAN_COLUMNS}
        cancelled_count = 0
        for task in tasks:
            # 🔴 只有**顶层**任务上板。子任务与父任务同为 ``tasks`` 行，若不过滤，
            # 同一个任务会在板上出现两次（父一次、子又是一次），计数翻倍。
            if task.parent_task_id:
                continue
            column = _STATUS_TO_COLUMN.get(task.status)
            if column is None:
                if task.status == "cancelled":
                    cancelled_count += 1
                continue
            columns[column].append(self._card(task, children_of, red_bands, deps_down, deps_up))

        for cards in columns.values():
            cards.sort(key=lambda c: (not c["critical"], -c["weighted_progress"], c["id"]))

        top_level = [t for t in tasks if not t.parent_task_id]
        total_weight = sum(max(0, int(t.weight or 0)) for t in top_level)
        if total_weight:
            overall = _clamp_pct(round(sum(
                self._weighted_progress(t, children_of.get(t.id, []))
                * max(0, int(t.weight or 0))
                for t in top_level
            ) / total_weight))
        else:
            overall = 0

        return {
            "columns": [
                {"id": cid, "cards": columns[cid], "count": len(columns[cid])}
                for cid in KANBAN_COLUMNS
            ],
            "summary": {
                "total_cards": sum(len(c) for c in columns.values()),
                "weighted_progress": overall,
                "red_band_count": sum(1 for c in columns.values() for card in c
                                      if card["red_band"]),
                "cancelled_count": cancelled_count,
                "escalation_hours": escalation_hours,
            },
        }

    def _card(
        self,
        task: Task,
        children_of: dict[str, list[Task]],
        red_bands: dict[str, dict[str, Any]],
        deps_down: dict[str, list[str]],
        deps_up: dict[str, list[str]],
    ) -> dict[str, Any]:
        children = children_of.get(task.id, [])
        weighted = self._weighted_progress(task, children)
        band = red_bands.get(task.id)
        scheduled = bool(task.planned_start and task.planned_end)
        return {
            "id": task.id,
            "goal": task.goal,
            "status": task.status,
            "stage": task.stage,
            "domain": task.domain,
            "mode": task.mode,
            "critical": bool(task.critical),
            "weight": int(task.weight or 1),
            "own_progress": self._effective_progress(task),
            "weighted_progress": weighted,
            "progress_source": "subtasks" if children else "self",
            "subtask_count": len(children),
            "done_subtask_count": sum(1 for c in children if c.status == "completed"),
            "depends_on": sorted(deps_down.get(task.id, [])),
            "blocks": sorted(deps_up.get(task.id, [])),
            "planned_start": _iso(_aware(task.planned_start)),
            "planned_end": _iso(_aware(task.planned_end)),
            "scheduled": scheduled,
            "blocked_reason": task.blocked_reason,
            "blocked_since": _iso(_aware(task.blocked_since)),
            "red_band": band is not None,
            "red_band_detail": band,
            "created_at": _iso(_aware(task.created_at)),
            "updated_at": _iso(_aware(task.updated_at)),
            "deadline": _iso(_aware(task.deadline)),
        }

    # ------------------------------------------------------------------
    # 读：详情（需求 02 / 10）
    # ------------------------------------------------------------------
    def detail(self, actor: Actor, task_id: str) -> dict[str, Any]:
        """任务详情：本体 + 子任务清单 + 关键事项 + 依赖边 + 历史记录。"""
        task = self.session.get(Task, task_id)
        if task is None or task.owner_id != actor.owner_id:
            # 他人的任务一律按「不存在」回，绝不泄露存在性（与既有路由一致）。
            raise NotFound("task_not_found", f"Task {task_id} not found")

        children = self.session.execute(
            select(Task).where(Task.parent_task_id == task_id)
            .order_by(Task.critical.desc(), Task.weight.desc(), Task.id)
        ).scalars().all()

        upstream = self.session.execute(
            select(TaskDependency).where(TaskDependency.task_id == task_id)
        ).scalars().all()
        downstream = self.session.execute(
            select(TaskDependency).where(TaskDependency.depends_on_task_id == task_id)
        ).scalars().all()

        events = self.session.execute(
            select(TaskEvent).where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        ).scalars().all()

        children_of = {task_id: list(children)}
        card = self._card(
            task, children_of,
            self._red_bands(actor, [task], DEFAULT_ESCALATION_HOURS),
            {task_id: [d.depends_on_task_id for d in upstream]},
            {task_id: [d.task_id for d in downstream]},
        )
        return {
            "task": card,
            "subtasks": [
                {
                    "id": c.id,
                    "goal": c.goal,
                    "status": c.status,
                    "critical": bool(c.critical),
                    "weight": int(c.weight or 1),
                    "own_progress": self._effective_progress(c),
                    "blocked_reason": c.blocked_reason,
                }
                for c in children
            ],
            "checklist": [
                {
                    "id": c.id,
                    "goal": c.goal,
                    "done": c.status == "completed",
                    "critical": bool(c.critical),
                }
                for c in children
            ],
            "critical_items": [
                {"id": c.id, "goal": c.goal, "status": c.status}
                for c in children if c.critical
            ] + ([{"id": task.id, "goal": task.goal, "status": task.status}]
                 if task.critical else []),
            "dependencies": {
                "blocked_by": [
                    {"task_id": d.depends_on_task_id,
                     "satisfied": self._is_satisfied(actor, d.depends_on_task_id)}
                    for d in upstream
                ],
                "blocks": [{"task_id": d.task_id} for d in downstream],
            },
            "history": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "from_status": e.from_status,
                    "to_status": e.to_status,
                    "detail": e.detail,
                    "created_at": _iso(_aware(e.created_at)),
                }
                for e in events
            ],
        }

    def _is_satisfied(self, actor: Actor, upstream_id: str) -> bool:
        """上游是否已完成（= 下游的前置条件已满足）。"""
        row = self.session.get(Task, upstream_id)
        if row is None or row.owner_id != actor.owner_id:
            return False
        return row.status == "completed"

    # ------------------------------------------------------------------
    # 依赖（需求 05）
    # ------------------------------------------------------------------
    def add_dependency(
        self, actor: Actor, task_id: str, depends_on_task_id: str
    ) -> dict[str, Any]:
        """声明「A 完成后 B 才能开始」。成环即 409，并给出该删哪条边。"""
        if task_id == depends_on_task_id:
            raise ValidationFailed(
                "self_dependency", "A task cannot depend on itself.",
            )
        for candidate in (task_id, depends_on_task_id):
            row = self.session.get(Task, candidate)
            if row is None or row.owner_id != actor.owner_id:
                raise NotFound("task_not_found", f"Task {candidate} not found")

        graph = self._dependency_graph(actor)
        # 新边 direction: upstream -> downstream（即 depends_on -> task）
        graph.setdefault(depends_on_task_id, []).append(task_id)
        _assert_acyclic(graph)

        existing = self.session.get(
            TaskDependency, (task_id, depends_on_task_id)
        )
        if existing is None:
            self.session.add(TaskDependency(
                task_id=task_id,
                depends_on_task_id=depends_on_task_id,
                owner_id=actor.owner_id,
                created_at=datetime.now(timezone.utc),
            ))
            self._record(actor, task_id, "dependency", detail={
                "depends_on": depends_on_task_id,
            })
            self.audit.append(actor, "kanban.dependency_added", task_id,
                              {"depends_on": depends_on_task_id})
            self.session.flush()
        return {"task_id": task_id, "depends_on": depends_on_task_id, "created": existing is None}

    def remove_dependency(
        self, actor: Actor, task_id: str, depends_on_task_id: str
    ) -> dict[str, Any]:
        row = self.session.get(TaskDependency, (task_id, depends_on_task_id))
        if row is None or row.owner_id != actor.owner_id:
            raise NotFound("dependency_not_found", "No such dependency edge")
        self.session.delete(row)
        self._record(actor, task_id, "dependency", detail={
            "depends_on": depends_on_task_id, "removed": True,
        })
        self.audit.append(actor, "kanban.dependency_removed", task_id,
                          {"depends_on": depends_on_task_id})
        self.session.flush()
        return {"task_id": task_id, "depends_on": depends_on_task_id, "removed": True}

    def _dependency_graph(self, actor: Actor) -> dict[str, list[str]]:
        """上游 → 下游 的邻接表，供三色 DFS 用。"""
        rows = self.session.execute(
            select(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            .where(TaskDependency.owner_id == actor.owner_id)
        ).all()
        graph: dict[str, list[str]] = {}
        for downstream, upstream in rows:
            graph.setdefault(upstream, []).append(downstream)
        return graph

    def blocking_chain(self, actor: Actor, task_id: str) -> list[dict[str, Any]]:
        """该任务当前被谁挡住（只回**未满足**的前置）。给 409 的响应体用。"""
        rows = self.session.execute(
            select(TaskDependency.depends_on_task_id)
            .where(TaskDependency.task_id == task_id,
                   TaskDependency.owner_id == actor.owner_id)
        ).scalars().all()
        blocking = []
        for upstream_id in rows:
            if not self._is_satisfied(actor, upstream_id):
                row = self.session.get(Task, upstream_id)
                blocking.append({
                    "task_id": upstream_id,
                    "goal": row.goal if row is not None else "",
                    "status": row.status if row is not None else "unknown",
                })
        return blocking

    # ------------------------------------------------------------------
    # 读：燃尽 / 燃起（需求 12）
    # ------------------------------------------------------------------
    def burndown(
        self, actor: Actor, *, days: int = 14, today: datetime | None = None
    ) -> dict[str, Any]:
        """燃尽/燃起数据点：理想线 + 实际线。

        🔴 口径必须说清楚（这是估算，不是台账）：

        * **剩余量** = 当天**未完成**的顶层任务权重和。不含子任务——子任务是
          父任务权重内部的拆分，计两次就重复计数了。
        * **实际点**只在「当天有过状态变更」的日子才有数据点：依据是
          ``task_events.created_at``。某天没有任何事件，就**不画点**，
          而不是把前一个值平移过去假装当天测过。
        * **理想线**从「区间首日的初始总量」线性降到 0，只是一条参考斜坡。

        没有真实历史就返回空 ``actual`` 序列 + ``partial: true``，前端据此显示
        「数据不足」，绝不补假点。
        """
        if days < 1:
            raise ValidationFailed("days_invalid", "days must be >= 1")
        end = _aware(today) or datetime.now(timezone.utc)
        start = (end - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        tasks = self._owned_tasks(actor)
        top_level = [t for t in tasks if not t.parent_task_id]
        weighted_ids = {t.id: max(0, int(t.weight or 0)) for t in top_level}

        # 每天的「已完成」集合来自事件流水，不是从当前状态倒推——当前状态只有
        # 一个快照，答不出「哪天完成的」。
        rows = self.session.execute(
            select(TaskEvent.task_id, TaskEvent.to_status, TaskEvent.created_at)
            .where(
                TaskEvent.owner_id == actor.owner_id,
                TaskEvent.kind == "status",
                TaskEvent.to_status == "completed",
                TaskEvent.created_at >= start,
            )
        ).all()
        completed_by_day: dict[str, set[str]] = {}
        for task_id, _to_status, created in rows:
            day = (_aware(created) or end).date().isoformat()
            completed_by_day.setdefault(day, set()).add(task_id)

        initial_total = sum(weighted_ids.values())
        # 区间首日「开始前」已完成的部分：从区间内每天的完成集合无法还原
        # 首日之前的历史，故用「首日之前完成的任务数」= 0 保守处理，
        # 即 ideal 从 initial_total 起算。这是有意选的保守口径，写在这里。
        actual: list[dict[str, Any]] = []
        burned_so_far: set[str] = set()
        for offset in range(days):
            day_dt = start + timedelta(days=offset)
            day = day_dt.date().isoformat()
            burned_so_far |= completed_by_day.get(day, set())
            remaining = sum(
                w for tid, w in weighted_ids.items() if tid not in burned_so_far
            )
            actual.append({
                "date": day,
                "remaining_weight": remaining,
                "completed_weight": initial_total - remaining,
                # 没有事件的空白日不补点，交给前端连线
                "sampled": bool(completed_by_day.get(day)),
            })

        ideal = [
            {
                "date": (start + timedelta(days=i)).date().isoformat(),
                "remaining_weight": round(initial_total * (1 - i / max(1, days - 1))),
            }
            for i in range(days)
        ]

        sampled_any = any(p["sampled"] for p in actual)
        return {
            "days": days,
            "initial_weight": initial_total,
            "ideal": ideal,
            "actual": actual,
            "sampled_days": sum(1 for p in actual if p["sampled"]),
            # honest flag：样本不足时前端必须显示「数据不足」而不是画一条假线
            "partial": not sampled_any,
            "method": (
                "remaining = sum(weight of top-level tasks not completed as of that day); "
                "completion days come from task_events(kind='status', to_status='completed'); "
                "days without events are reported with sampled=false and no interpolation"
            ),
        }

    # ------------------------------------------------------------------
    # 写：进度 / 计划（需求 02 / 13）
    # ------------------------------------------------------------------
    def set_progress(
        self, actor: Actor, task_id: str, progress_percent: int | None = UNSET,
        *, weight: int | None = UNSET, critical: bool | None = UNSET,
    ) -> dict[str, Any]:
        """写本行进度 / 权重 / 关键事项标记。

        三种语义必须分清，所以用 ``UNSET`` 哨兵而不是 ``None``：

        * 不传（``UNSET``）→ 不改这一项；
        * 传 ``None`` → **清空**（进度回到「未开始」，甘特回到「未排期」）；
        * 传值 → 写入。

        用 ``None`` 同时表达「不改」和「清空」会让 API 无法把进度撤回未开始，
        而「未开始」在需求 03 的语义里是一个真实状态，不是缺省。
        """
        if progress_percent is not UNSET and progress_percent is not None:
            if not (0 <= progress_percent <= 100):
                raise ValidationFailed(
                    "progress_out_of_range", "progress_percent must be within 0..100",
                )
        if weight is not UNSET and weight is not None and weight < 0:
            raise ValidationFailed("weight_negative", "weight must be >= 0")

        task = self._owned_task(actor, task_id)
        changed: dict[str, Any] = {}
        if progress_percent is not UNSET:
            task.progress_percent = (
                None if progress_percent is None else _clamp_pct(progress_percent)
            )
            changed["progress_percent"] = task.progress_percent
        if weight is not UNSET:
            task.weight = 1 if weight is None else weight
            changed["weight"] = task.weight
        if critical is not UNSET:
            task.critical = False if critical is None else bool(critical)
            changed["critical"] = task.critical

        self._record(actor, task_id, "progress", detail={"changed": changed})
        self.audit.append(actor, "kanban.progress_set", task_id, changed)
        self.session.flush()
        return self._card(task, {}, {}, {}, {})

    def set_plan(
        self, actor: Actor, task_id: str, *,
        planned_start: datetime | None = None,
        planned_end: datetime | None = None,
    ) -> dict[str, Any]:
        """写排期（甘特的条）。两者皆空 = 未排期。"""
        task = self._owned_task(actor, task_id)
        task.planned_start = planned_start
        task.planned_end = planned_end
        self._record(actor, task_id, "plan", detail={
            "planned_start": _iso(planned_start),
            "planned_end": _iso(planned_end),
        })
        self.audit.append(actor, "kanban.plan_set", task_id, {
            "planned_start": _iso(planned_start), "planned_end": _iso(planned_end),
        })
        self.session.flush()
        return self._card(task, {}, {}, {}, {})

    # ------------------------------------------------------------------
    # 生命周期（需求 06）+ 依赖门控（需求 05 后半）
    # ------------------------------------------------------------------
    def transition(
        self, actor: Actor, task_id: str, operation: str, *,
        reason: str = "",
    ) -> dict[str, Any]:
        """看板级统一操作入口：``pause`` / ``resume`` / ``terminate``。

        状态流转矩阵是**显式**的，不靠 if 链猜：

        * ``pause``：只接受在跑的状态（``running`` / ``waiting_*``）→ ``waiting_input``
          并记下 ``blocked_reason``（需求 11 的红带要「原因」，所以这里必须写）。
        * ``resume``：只接受 ``waiting_input`` → 回 ``running``。
        * ``terminate``：任何非终态 → ``cancelled``；**已 cancelled 幂等返回**
          （重复点「终止」不该报错，那是 UI 的一次重复点击，不是非法操作）。

        非法流转一律 409，并说明当前状态与允许的流转，不静默成功。
        """
        task = self._owned_task(actor, task_id)
        current = task.status
        target, allowed = _TRANSITIONS.get(operation, (None, ()))
        if target is None:
            raise ValidationFailed(
                "unknown_operation",
                f"Unknown operation '{operation}'. "
                f"Allowed: {', '.join(sorted(_TRANSITIONS))}.",
            )

        if operation == "terminate" and current == "cancelled":
            # 幂等护栏：重复终止返回同一形状，不报错也不重复写事件。
            return {
                "task_id": task_id,
                "status": current,
                "changed": False,
                "idempotent": True,
                "allowed_operations": sorted(_allowed_from(current)),
            }

        if current not in allowed:
            raise Conflict(
                "illegal_transition",
                f"Cannot {operation} a task in status '{current}'. "
                f"Allowed from '{current}': "
                f"{', '.join(sorted(_allowed_from(current))) or '(terminal)'}.",
            )

        if operation == "resume":
            blocking = self.blocking_chain(actor, task_id)
            if blocking:
                # 依赖门控：A 没完成，B 不许开（需求 05）
                raise Conflict(
                    "dependency_blocking",
                    "Cannot resume: upstream dependencies are not finished.",
                ) from None

        task.status = target
        if target == "waiting_input":
            task.blocked_reason = (reason or "paused by owner")[:500]
            task.blocked_since = datetime.now(timezone.utc)
        elif operation == "resume":
            task.blocked_reason = None
            task.blocked_since = None

        self._record(actor, task_id, "status", from_status=current, to_status=target,
                     detail={"operation": operation, "reason": reason})
        self.audit.append(actor, f"kanban.{operation}", task_id,
                          {"from": current, "to": target, "reason": reason})
        self.session.flush()
        return {
            "task_id": task_id,
            "status": target,
            "previous_status": current,
            "changed": True,
            "idempotent": False,
            "blocked_reason": task.blocked_reason,
            "blocked_since": _iso(_aware(task.blocked_since)),
            "allowed_operations": sorted(_allowed_from(target)),
        }

    def start(self, actor: Actor, task_id: str, *, reason: str = "") -> dict[str, Any]:
        """把任务从 ``queued`` 推到 ``running``，走同一套依赖门控。

        单独存在而不是复用 ``resume``：``resume`` 的语义是「暂停后恢复」，
        它的合法前态只有 ``waiting_input``；而「开始一个新任务」的前态是
        ``queued``。两者混用会让状态机出现两条意义不同的入口。
        """
        task = self._owned_task(actor, task_id)
        if task.status != "queued":
            raise Conflict(
                "illegal_transition",
                f"Cannot start a task in status '{task.status}'. "
                f"Only 'queued' tasks can be started.",
            )
        blocking = self.blocking_chain(actor, task_id)
        if blocking:
            raise Conflict(
                "dependency_blocking",
                "Cannot start: upstream dependencies are not finished.",
            )
        previous = task.status
        task.status = "running"
        self._record(actor, task_id, "status", from_status=previous, to_status="running",
                     detail={"operation": "start", "reason": reason})
        self.audit.append(actor, "kanban.start", task_id, {"reason": reason})
        self.session.flush()
        return {
            "task_id": task_id,
            "status": "running",
            "previous_status": previous,
            "changed": True,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _owned_task(self, actor: Actor, task_id: str) -> Task:
        task = self.session.get(Task, task_id)
        if task is None or task.owner_id != actor.owner_id:
            raise NotFound("task_not_found", f"Task {task_id} not found")
        return task

    def _record(
        self, actor: Actor, task_id: str, kind: str, *,
        from_status: str | None = None, to_status: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(TaskEvent(
            id=f"evt-{uuid4().hex[:16]}",
            task_id=task_id,
            owner_id=actor.owner_id,
            kind=kind,
            from_status=from_status,
            to_status=to_status,
            detail=detail,
            created_at=datetime.now(timezone.utc),
        ))


def _assert_acyclic(graph: dict[str, list[str]]) -> None:
    """三色 DFS 检测成环，范式照 ``services/agent_teams.py::_assert_no_cycle``。

    出错时把**导致成环的那条链**原样拼进消息，用户能直接看出该删哪条边，
    而不是只得到一句「存在环」。
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    for targets in graph.values():
        for target in targets:
            color.setdefault(target, WHITE)

    def visit(node: str, path: list[str]) -> None:
        color[node] = GREY
        for nxt in graph.get(node, []):
            if color.get(nxt) == GREY:
                start = path.index(nxt) if nxt in path else 0
                cycle = path[start:] + [nxt]
                raise Conflict(
                    "dependency_cycle",
                    f"Cyclic task dependency detected: {' -> '.join(cycle)}. "
                    f"Remove one of those edges before the board can be planned.",
                )
            if color.get(nxt) == WHITE:
                visit(nxt, path + [nxt])
        color[node] = BLACK

    for node in list(graph):
        if color.get(node) == WHITE:
            visit(node, [node])


# Re-exported so callers can validate a status without importing db.models.
VALID_TASK_STATUSES = TASK_STATUSES