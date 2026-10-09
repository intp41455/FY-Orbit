"""可观测与成本聚合服务（A-可观测-02/03 · P2，A-成本仪表盘-02 · P3）。

三条需求的落点，以及**每条都复用了什么既有资产**：

============ ======================================================= ==========
需求复用
============ ======================================================= ==========
日志面板 ``LogService.query``（五维筛选 + 脱敏）与「错误跳 trace」共用其序号轴
性能监控 ``task_events.created_at`` / ``canvas_events.created_at`` / ``dispatch_records``
成本进度条 ``tasks.steps`` / ``agent_instances.steps`` + ``budget_reservations``
============ ======================================================= ==========

🔴 **本模块不新建任何表。** 三条需求要的数据全部已经存在，所以**不需要迁移**——
这也是「不自行编号迁移」这条铁律在本包的最省事解法：没有迁移，就没有编号可撞。

设计上有三处刻意选择，都是为了不编数据：

1. **时间轴是 ``seq`` 不是墙钟。** ``audit_events`` 哈希链上没有时间戳列（见
   ``services/quality/logs.py:14-19``），所以日志一律按序号窗口筛，并在响应里用
   ``time_axis="seq"`` 明说。不拿 ``created_at`` 之类别的表的时间硬凑。

2. **「错误跳 trace」按可证的关联走，不虚构 span。** 本仓**没有**跨事件的 trace
   关联 id：``canvas_events.trace_id`` 是每条事件现生成的随机值（``canvas.py:1857``
   铸 ``tr-<hex8>``），不跨事件共享，所以拿它当 correlation id 是错的。真正能用的
   关联只有两种，都是既有的：
     * ``details["message_id"]`` —— ``AuditService.frames_for_message`` 认它，
       SSE 的 ``inject_message_id`` 也用它把一问一帧串起来；
     * 实体 id（task / canvas instance / team）—— 各自的事件表里有真实时间戳。
   两种都不成立时返回 ``basis="none"`` 并说明原因，**不返回空壳假装有 trace**。

3. **样本不足就报 ``partial``，不给估计值。** 「预计剩余」需要速度，速度需要历史；
   没有历史就给 ``null`` + ``reason``。这沿用 ``KanbanService.burndown`` 的
   ``partial: true`` 纪律（``services/kanban.py:588-599``）——数据不足时补假点，
   比明说「算不出来」有害得多。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..db.canvas_models import CanvasEvent, CanvasInstance, DispatchRecord
from ..db.models import AuditEvent, BudgetReservation, Task, TaskEvent
from ..db.team_models import AgentInstance, TeamDefinition, TeamEvent
from ..db.types import utcnow
from ..services.actor import Actor
from ..services.audit import AuditService
from ..services.budget import BudgetService
from ..services.errors import NotFound, PermissionDenied, ValidationFailed
from ..services.quality.logs import LogService, classify, level_rules, redact

#: ``budget_reservations`` 里仍然占用额度的状态（与 ``services/kanban.py:83`` 一致）。
#: ``released`` / ``cancelled`` 已把额度还回去，计入就会虚报花费。
_BUDGET_LIVE_STATES = ("reserved", "settled", "unknown")

#: 流式日志的默认轮询间隔（秒）。轮询而非事件总线，理由见 ``stream_logs``。
DEFAULT_POLL_INTERVAL = 2.0

#: 单次 SSE 连接最多连续推多少帧，之后让客户端重连。
#: 不设上限的话一个只读不消费的客户可以把连接永久钉住。
MAX_STREAM_FRAMES = 500

#: 连续多少个轮询周期没有新帧就收尾。只给 ``max_frames`` 是不够的：
#: 一直没有新事件时帧数永远不涨，生成器会一直转下去——所以「等不到」本身
#: 也必须是一个可数的终点。
MAX_STREAM_IDLE_POLLS = 10

#: 算百分位所需的最小样本数。低于此数报 ``partial`` 而不是给一个
#: 由两三个数据点决定的 p95——那种数字看着精确，其实噪声。
MIN_SAMPLES_FOR_PERCENTILE = 5


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _percentile(values: list[float], pct: float) -> float | None:
    """最近秩百分位。样本为空返回 None——**不返回 0**，0 是个很像真的假数字。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    # 最近秩：rank = ceil(pct/100 * n)，夹到 [1, n]
    import math

    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def _sample_gate(samples: list[float], *, what: str) -> dict[str, Any]:
    """百分位的诚实包装：够样本才给值，不够就明说差多少。"""
    if len(samples) < MIN_SAMPLES_FOR_PERCENTILE:
        return {
            "p50": None,
            "p95": None,
            "sample_count": len(samples),
            "partial": True,
            "reason": (
                f"{what} 样本 {len(samples)} 条，少于 {MIN_SAMPLES_FOR_PERCENTILE} 条"
                f"最低门槛；此时给百分位等于把噪声当结论"
            ),
        }
    return {
        "p50": _percentile(samples, 50),
        "p95": _percentile(samples, 95),
        "sample_count": len(samples),
        "partial": False,
        "reason": None,
    }


class ObservabilityService:
    """只读聚合。三条需求共用一个实例，构造在路由里就地完成（不碰 ``deps.py``）。"""

    def __init__(
        self,
        session: Session,
        *,
        audit: AuditService | None = None,
        logs: LogService | None = None,
        budget: BudgetService | None = None,
    ) -> None:
        self.session = session
        self.audit = audit
        self.logs = logs if logs is not None else LogService(session, audit=audit)
        self.budget = budget

    # ------------------------------------------------------------------
    # 需求 A-可观测-02 · 日志监控面板
    # ------------------------------------------------------------------
    def logs_panel(
        self,
        actor: Actor,
        *,
        level: str | None = None,
        actor_filter: str | None = None,
        surface: str | None = None,
        project: str | None = None,
        since_seq: int | None = None,
        until_seq: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """五维筛选 + 分面计数 + 当前序号水位。

        筛选与脱敏**全部委托** ``LogService.query``——不在这里重写一遍规则：
        级别词表、脱敏键表都是可配置项（``FY_QUALITY_LEVEL_RULES``），复制一份
        就会和真源漂移，而漂移的方向通常是「更宽松」，即漏脱敏。

        额外补两样面板真正需要、而 ``query`` 不给的东西：
        * ``head_seq``：该 owner 当前最大序号。客户端拿它当 SSE 起点，
          免得「先开流、后查历史」之间漏掉一段。
        * ``facets``：各级别 / 各 actor 的计数，面板上的过滤条直接渲染它。
        """
        out = self.logs.query(
            actor,
            level=level,
            actor_filter=actor_filter,
            surface=surface,
            project=project,
            since_seq=since_seq,
            until_seq=until_seq,
            limit=limit,
            offset=offset,
        )
        out["head_seq"] = self._head_seq(actor)
        out["facets"] = self._facets(actor)
        return out

    def _owner_identity(self, actor: Actor) -> str | None:
        return AuditService.identity_of(actor)

    def _head_seq(self, actor: Actor) -> int:
        identity = self._owner_identity(actor)
        if identity is None:
            return 0
        return int(
            self.session.execute(
                sa.select(sa.func.coalesce(sa.func.max(AuditEvent.seq), 0)).where(
                    AuditEvent.actor == identity
                )
            ).scalar_one()
        )

    def _facets(self, actor: Actor) -> dict[str, Any]:
        """按级别与按 actor 的计数，供面板的过滤条直接渲染。"""
        identity = self._owner_identity(actor)
        if identity is None:
            return {"levels": {}, "actors": {}, "total": 0}
        rows = self.session.execute(
            sa.select(AuditEvent.action, AuditEvent.actor).where(
                AuditEvent.actor == identity
            )
        ).all()
        rules = level_rules()
        by_level: dict[str, int] = {}
        by_actor: dict[str, int] = {}
        for action, row_actor in rows:
            lv = classify(action, rules)
            by_level[lv] = by_level.get(lv, 0) + 1
            by_actor[row_actor] = by_actor.get(row_actor, 0) + 1
        return {"levels": by_level, "actors": by_actor, "total": len(rows)}

    async def stream_logs(
        self,
        actor: Actor,
        *,
        since_seq: int | None = None,
        level: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_frames: int = MAX_STREAM_FRAMES,
        max_idle_polls: int = MAX_STREAM_IDLE_POLLS,
    ) -> AsyncIterator[str]:
        """把新到的审计帧按 SSE 推给面板。

        🔴 **为什么是轮询而不是接进 ``runtime.sse.bus``**：总线是给「执行过程中主动
        发布的领域事件」用的（stage 变更、画布事件），而 ``audit_events`` 是由
        ``AuditService.append`` 落库的副作用——要让它上总线就得改 ``services/audit.py``，
        那是共享文件、不在本包所有权内。改共享文件去换一个能轮询解决的延迟，不划算。
        代价写明：新增可见延迟 ≤ ``poll_interval``（默认 2 秒）。真要端到端实时，
        该做的是让 ``AuditService`` 发事件，那是独立一刀。

        ``since_seq`` 缺省为当前水位（只推**今后**新增的）；客户端要补历史就显式传。
        """
        identity = self._owner_identity(actor)
        if identity is None:
            # 没有身份就没有可推的东西；让连接安静地只发心跳，别假装有数据。
            cursor = 0
        else:
            cursor = self._head_seq(actor) if since_seq is None else max(0, int(since_seq))
        rules = level_rules()
        sent = 0
        idle = 0
        while sent < max_frames and idle < max_idle_polls:
            rows = self.session.execute(
                sa.select(AuditEvent)
                .where(AuditEvent.actor == identity, AuditEvent.seq > cursor)
                .order_by(AuditEvent.seq.asc())
                .limit(50)
            ).scalars().all() if identity is not None else []
            for row in rows:
                cursor = row.seq
                row_level = classify(row.action, rules)
                if level is not None and row_level != level:
                    continue
                payload: dict[str, Any] = {
                    "seq": row.seq,
                    # 与 ``logs.query`` 保持一致：不编造墙钟时间。
                    "at": None,
                    "level": row_level,
                    "actor": row.actor,
                    "action": row.action,
                    "target": row.target,
                    "details": row.details or {},
                }
                cleaned, _hits = redact(payload)
                sent += 1
                yield _sse(row.seq, row.action, cleaned)
                if sent >= max_frames:
                    # 跳出内层 for，让下面统一的 ``event: end`` 收尾帧也能发出去。
                    # 直接 return 的话客户端只看到流凭空断掉，分不清是
                    # 「帧预算用完」还是「网络断了」。
                    break
            if rows:
                idle = 0
            else:
                idle += 1
            if sent >= max_frames:
                break
            await asyncio.sleep(poll_interval)
            yield ": keepalive\n\n"
        # 收尾帧：明说「本轮到此为止」和为什么，客户端据此决定是否重连。
        # 静默断开会让前端分不清「流结束了」和「网络断了」。
        end_reason = (
            "idle" if idle >= max_idle_polls else "max_frames"
        )
        yield (
            "event: end\n"
            f'data: {{"reason": "{end_reason}", "frames": {sent}, '
            f'"idle_polls": {idle}}}\n\n'
        )

    # ------------------------------------------------------------------
    # 需求 A-可观测-02 · 错误跳 trace
    # ------------------------------------------------------------------
    def trace_for(self, actor: Actor, seq: int) -> dict[str, Any]:
        """从一条审计帧跳到它的关联事件。关联依据只有两种，都说清楚。

        * ``basis="message"`` —— ``details["message_id"]`` 命中。真关联：
          ``AuditService.frames_for_message`` 与 SSE 的 ``inject_message_id``
          都在用这个字段串帧。
        * ``basis="entity"`` —— 帧的 ``target`` 或 details 里能认出
          task / canvas instance / team id，于是去各自事件表取**真实时间戳**的行。
        * ``basis="none"`` —— 两种都不成立。返回空数组加 ``reason``，**不给空壳**。
        """
        identity = self._owner_identity(actor)
        if identity is None:
            raise PermissionDenied("owner_required", "trace lookup requires an owner actor")
        frame = self.session.execute(
            sa.select(AuditEvent).where(AuditEvent.actor == identity, AuditEvent.seq == seq)
        ).scalar_one_or_none()
        if frame is None:
            raise NotFound("log_frame_not_found", f"audit frame seq={seq} not found")

        details = frame.details or {}
        message_id = details.get("message_id")
        if isinstance(message_id, str) and message_id:
            frames = self.audit.frames_for_message(actor, message_id) if self.audit else []
            related = [
                {
                    "kind": "audit_frame",
                    "seq": f.seq,
                    "at": None,  # 审计帧无墙钟时间：不编造
                    "action": f.action,
                    "target": f.target,
                }
                for f in frames
            ]
            return {
                "seq": seq,
                "action": frame.action,
                "basis": "message",
                "correlation_key": message_id,
                "items": related,
                "total": len(related),
                "note": "关联依据 details.message_id：既有的问-帧串联字段",
                "reason": None,
                "trace_id": None,
            }

        entities = self._entities_of(details, frame.target)
        if entities:
            related: list[dict[str, Any]] = []
            # 🔴 逐个 ``.get()``：只认出了 team_id 而没有 task_id 是**正常**情况
            # （一帧完全可能只提到团队）。写成 ``entities["task_id"]`` 会直接 KeyError,
            # 把「关联得上但只关联到一部分」变成 500。
            if entities.get("task_id"):
                related.extend(self._task_events(actor, entities["task_id"]))
            if entities.get("canvas_instance_id"):
                related.extend(self._canvas_events(actor, entities["canvas_instance_id"]))
            if entities.get("team_id"):
                related.extend(self._team_events(actor, entities["team_id"]))
            related.sort(key=lambda r: (r.get("at") or "", r["kind"], r.get("seq", 0)))
            return {
                "seq": seq,
                "action": frame.action,
                "basis": "entity",
                "correlation_key": next(iter(entities.values())),
                "entities": entities,
                "items": related,
                "total": len(related),
                "note": (
                    "关联依据实体 id（task / canvas instance / team）——"
                    "这些表有真实 created_at；本仓没有跨事件的 span 关联 id"
                ),
                "reason": None,
                "trace_id": None,
            }

        return {
            "seq": seq,
            "action": frame.action,
            "basis": "none",
            "correlation_key": None,
            "entities": entities,
            "items": [],
            "total": 0,
            "note": (
                "这帧既没有 details.message_id，target/details 里也认不出 "
                "task/canvas/team id。返回空数组是真结论，不是加载失败——"
                "audit_events 没有 trace_id 列，本仓也不存在跨事件的 span 关联 id，"
                "补一个假关联会让面板按一个恒不匹配的数字跳转"
            ),
            "reason": "no_correlation_key",
            "trace_id": None,
        }

    @staticmethod
    def _entities_of(details: dict[str, Any], target: str | None) -> dict[str, str]:
        """从 details 与 target 里认出实体 id。

        target 的约定形如 ``task:<id>`` / ``canvas-instance:<id>`` / ``team:<id>``
        （``services/quality/logs.py:161-169`` 的 ``_project_of`` 也按 ``:`` 切 target）。
        """
        found: dict[str, str] = {}
        pairs = {
            "task_id": ("task_id", "task"),
            "canvas_instance_id": ("canvas_instance_id", "instance_id", "canvas_instance"),
            "team_id": ("team_id", "team"),
        }
        # details 里的键是调用方写的，同样归一化再比对
        for field, keys in pairs.items():
            for key in details:
                if isinstance(key, str) and key.replace("-", "_").lower() in keys:
                    val = details[key]
                    if isinstance(val, str) and val:
                        found[field] = val
                        break
        if target and ":" in target:
            prefix, _, value = target.partition(":")
            if value:
                # target 前缀在真实数据里两种写法都有（``canvas-instance:`` /
                # ``canvas_instance:``），所以把 ``-`` 归一成 ``_`` 再比对。
                # 只比对归一后的形式，避免为每种写法各加一个别名。
                normalized = prefix.replace("-", "_").lower()
                for field, keys in pairs.items():
                    if normalized in keys and field not in found:
                        found[field] = value
        return found

    def _task_events(self, actor: Actor, task_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            sa.select(TaskEvent)
            .where(TaskEvent.owner_id == actor.owner_id, TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.asc())
        ).scalars().all()
        return [
            {
                "kind": "task_event",
                "seq": None,
                "at": _iso(r.created_at),
                "event": r.kind,
                "from_status": r.from_status,
                "to_status": r.to_status,
                "detail": r.detail,
            }
            for r in rows
        ]

    def _canvas_events(self, actor: Actor, instance_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            sa.select(CanvasEvent)
            .join(CanvasInstance, CanvasInstance.id == CanvasEvent.instance_id)
            .where(
                CanvasInstance.owner_id == actor.owner_id,
                CanvasEvent.instance_id == instance_id,
            )
            .order_by(CanvasEvent.seq.asc())
        ).scalars().all()
        return [
            {
                "kind": "canvas_event",
                "seq": r.seq,
                "at": _iso(r.created_at),
                "event": r.event_type,
                "agent_id": r.agent_id,
                "task_id": r.task_id,
                # 带上但不当作关联 id 用——它是每条事件现生成的，不跨事件共享。
                "event_trace_id": r.trace_id,
            }
            for r in rows
        ]

    def _team_events(self, actor: Actor, team_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            sa.select(TeamEvent)
            .join(TeamDefinition, TeamDefinition.id == TeamEvent.team_id)
            .where(TeamDefinition.owner_id == actor.owner_id, TeamEvent.team_id == team_id)
            .order_by(TeamEvent.seq.asc())
        ).scalars().all()
        return [
            {
                "kind": "team_event",
                "seq": r.seq,
                "at": _iso(r.created_at),
                "event": r.event_type,
                "agent_instance_id": r.agent_instance_id,
                "task_id": r.task_id,
                "source": r.source,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 需求 A-可观测-03 · 性能监控（整体指标）
    # ------------------------------------------------------------------
    def performance(self, actor: Actor, *, days: int = 7) -> dict[str, Any]:
        """整体指标。只统计**有真实墙钟时间戳**的表。

        数据源与各自的时钟：
        * ``task_events.created_at`` —— 阶段流转，真实推进耗时
        * ``canvas_events.created_at`` —— 画布执行
        * ``dispatch_records.created_at`` → ``completed_at`` —— 每 worker 端到端时长
        * ``audit_events`` —— 按 ``seq``（**非**时间）统计日志量

        没有持久化的 latency 表，所以不造一个「全站平均延迟」：能给的只有上面
        这些**被观测到的**事件窗口，样本不够就报 ``partial``。
        """
        actor.require_authenticated()
        if days < 1 or days > 90:
            raise ValidationFailed("days_invalid", "days 必须是 1..90 的整数")
        since = utcnow() - timedelta(days=days)
        owner = actor.owner_id or ""

        stage_durations = self._stage_durations(owner, since)
        dispatch_durations = self._dispatch_durations(owner, since)
        canvas_counts = self._canvas_counts(owner, since)
        completion = self._completion_series(owner, since, days)

        all_durations = stage_durations + dispatch_durations
        return {
            "window_days": days,
            "since": _iso(since),
            "overall": {
                "stage_duration_ms": _sample_gate(stage_durations, what="阶段流转"),
                "dispatch_duration_ms": _sample_gate(
                    dispatch_durations, what="worker 端到端"
                ),
                "combined_duration_ms": _sample_gate(all_durations, what="全部事件窗口"),
            },
            "tasks": self._task_counts(owner),
            "agents": self._agent_state_counts(owner),
            "throughput": completion,
            "canvas": canvas_counts,
            "logs": self._facets(actor),
            "time_axis": "created_at（本聚合）/ seq（日志量）",
            "honesty": {
                "no_persisted_latency_table": (
                    "本仓没有持久化 latency 表，因此不提供「全站平均延迟」；"
                    "此处只有被上述事件窗口观测到的时长"
                ),
                "percentile_gate": (
                    f"样本 < {MIN_SAMPLES_FOR_PERCENTILE} 条时百分位为 null 且 "
                    "partial=true"
                ),
            },
        }

    def _stage_durations(
        self, owner: str, since: datetime, *, only_task: str | None = None
    ) -> list[float]:
        """相邻两次阶段流转之间的耗时（毫秒），按任务内配对。

        只配对**同一个 task 内**相邻两条 ``task_events``——跨任务配对没有意义。
        ``only_task`` 用于剩余步数估计：只看那一个任务的历史。
        """
        stmt = sa.select(TaskEvent.task_id, TaskEvent.created_at).where(
            TaskEvent.owner_id == owner, TaskEvent.created_at >= since
        )
        if only_task is not None:
            stmt = stmt.where(TaskEvent.task_id == only_task)
        rows = self.session.execute(
            stmt.order_by(TaskEvent.task_id.asc(), TaskEvent.created_at.asc())
        ).all()
        out: list[float] = []
        last: dict[str, datetime] = {}
        for task_id, at in rows:
            if at is None:
                continue
            prev = last.get(task_id)
            if prev is not None:
                delta = (at - prev).total_seconds() * 1000.0
                if delta >= 0:
                    out.append(delta)
            last[task_id] = at
        return out

    def _dispatch_durations(self, owner: str, since: datetime) -> list[float]:
        rows = self.session.execute(
            sa.select(DispatchRecord.created_at, DispatchRecord.completed_at)
            .join(CanvasInstance, CanvasInstance.id == DispatchRecord.instance_id)
            .where(CanvasInstance.owner_id == owner, DispatchRecord.created_at >= since)
        ).all()
        out: list[float] = []
        for created, completed in rows:
            if created is None or completed is None:
                continue
            delta = (completed - created).total_seconds() * 1000.0
            if delta >= 0:
                out.append(delta)
        return out

    def _canvas_counts(self, owner: str, since: datetime) -> dict[str, Any]:
        rows = self.session.execute(
            sa.select(CanvasEvent.event_type)
            .join(CanvasInstance, CanvasInstance.id == CanvasEvent.instance_id)
            .where(CanvasInstance.owner_id == owner, CanvasEvent.created_at >= since)
        ).all()
        counts: dict[str, int] = {}
        for (event_type,) in rows:
            counts[event_type] = counts.get(event_type, 0) + 1
        return {"by_event_type": counts, "total": len(rows)}

    def _completion_series(self, owner: str, since: datetime, days: int) -> dict[str, Any]:
        """每日完成数。**只填有事件的那天**，缺的日子不补 0。

        补 0 会让折线看起来像「那天一个都没完成」，而真实情况是「那天没有数据」。
        缺失日以 ``partial: true`` + ``sampled_days`` 说明，和 ``burndown`` 一致。
        """
        rows = self.session.execute(
            sa.select(TaskEvent.created_at)
            .where(
                TaskEvent.owner_id == owner,
                TaskEvent.kind == "status",
                TaskEvent.to_status == "completed",
                TaskEvent.created_at >= since,
            )
        ).all()
        by_day: dict[str, int] = {}
        for (at,) in rows:
            if at is None:
                continue
            by_day[_iso(at)[:10]] = by_day.get(_iso(at)[:10], 0) + 1
        expected = days
        return {
            "unit": "completed_tasks_per_day",
            "actual": [{"date": d, "completed": n} for d, n in sorted(by_day.items())],
            "sampled_days": len(by_day),
            "expected_days": expected,
            "partial": len(by_day) < expected,
            "missing_note": (
                f"{expected - len(by_day)} 天没有任何完成事件；这些天**不补 0**"
                "（那是「无数据」，不是「零完成」）"
                if len(by_day) < expected
                else None
            ),
        }

    def _task_counts(self, owner: str) -> dict[str, Any]:
        rows = self.session.execute(
            sa.select(Task.status, sa.func.count(Task.id)).where(Task.owner_id == owner)
            .group_by(Task.status)
        ).all()
        by_status = {status: int(n) for status, n in rows}
        return {"by_status": by_status, "total": sum(by_status.values())}

    def _agent_state_counts(self, owner: str) -> dict[str, Any]:
        rows = self.session.execute(
            sa.select(AgentInstance.state, sa.func.count(AgentInstance.id))
            .join(TeamDefinition, TeamDefinition.id == AgentInstance.team_id)
            .where(TeamDefinition.owner_id == owner)
            .group_by(AgentInstance.state)
        ).all()
        by_state = {state: int(n) for state, n in rows}
        return {"by_state": by_state, "total": sum(by_state.values())}

    # ------------------------------------------------------------------
    # 需求 A-可观测-03 · 单 Agent 统计
    # ------------------------------------------------------------------
    def agent_stats(
        self, actor: Actor, *, team_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """逐个 Agent 的状态、步数、预算与活动窗口。

        owner 收敛走 ``team_definitions.owner_id``（``agent_instances`` 自身没有
        owner 列）。活动窗口取该成员 ``team_events`` 的首末真实时间戳——不是
        ``updated_at``，后者被任何一次写都会刷新，拿它当「工作时长」是虚的。
        """
        actor.require_owner()
        if limit < 1 or limit > 500:
            raise ValidationFailed("limit_invalid", "limit 必须是 1..500 的整数")
        stmt = (
            sa.select(AgentInstance, TeamDefinition)
            .join(TeamDefinition, TeamDefinition.id == AgentInstance.team_id)
            .where(TeamDefinition.owner_id == actor.owner_id)
        )
        if team_id:
            stmt = stmt.where(AgentInstance.team_id == team_id)
        pairs = self.session.execute(stmt.order_by(AgentInstance.created_at.asc()).limit(limit)).all()

        items = []
        for member, team in pairs:
            events = self.session.execute(
                sa.select(TeamEvent.created_at, TeamEvent.event_type)
                .where(TeamEvent.agent_instance_id == member.id)
                .order_by(TeamEvent.created_at.asc())
            ).all()
            ats = [at for at, _ in events if at is not None]
            span_ms = (ats[-1] - ats[0]).total_seconds() * 1000.0 if len(ats) >= 2 else None
            items.append(
                {
                    "agent_instance_id": member.id,
                    "team_id": team.id,
                    "role": member.role,
                    "title": member.title,
                    "state": member.state,
                    "run_batch": member.run_batch,
                    "steps": member.steps,
                    "max_steps": member.max_steps,
                    "step_progress_pct": (
                        round(member.steps / member.max_steps * 100.0, 1)
                        if member.max_steps
                        else None
                    ),
                    "budget_reserved_usd": float(member.budget_reserved_usd or 0),
                    "budget_spent_usd": float(member.budget_spent_usd or 0),
                    "blocked_reason": member.blocked_reason or "",
                    "event_count": len(events),
                    "active_from": _iso(ats[0]) if ats else None,
                    "active_to": _iso(ats[-1]) if ats else None,
                    # 样本不足（<2 个事件）就是 None，不是 0
                    "active_span_ms": span_ms,
                    "active_span_partial": span_ms is None,
                }
            )
        return {
            "items": items,
            "agents": items,  # 前端兼容：读 agents 键
            "total": len(items),
            "limit": limit,
            "team_filter": team_id,
            "note": (
                "active_span_ms 取该成员 team_events 的首末时间戳差；"
                "少于 2 个事件时为 null（不是 0）——没有跨度可言"
            ),
        }

    # ------------------------------------------------------------------
    # 需求 A-成本仪表盘-02 · 进度条（当前步 / 总步 / 预计剩余）
    # ------------------------------------------------------------------
    def cost_progress(
        self, actor: Actor, *, task_id: str | None = None, team_id: str | None = None
    ) -> dict[str, Any]:
        """进度条三段：当前步、总步、预计剩余；外加费用占用。

        「预计剩余」需要速度，速度来自 ``task_events`` 里同任务的真实阶段耗时。
        规则：
        * 样本够（>= ``MIN_SAMPLES_FOR_PERCENTILE``）→ 给剩余**步数**估计；
        * 样本不够 → ``remaining_steps_estimate: null`` + ``partial: true`` + 原因。

        绝不拿 ``max_steps - steps`` 冒充「预计剩余」——那是**上限余量**，
        不是预测；两者混为一谈会让面板在最需要说实话的时候说漂亮话。
        """
        actor.require_owner()
        if task_id is None and team_id is None:
            raise ValidationFailed(
                "target_required",
                "需要 task_id 或 team_id 之一：进度条要有明确的进度载体，"
                "给全局一个百分比没有意义",
            )

        limits = getattr(self.budget, "limits", None)
        per_task = getattr(limits, "per_task_usd", None)

        if task_id is not None:
            targets = self._task_targets(actor, task_id)
        else:
            targets = self._team_targets(actor, str(team_id))

        bars: list[dict[str, Any]] = []
        for item in targets:
            step_total = int(item.get("max_steps") or 0)
            step_now = int(item.get("steps") or 0)
            remaining = self._estimate_remaining(actor, item, step_now, step_total)
            spent = self._spent_for_tasks([item["task_id"]]) if item.get("task_id") else None
            bars.append(
                {
                    **item,
                    "step_current": step_now,
                    "step_total": step_total,
                    # step_total=0 时给 None 而不是 0：那不是「0% 完成」，
                    # 是「这条记录没有声明步数上限」，两者不该共用一个数。
                    "step_progress_pct": (
                        round(min(step_now, step_total) / step_total * 100.0, 1)
                        if step_total > 0
                        else None
                    ),
                    "step_progress_partial": step_total <= 0,
                    # 剩余量是「上界余量 + 实测速率折算的时间区间 + 明写的假设」，
                    # 不是单一预测数。字段名刻意带 upper_bound / assumption。
                    "remaining": remaining,
                    "cost": {
                        "spent_usd": spent,
                        "reserved_usd": self._reserved_for_tasks([item["task_id"]])
                        if item.get("task_id")
                        else None,
                        "limit_usd": float(per_task) if per_task else None,
                        "usage_pct": (
                            round(float(spent) / float(per_task) * 100.0, 2)
                            if spent is not None and per_task and Decimal(per_task) > 0
                            else None
                        ),
                        "partial": spent is None,
                    },
                }
            )
        return {
            "items": bars,
            "total": len(bars),
            "target": {"task_id": task_id, "team_id": team_id},
            "currency": "USD",
            "honesty": {
                "no_padded_remaining": (
                    "remaining 给的是区间 + assumption，不是单一预测："
                    "上界余量 = max_steps - steps（精确但只是余量），"
                    "时间区间由实测步均耗时折算且下界为 0。"
                    "样本不足时全部时间字段为 null"
                ),
                "no_zero_fabrication": (
                    "step_progress_pct 为 null 表示该记录没有步数上限，"
                    "不是 0% 完成"
                ),
            },
        }

    def _estimate_remaining(
            self, actor: Actor, item: dict[str, Any], step_now: int, step_total: int
        ) -> dict[str, Any]:
            """剩余时间估计。返回一个**区间**加明写的假设，不返回一个假装确定的数。

            能说的和不能说的：
            * ``remaining_steps_upper_bound`` = ``max_steps - steps``：这是**上界余量**，
              精确可算，但它不是预测——任务通常用不满 ``max_steps``。
            * ``estimated_remaining_ms``：由**实测步均耗时** × 上界余量得出。
              它成立的前提是「本任务会走完剩下的每一步」，所以必须连假设一起返回，
              并且给下界 0（任务现在就收工也是可能的）。
            * 没有实测速率（样本不足 / 刚起步）→ 两个时间字段都是 ``null``。

            把上界余量直接标成「预计剩余」是这类面板最常见的撒谎方式：数字漂亮，
            含义是假的。
            """
            empty = {
                "remaining_steps_upper_bound": None,
                "estimated_remaining_ms": None,
                "lower_bound_ms": None,
                "upper_bound_ms": None,
                "partial": True,
                "assumption": None,
                "reason": None,
            }
            if step_total <= 0:
                empty["reason"] = "该记录没有声明 max_steps，无法给出剩余量"
                return empty
            if step_now >= step_total:
                return {
                    "remaining_steps_upper_bound": 0,
                    "estimated_remaining_ms": 0,
                    "lower_bound_ms": 0,
                    "upper_bound_ms": 0,
                    "partial": False,
                    "assumption": "已达步数上限",
                    "reason": None,
                }

            remaining_steps = step_total - step_now
            task_id = item.get("task_id")
            _ = item.get("agent_instance_id")

            if not task_id:
                # 团队成员级：team_events 一次事件可推进多步，与 max_steps 不一一对应，
                # 折算步均耗时会造数。只报上界余量。
                return {
                    "remaining_steps_upper_bound": remaining_steps,
                    "estimated_remaining_ms": None,
                    "lower_bound_ms": None,
                    "upper_bound_ms": None,
                    "partial": True,
                    "assumption": None,
                    "reason": (
                        "成员级事件与 max_steps 不一一对应（一次事件可推进多步），"
                        "折算步均耗时会产生假数据；只给上界余量"
                    ),
                }

            durations = self._stage_durations(
                actor.owner_id or "",
                datetime.min.replace(tzinfo=timezone.utc),
                only_task=task_id,
            )
            if len(durations) < MIN_SAMPLES_FOR_PERCENTILE:
                return {
                    "remaining_steps_upper_bound": remaining_steps,
                    "estimated_remaining_ms": None,
                    "lower_bound_ms": None,
                    "upper_bound_ms": None,
                    "partial": True,
                    "assumption": None,
                    "reason": (
                        f"该任务只有 {len(durations)} 段阶段耗时，少于 "
                        f"{MIN_SAMPLES_FOR_PERCENTILE} 段最低门槛，无法测步均耗时"
                    ),
                }

            median = _percentile(durations, 50)
            if not median or median <= 0:
                return {
                    "remaining_steps_upper_bound": remaining_steps,
                    "estimated_remaining_ms": None,
                    "lower_bound_ms": None,
                    "upper_bound_ms": None,
                    "partial": True,
                    "assumption": None,
                    "reason": "历史阶段耗时为 0 或缺失，无法据此折算",
                }

            # 步均耗时 = 已观测总耗时 / 已用步数。
            # 刻意**不**用「总耗时 / 总步数」：那会把启动开销摊进分母，系统性低估。
            elapsed_ms = sum(durations)
            if step_now <= 0 or elapsed_ms <= 0:
                return {
                    "remaining_steps_upper_bound": remaining_steps,
                    "estimated_remaining_ms": None,
                    "lower_bound_ms": None,
                    "upper_bound_ms": None,
                    "partial": True,
                    "assumption": None,
                    "reason": "尚未观察到任何推进速率（已用步数为 0 或无耗时样本）",
                }
            per_step_ms = elapsed_ms / step_now
            return {
                "remaining_steps_upper_bound": remaining_steps,
                "estimated_remaining_ms": round(per_step_ms * remaining_steps, 1),
                "lower_bound_ms": 0.0,
                "upper_bound_ms": round(per_step_ms * remaining_steps, 1),
                "partial": False,
                "assumption": (
                    f"按已实测步均耗时 {round(per_step_ms, 1)} ms（= 已观测 "
                    f"{round(elapsed_ms, 1)} ms ÷ 已用 {step_now} 步）× 上界余量 "
                    f"{remaining_steps} 步；前提是本任务走完剩余每一步。"
                    "下界 0 表示现在就收工也可能。"
                ),
                "reason": None,
            }

    def _spent_for_tasks(self, task_ids: list[str]) -> float | None:
        if not task_ids:
            return None
        total = self.session.execute(
            sa.select(sa.func.coalesce(sa.func.sum(BudgetReservation.amount), 0)).where(
                BudgetReservation.task_id.in_(task_ids),
                BudgetReservation.state.in_(_BUDGET_LIVE_STATES),
            )
        ).scalar_one()
        return float(total or 0)

    def _reserved_for_tasks(self, task_ids: list[str]) -> float | None:
        if not task_ids:
            return None
        total = self.session.execute(
            sa.select(sa.func.coalesce(sa.func.sum(BudgetReservation.amount), 0)).where(
                BudgetReservation.task_id.in_(task_ids),
                BudgetReservation.state == "reserved",
            )
        ).scalar_one()
        return float(total or 0)

    def _task_targets(self, actor: Actor, task_id: str) -> list[dict[str, Any]]:
        task = self.session.execute(
            sa.select(Task).where(Task.owner_id == actor.owner_id, Task.id == task_id)
        ).scalar_one_or_none()
        if task is None:
            raise NotFound("task_not_found", f"Task {task_id} not found")
        return [
            {
                "kind": "task",
                "task_id": task.id,
                "agent_instance_id": None,
                "team_id": None,
                "role": None,
                "title": task.goal or "",
                "state": task.status,
                "stage": task.stage,
                # 进度条的三段全靠这两个字段。缺了它们，``cost_progress`` 会把
                # 每个任务都算成 0 步——「没有步数」和「一步没走」在页面上
                # 长得一模一样，所以必须原样带上来。
                "steps": task.steps,
                "max_steps": task.max_steps,
            }
        ]

    def _team_targets(self, actor: Actor, team_id: str) -> list[dict[str, Any]]:
        team = self.session.execute(
            sa.select(TeamDefinition).where(
                TeamDefinition.owner_id == actor.owner_id, TeamDefinition.id == team_id
            )
        ).scalar_one_or_none()
        if team is None:
            raise NotFound("team_not_found", f"Team {team_id} not found")
        members = self.session.execute(
            sa.select(AgentInstance)
            .where(AgentInstance.team_id == team_id)
            .order_by(AgentInstance.role.asc())
        ).scalars().all()
        return [
            {
                "kind": "agent_member",
                "task_id": m.parent_task_id,
                "agent_instance_id": m.id,
                "team_id": team_id,
                "role": m.role,
                "title": m.title,
                "state": m.state,
                "stage": None,
                "steps": m.steps,
                "max_steps": m.max_steps,
            }
            for m in members
        ]


def _sse(seq: int, event: str, data: dict[str, Any]) -> str:
    import json

    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return f"id: {seq}\nevent: log\ndata: {payload}\n\n"


__all__ = ["ObservabilityService"]
