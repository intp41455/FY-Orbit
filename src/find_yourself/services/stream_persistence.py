"""T6-D 流式产出落盘与断点取回（补 G3）。

G3 现场：``api/routes/streaming.py`` 的 ``_stamped(gen, message_id)`` 直接把帧
吐给客户端，中途断流则已生成的一半**蒸发**（S2/S3 违反）。

本模块把流变成「**先落库、后吐出**」：

* 每一帧先写 ``stream_segments``（``(message_id, seq)`` 唯一）并 ``commit``，
  然后才 yield 给网络——断流瞬间，已吐出的每一帧都保证已在盘上；
* 生成器异常（断流/断网/限流）→ 六类分类 → 中断台账一行 + 已产出片段
  自动暂存 WorkStash + 审计挂帧（红线 3/4/5）；
* ``GET /api/streaming/{message_id}/segments?after_seq=N`` 提供 Last-Event-ID
  语义的断点取回（照抄 ``collaboration.py:296`` 范式）。

诚实边界：本模块保证「已产出部分不蒸发、可取回」；**不**承诺把新客户端
重连到同一在飞的进程内生成器——那需要把生成器脱离请求生命周期托管，
是后续切片。恢复中心会如实标注这一点。
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.resilience_models import StreamSegment
from ..runtime.interruption import (
    InterruptionClass,
    classify_stream_exception,
    record_interruption,
)
from .actor import Actor
from .audit import AuditService
from .auto_stash import auto_stash


class StreamRecorder:
    """给一条 message_id 逐帧分配递增 seq 并落库。"""

    def __init__(self, session: Session, message_id: str):
        self.session = session
        self.message_id = message_id
        self.seq = 0

    def record(self, frame: str) -> StreamSegment:
        self.seq += 1
        seg = StreamSegment(
            id=uuid.uuid4().hex,
            message_id=self.message_id,
            seq=self.seq,
            frame=frame,
        )
        self.session.add(seg)
        self.session.flush()
        return seg


def get_segments(
    session: Session, message_id: str, *, after_seq: int = 0, limit: int = 1000
) -> list[StreamSegment]:
    """取回已落盘片段（after_seq 之后按 seq 升序）——断点续传的读半边。"""
    rows = session.execute(
        select(StreamSegment)
        .where(StreamSegment.message_id == message_id,
               StreamSegment.seq > int(after_seq))
        .order_by(StreamSegment.seq.asc())
        .limit(max(1, min(int(limit), 5000)))
    ).scalars().all()
    return list(rows)


async def persist_stream(
    frames: AsyncIterator[str],
    *,
    session: Session,
    audit: AuditService,
    actor: Actor,
    message_id: str,
    task_id: str = "",
) -> AsyncIterator[str]:
    """包一层「先落库后吐出」。异常即分类落台账 + 自动暂存 + 挂帧，然后原样抛出。"""
    recorder = StreamRecorder(session, message_id)
    produced: list[str] = []
    try:
        async for frame in frames:
            recorder.record(frame)
            # 每帧提交：断流瞬间的「已 yield = 已落盘」不变式由这里保证
            session.commit()
            produced.append(frame)
            yield frame
    except BaseException as exc:
        cls: InterruptionClass = classify_stream_exception(exc)
        stash = auto_stash(
            session, audit, actor,
            title=f"流式中断 · {message_id}",
            content="".join(produced) if produced else "(断流时未产出任何帧)",
            metadata={
                "interruption_class": cls.value,
                "message_id": message_id,
                "task_id": task_id,
                "frames": len(produced),
                "error": f"{type(exc).__name__}: {exc}"[:300],
            },
        )
        record_interruption(
            session, audit, actor,
            cls=cls,
            detail=(f"stream broken at seq={recorder.seq}: "
                    f"{type(exc).__name__}: {exc}")[:1000],
            task_id=task_id,
            message_id=message_id,
            stash_id=stash.id,
        )
        session.commit()
        raise
    else:
        session.commit()
