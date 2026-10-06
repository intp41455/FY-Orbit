"""P5 · 定时 / 周期任务调度（A-竞品借鉴-03）+ 渐进式 Harness 路线图（A-Agent运行时-15）。

需求现场
--------

A-竞品借鉴-03「定时 / 周期任务调度」：需要有「到点自动跑」「每 N 分钟跑一次」
「每天 9 点跑一次」这类**时间驱动**的调度能力。此前 ``UnifiedScheduler`` 只支持
**事件驱动**（有人 submit 才跑），没有时间驱动入口。

A-Agent运行时-15「渐进式 Harness 建造路线图（s01→s20）」：把「按时间/周期推进
建造阶段」流程化——每一阶（stage）有触发条件与验收门，逐阶推进而非一次梭哈。
本模块用同一套 job 机制承载「阶段推进」：一个 stage 就是一个带验收门的 job。

依赖选择（附录 C 已拍板）
-------------------------

本模块用 **APScheduler**（``pyproject.toml`` 已显式声明 ``apscheduler>=3.10,<4``）——
附录 C 把本条列为 **D 类**：「用 APScheduler / cron 库（**非自研 cron**）」。
因此**不自己写 cron 表达式解析器**（重复造轮子且易错）。

设计约束
--------

1. **不自研 cron**——解析与触发交给 APScheduler；本模块只做「**桥接**」：
   把到点的触发转成 ``UnifiedScheduler.submit()``，让定时任务与事件任务走**同一
   个调度中心**（不产生第二套并发/回收体系，PRD 红线）。
2. **零外部依赖**——默认 ``MemoryJobStore`` + ``BackgroundScheduler``，进程内即可
   用，不引入 broker/DB。需要持久化时调用方传自己的 jobstore。
3. **诚实边界**——进程退出即停（内存 jobstore 不跨重启）；需要跨重启的周期任务
   应把「上次跑到哪」落主库，由启动补偿扫描接续（沿用 ``recovery.startup_autoresume``
   同一思路，本模块不假装持久）。
4. **每次触发都留痕**——命中后写审计帧，可反查「这个周期任务跑过几次、哪次失败」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from ...services.actor import Actor
from ...services.audit import AuditService
from .core import DispatchRequest, UnifiedScheduler

#: job 类型标识
TRIGGER_INTERVAL = "interval"
TRIGGER_CRON = "cron"

KNOWN_TRIGGER_KINDS = (TRIGGER_INTERVAL, TRIGGER_CRON)


@dataclass
class CronJobSpec:
    """一个定时/周期任务的定义。

    ⚠️ ``request`` 是**模板**而非复用对象：每次触发都从它派发一个**全新**
    ``DispatchRequest``（见 :meth:`build_request`）。原因——``UnifiedScheduler``
    在派发时会往 request 上写 ``task_id``；若跨次复用同一对象，第二次触发就会
    带着上一次的 task_id 提交，被调度中心判为「重复提交」而永久拒收。
    这是本模块必须固化的正确性要点（由单测 test_interval_job_fires_repeatedly 守）。
    """

    job_id: str
    #: 目标派发请求**模板**（到点即由此派生新请求交给 UnifiedScheduler.submit）
    request: DispatchRequest
    #: interval：每 seconds 秒跑一次（必须 > 0）
    #: cron：用 5 段 cron 表达式（分 时 日 月 周）
    trigger_kind: str = TRIGGER_INTERVAL
    seconds: int = 60
    cron_expr: str = ""
    description: str = ""
    #: 启动时是否立刻先跑一次（默认否：等第一个触发点）
    run_immediately: bool = False

    def validate(self) -> None:
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must be non-empty")
        if self.trigger_kind not in KNOWN_TRIGGER_KINDS:
            raise ValueError(
                f"unknown trigger_kind {self.trigger_kind!r}; "
                f"KNOWN_TRIGGER_KINDS: {', '.join(KNOWN_TRIGGER_KINDS)}"
            )
        if self.trigger_kind == TRIGGER_INTERVAL and self.seconds <= 0:
            raise ValueError("seconds must be > 0 for interval triggers")
        if self.trigger_kind == TRIGGER_CRON and not self.cron_expr.strip():
            raise ValueError("cron_expr is required for cron triggers")

    def build_request(self) -> DispatchRequest:
        """派生一次触发用的**全新**请求（不复用模板对象，见类 docstring）。

        ``task_id`` 留空——由调度中心自行分配唯一 id；每次触发都是独立任务。
        """
        tpl = self.request
        return DispatchRequest(
            channel=tpl.channel,
            action=tpl.action,
            payload=dict(tpl.payload or {}),
            priority=tpl.priority,
            timeout_seconds=tpl.timeout_seconds,
            requested_capability=tpl.requested_capability,
            meta={**dict(tpl.meta or {}), "cron_job_id": self.job_id},
            task_id="",
        )

    def build_trigger(self):
        """构造 APScheduler 触发器（不校验 cron 语法——交给 APScheduler 报错）。"""
        self.validate()
        if self.trigger_kind == TRIGGER_INTERVAL:
            return IntervalTrigger(seconds=self.seconds)
        # 5 段 cron：分 时 日 月 周
        return CronTrigger.from_crontab(self.cron_expr.strip())


@dataclass
class HarnessStage:
    """A-Agent运行时-15 渐进式 Harness 的一阶（s01→s20）。

    每阶都有：编号（sNN）、名称、触发条件描述、验收门（``acceptance``）。
    ``advance_or_gate`` 决定「这一阶过了没有」——门未过就不许进下一阶
    （与 ``parallel_policy.StopLossGuard`` 的硬上限精神一致：不许跳过门）。
    """

    stage_id: str            # "s01".."s20"
    name: str
    trigger: str = ""        # 触发条件（人读描述）
    acceptance: str = ""     # 验收门（人读描述）
    done: bool = False


def default_harness_roadmap() -> list[HarnessStage]:
    """s01→s20 的默认路线图骨架（框架 + 命名，具体门的判定由调用方注入）。

    说明（对齐附录 C 的风险提示）：附录 C 指出「第 2 条 s01→s20 路线图体量弹性大，
    建议先与主控确认交付边界（是『框架 + 若干样例』还是『全 20 阶』）」。本实现
    交付的是**可扩展的框架 + 20 阶占位骨架**，每阶的验收门文本明确标注「待细化」
    以示诚实——不冒充「20 阶全部定义完毕」。
    """
    names = [
        "单步工具调用", "工具失败重试", "工具结果校验", "两步串联",
        "条件分支", "循环收敛", "状态落盘", "断点续跑",
        "多工具扇出", "结果归并", "人工确认门", "成本预算闸",
        "并行分支执行", "分支结果对比", "失败隔离", "自动降级",
        "自审校验", "独立质检", "目标对齐检查", "收口交付",
    ]
    return [
        HarnessStage(
            stage_id=f"s{i + 1:02d}",
            name=names[i],
            trigger="待细化（每阶触发条件）",
            acceptance="待细化（每阶验收门）",
        )
        for i in range(20)
    ]


class CronScheduler:
    """定时/周期调度桥：APScheduler 到点 → ``UnifiedScheduler.submit``。

    与 ``UnifiedScheduler`` 的关系：本类**不**自己管并发/回收/优先级，只负责
    「时间 → 提交」的翻译；真正的执行编排仍是调度中心的职责。
    """

    def __init__(
        self,
        scheduler: UnifiedScheduler,
        *,
        session_factory: Callable[[], Session] | None = None,
        audit: AuditService | None = None,
        timezone: str = "UTC",
    ):
        self._scheduler = scheduler
        self._session_factory = session_factory
        self._audit = audit
        self._tz = timezone
        self._aps = BackgroundScheduler(timezone=timezone)
        self._specs: dict[str, CronJobSpec] = {}
        self._fired: list[dict[str, Any]] = []

    # -- 生命周期 ------------------------------------------------------------ #

    def start(self) -> None:
        """启动后台调度线程（幂等：已启动则不重复）。"""
        if not self._aps.running:
            self._aps.start()

    def shutdown(self, *, wait: bool = False) -> None:
        if self._aps.running:
            self._aps.shutdown(wait=wait)

    @property
    def running(self) -> bool:
        return bool(self._aps.running)

    # -- 注册 ---------------------------------------------------------------- #

    def add_job(self, spec: CronJobSpec, *, actor: Actor | None = None) -> CronJobSpec:
        """注册一个定时/周期任务（幂等：同 job_id 覆盖）。

        ``replace_existing`` 只在调度器**已启动**时才真正生效（APScheduler 的
        pending-job 行为），故此处先自启一次——保证「同 id 只留一个」对调用方
        始终成立，不因「注册早于 start」而留下两个待调度副本。
        """
        spec.validate()
        trigger = spec.build_trigger()

        def _fire() -> None:
            self._fire(spec)

        if not self._aps.running:
            self._aps.start()
        self._aps.add_job(
            _fire,
            trigger=trigger,
            id=spec.job_id,
            replace_existing=True,
            name=spec.description or spec.job_id,
            misfire_grace_time=None,  # 不丢触发（诚实边界：迟到的照跑）
        )
        self._specs[spec.job_id] = spec
        if actor is not None and self._audit is not None:
            self._audit_job(actor, "cron.job_registered", spec.job_id, {
                "trigger_kind": spec.trigger_kind,
                "seconds": spec.seconds,
                "cron_expr": spec.cron_expr,
            })
        if spec.run_immediately:
            self._fire(spec)
        return spec

    def remove_job(self, job_id: str, *, actor: Actor | None = None) -> bool:
        existed = False
        try:
            self._aps.remove_job(job_id)
            existed = True
        except Exception:  # noqa: BLE001 — job 不存在即视为未删
            existed = False
        self._specs.pop(job_id, None)
        if existed and actor is not None and self._audit is not None:
            self._audit_job(actor, "cron.job_removed", job_id, {})
        return existed

    def jobs(self) -> list[dict[str, Any]]:
        """列出已注册任务（含下次触发时间，便于观测）。"""
        out = []
        for j in self._aps.get_jobs():
            nrt = getattr(j, "next_run_time", None)
            out.append({
                "job_id": j.id,
                "name": j.name,
                "next_run_time": nrt.isoformat() if nrt else None,
            })
        return out

    # -- 触发桥接 ------------------------------------------------------------ #

    def _fire(self, spec: CronJobSpec) -> None:
        """到点回调：派生**新请求**提交到统一调度中心，并记录一次触发。

        ★ 每次调用 ``spec.build_request()`` 取一个全新 ``DispatchRequest``——
        绝不复用模板对象（否则第二次触发会带上一次的 task_id，被调度中心拒收）。
        """
        try:
            self._scheduler.submit(spec.build_request())
            entry = {"job_id": spec.job_id, "status": "submitted"}
        except Exception as exc:  # noqa: BLE001 — 无路由/过载等都在此如实记录
            entry = {"job_id": spec.job_id, "status": "submit_failed",
                     "error": f"{type(exc).__name__}: {exc}"}
        self._fired.append(entry)
        self._persist_fire(spec, entry)

    def _persist_fire(self, spec: CronJobSpec, entry: dict[str, Any]) -> None:
        """把一次触发写审计帧（若配置了 session_factory + audit）。"""
        if self._session_factory is None or self._audit is None:
            return
        try:
            with self._session_factory() as session:
                actor = Actor.service("cron-scheduler", "worker")
                self._audit.append(
                    actor, "cron.fired", spec.job_id,
                    {"status": entry.get("status"), "error": entry.get("error", "")[:200]},
                )
                session.commit()
        except Exception:  # noqa: BLE001 — 留痕失败不反噬调度主流程
            pass

    def _audit_job(self, actor: Actor, action: str, job_id: str, data: dict) -> None:
        if self._session_factory is None or self._audit is None:
            return
        try:
            with self._session_factory() as session:
                self._audit.append(actor, action, job_id, data)
                session.commit()
        except Exception:  # noqa: BLE001
            pass

    # -- 观测 ---------------------------------------------------------------- #

    def fire_log(self) -> list[dict[str, Any]]:
        """本进程内每次触发的流水（进程内，重启即清——诚实边界）。"""
        return list(self._fired)
