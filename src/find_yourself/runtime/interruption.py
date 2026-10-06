"""T6-A 中断分类与统一处置矩阵（六类场景 S1–S6，主控红线产物）。

设计约束（任务书 T6.4，违反即返工）：

1. 每类中断都有**唯一分类**、**落盘动作**与**恢复策略**（auto/confirm/manual）；
2. 恢复动作必须往 ``AuditService`` 哈希链挂帧——本模块的 ``record_interruption``
   / ``mark_resumed`` 是唯一的挂帧入口，散落的 try/except 不许各自为政；
3. ``classify_*`` 只做**检测**，不做处置；处置由 ``INTERRUPTION_PLAYBOOK``
   的 ``resume_policy`` 驱动（recovery 服务据此决定自动续作还是要人确认）；
4. 不可逆动作不许盲重试——沿用 ``services/outbox.py`` 的 ``unknown`` 态对账语义，
   本模块不改变任何重试行为（红线 7：``providers/base.py`` 的 retryable 分类是
   防重复计费护栏，不许为「看起来不断」而放宽）。

六类场景与任务书 T6.1 一一对应（S1–S6），缺一类即未交付。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session

from ..db.resilience_models import InterruptionEvent
from ..db.types import utcnow
from ..services.actor import Actor
from ..services.audit import AuditService


class InterruptionClass(str, Enum):
    """六类中断场景（T6.1）+ unknown。值即台账行 ``interruption_class``。"""

    RATE_LIMITED = "rate_limited"        # S1 模型 API 限流 / 配额耗尽
    STREAM_BROKEN = "stream_broken"      # S2 流式中途断流
    NETWORK_LOST = "network_lost"        # S3 网络信号中断 / 传输层不可达
    PROCESS_KILLED = "process_killed"    # S4 进程被杀 / 崩溃 / 机器重启
    PROVIDER_RESET = "provider_reset"    # S5 换 key / base_url / 模型
    AGENT_MISUSE = "agent_misuse"        # S6 AI / agent 误删误覆盖
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlaybookEntry:
    """一类中断的统一处置矩阵行。"""

    scene: str                 # "S1".."S6"（unknown 无编号）
    description: str
    #: 中断发生时的落盘动作（who/what）
    on_interrupt: str
    #: auto = 扫描到即可自动续作；confirm = 恢复前必须用户确认；manual = 只登记不自动续
    resume_policy: str
    resume_description: str


INTERRUPTION_PLAYBOOK: dict[str, PlaybookEntry] = {
    InterruptionClass.RATE_LIMITED.value: PlaybookEntry(
        scene="S1",
        description="模型 API 限流（429 / Retry-After / 配额耗尽）",
        on_interrupt="gateway 退避重试（honour Retry-After）；重试预算耗尽即挂起："
                     "分类落台账 + 已产出内容自动暂存 WorkStash + 审计挂帧，绝不静默失败",
        resume_policy="auto",
        resume_description="限流是暂时态：供应商指纹未变，扫描到即自动从断点续作"
                           "（只续剩余步骤，已完成步骤不重跑不重复计费）",
    ),
    InterruptionClass.STREAM_BROKEN.value: PlaybookEntry(
        scene="S2",
        description="SSE / 流式响应中途断开",
        on_interrupt="逐帧先落库后吐出（stream_segments），断流时已产出部分必定已在盘上；"
                     "分类落台账 + 片段可凭 message_id 取回",
        resume_policy="manual",
        resume_description="在飞生成无法重连到同一进程内生成器（诚实边界）；"
                           "已产出片段经恢复中心取回，续跑需用户重新发起（显式计费）",
    ),
    InterruptionClass.NETWORK_LOST.value: PlaybookEntry(
        scene="S3",
        description="网络信号中断 / 传输层不可达",
        on_interrupt="同 S2：逐帧落库 + 分类落台账；客户端断开（GeneratorExit）按本类处理",
        resume_policy="auto",
        resume_description="网络恢复后扫描到未完成的活即自动续作（检查点在盘上，"
                           "从最后完成的超步继续，不重跑已完成节点）",
    ),
    InterruptionClass.PROCESS_KILLED.value: PlaybookEntry(
        scene="S4",
        description="进程被杀 / 崩溃 / 超时 / 机器重启",
        on_interrupt="检查点不在内存（SqliteCheckpointer 落盘 sqlite），"
                     "重启后扫描 open 台账并提示续作",
        resume_policy="auto",
        resume_description="重启后自动从持久检查点续跑；已完成节点不重跑、不重复计费",
    ),
    InterruptionClass.PROVIDER_RESET.value: PlaybookEntry(
        scene="S5",
        description="换 key / base_url / 模型（供应商指纹变化）",
        on_interrupt="中断时记录供应商指纹；恢复时指纹不一致即强制升级为本类处置",
        resume_policy="confirm",
        resume_description="换供应商后成本与输出分布都变了——恢复前必须用户显式确认，"
                           "绝不静默切换后自动续跑（红线 6）",
    ),
    InterruptionClass.AGENT_MISUSE.value: PlaybookEntry(
        scene="S6",
        description="AI / agent 操作失误（误删、误覆盖、误提交）",
        on_interrupt="高危写操作前置快照（services/snapshot.py，sha256 manifest + WorkStash）",
        resume_policy="manual",
        resume_description="恢复=回滚：先 diff 预览再落地，落回后逐文件 sha256 校验；"
                           "涉及覆盖用户数据，必须人工发起",
    ),
    InterruptionClass.UNKNOWN.value: PlaybookEntry(
        scene="-",
        description="未归类的中断（诚实兜底，不许硬塞进六类）",
        on_interrupt="照常落台账 + 挂帧，策略取 manual",
        resume_policy="manual",
        resume_description="查明原因前不许自动续作",
    ),
}

#: 合法策略集合——playbook 自检与台账写入共用，防手滑写错策略值
RESUME_POLICIES = ("auto", "confirm", "manual")


def playbook_for(cls: InterruptionClass | str) -> PlaybookEntry:
    key = cls.value if isinstance(cls, InterruptionClass) else str(cls)
    return INTERRUPTION_PLAYBOOK[key]


def classify_provider_error(exc: BaseException) -> InterruptionClass:
    """把 provider/gateway 异常映射到六类之一。

    ``providers/base.py`` 的 retryable 分类**只**决定「能否重试」（防重复计费），
    不决定「是什么类型的中断」——本函数独立分类，不许互相替代。
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    kind = str(getattr(exc, "kind", "") or type(exc).__name__ or "").lower()
    text = f"{kind} {type(exc).__name__}".lower()
    try:
        status_val = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_val = None
    if status_val == 429 or "rate" in text or "quota" in text:
        return InterruptionClass.RATE_LIMITED
    if ("timeout" in text or "connect" in text or "network" in text
            or "stream" in text or "transport" in text):
        return InterruptionClass.NETWORK_LOST
    return InterruptionClass.UNKNOWN


def classify_stream_exception(exc: BaseException) -> InterruptionClass:
    """流式生成器中途异常的分类（S2/S3/S1）。"""
    from ..runtime.providers.base import ProviderError  # local: avoid import cycle

    if isinstance(exc, GeneratorExit):
        # 客户端断开 = 消费侧网络中断（S3）。产出已逐帧落盘，不算 S2 生成侧断流。
        return InterruptionClass.NETWORK_LOST
    if isinstance(exc, ProviderError):
        if classify_provider_error(exc) is InterruptionClass.RATE_LIMITED:
            return InterruptionClass.RATE_LIMITED
        return InterruptionClass.NETWORK_LOST
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name or "cancel" in name or "os" in name:
        return InterruptionClass.NETWORK_LOST
    return InterruptionClass.STREAM_BROKEN


def provider_fingerprint(settings: object) -> str:
    """供应商指纹：key/base_url/model 任一变化即变（S5 检测依据）。

    只取摘要不存原文——指纹进台账与审计帧，**绝不存 key 本身**。
    """
    import hashlib

    raw = "|".join(
        str(getattr(settings, field, "") or "")
        for field in ("model_provider", "model_base_url", "model_name")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_interruption(
    session: Session,
    audit: AuditService,
    actor: Actor,
    *,
    cls: InterruptionClass | str,
    detail: str = "",
    task_id: str = "",
    thread_id: str = "",
    message_id: str = "",
    stash_id: str = "",
    resume_policy: str | None = None,
    provider_fp: str = "",
) -> InterruptionEvent:
    """落一行台账 + 往审计哈希链挂帧（红线 4：每次中断必须留痕）。

    只 ``flush`` 不 ``commit``——与调用方同事务，由路由/lifespan 决定提交点。
    """
    key = cls.value if isinstance(cls, InterruptionClass) else str(cls)
    policy = resume_policy or playbook_for(key).resume_policy
    if policy not in RESUME_POLICIES:
        raise ValueError(f"invalid resume_policy: {policy!r}")
    event = InterruptionEvent(
        id=uuid.uuid4().hex,
        owner_id=getattr(actor, "owner_id", "") or "",
        interruption_class=key,
        task_id=task_id,
        thread_id=thread_id,
        message_id=message_id,
        stash_id=stash_id,
        detail=detail[:2000],
        resume_policy=policy,
        provider_fp=provider_fp,
        status="open",
    )
    session.add(event)
    session.flush()
    audit.append(
        actor,
        "interruption.recorded",
        event.id,
        {
            "interruption_class": key,
            "task_id": task_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "stash_id": stash_id,
            "resume_policy": policy,
            "provider_fp": provider_fp,
            "detail": detail[:300],
        },
    )
    return event


def mark_resumed(
    session: Session,
    audit: AuditService,
    actor: Actor,
    event: InterruptionEvent,
    *,
    ok: bool,
    note: str,
) -> InterruptionEvent:
    """恢复动作落账：成功置 ``resumed``，失败保持 ``open``（仍然可再续，诚实不装成功）。

    两种情况都挂审计帧——「这次为什么没保住 / 后来怎么恢复的」必须可反查（G7）。
    """
    now: datetime = utcnow()
    if ok:
        event.status = "resumed"
        event.resumed_at = now
    event.last_resume_note = note[:2000]
    session.add(event)
    session.flush()
    audit.append(
        actor,
        "interruption.resumed" if ok else "interruption.resume_failed",
        event.id,
        {"ok": ok, "note": note[:300], "interruption_class": event.interruption_class},
    )
    return event
