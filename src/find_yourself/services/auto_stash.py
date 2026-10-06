"""T6-C 产出自动暂存（补 G4）：中断时把已产出内容自动写进 WorkStash。

G4 现场：``api/routes/stash.py`` 只有手动 POST；用户不点就没有备份。
本模块是「自动暂存」的唯一入口——中断路径（流式断流、限流挂起等）调用它，
把已产出内容落进主库 ``work_stashes`` 表（重启后仍在，红线 1），
并往审计链挂 ``stash.auto_created`` 帧。

元数据约定：``metadata.auto`` 恒为 True——恢复中心/前端据此区分
「系统自动暂存」与「用户手动暂存」，自动行不污染用户的手动列表语义。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db.staging_models import WorkStash
from .actor import Actor
from .audit import AuditService


def auto_stash(
    session: Session,
    audit: AuditService,
    actor: Actor,
    *,
    title: str,
    content: str,
    content_type: str = "text/plain",
    metadata: dict[str, Any] | None = None,
) -> WorkStash:
    """自动暂存一行（flush 不 commit，与调用方同事务）。"""
    meta: dict[str, Any] = {"auto": True, **(metadata or {})}
    row = WorkStash(
        id=uuid.uuid4().hex,
        owner_id=getattr(actor, "owner_id", "") or "",
        title=(title or "自动暂存")[:200],
        content=content,
        content_type=(content_type or "text/plain")[:100],
        stash_metadata=meta,
    )
    session.add(row)
    session.flush()
    audit.append(
        actor,
        "stash.auto_created",
        row.id,
        {
            "title": (title or "")[:120],
            "auto": True,
            **{k: str(v)[:120] for k, v in (metadata or {}).items()},
        },
    )
    return row
