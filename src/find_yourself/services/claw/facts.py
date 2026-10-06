"""A-Claw机制-02 · 全局事实基线库。

「任何 Agent 验证过的结论/确认的数据立刻同步基线库，所有其他 Agent 实时读，
杜绝各说各话。」——落库即 ``upsert``（owner+fact_key 唯一），读即
``get_fact``；每次写入挂审计帧 ``claw.fact.baseline_updated``。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.claw_models import ClawFactBaseline
from ..actor import Actor
from ..audit import AuditService


def upsert_fact(
    session: Session,
    audit: AuditService,
    actor: Actor,
    *,
    fact_key: str,
    fact_value: str,
    verified_by: str = "",
    source_task_id: str = "",
    owner_id: str = "",
) -> tuple[ClawFactBaseline, bool]:
    """验证过的结论立即同步基线库。返回 (行, 是否新建)。

    只 ``flush`` 不 ``commit``（与调用方同事务）。空 key/空 value 显式拒绝——
    基线库里不许放没有内容的行。
    """
    key = str(fact_key or "").strip()
    value = str(fact_value or "").strip()
    if not key or not value:
        raise ValueError("fact_key and fact_value are required")
    owner = owner_id or getattr(actor, "owner_id", "") or ""
    row = session.execute(
        select(ClawFactBaseline).where(
            ClawFactBaseline.owner_id == owner,
            ClawFactBaseline.fact_key == key,
        )
    ).scalar_one_or_none()
    created = row is None
    if row is None:
        row = ClawFactBaseline(
            id=uuid.uuid4().hex,
            owner_id=owner,
            fact_key=key[:200],
            fact_value=value,
            verified_by=verified_by[:200],
            source_task_id=source_task_id[:200],
        )
        session.add(row)
    else:
        row.fact_value = value
        row.verified_by = verified_by[:200] or row.verified_by
        row.source_task_id = source_task_id[:200] or row.source_task_id
    session.flush()
    audit.append(
        actor,
        "claw.fact.baseline_updated" if not created else "claw.fact.baseline_created",
        row.id,
        {"fact_key": key[:120], "verified_by": verified_by[:120],
         "source_task_id": source_task_id[:120]},
    )
    return row, created


def get_fact(
    session: Session, actor: Actor, fact_key: str, *, owner_id: str = ""
) -> str | None:
    """读一条基线事实（其他 Agent 实时读的入口）；不存在返回 None——诚实缺省。"""
    key = str(fact_key or "").strip()
    if not key:
        return None
    owner = owner_id or getattr(actor, "owner_id", "") or ""
    row = session.execute(
        select(ClawFactBaseline).where(
            ClawFactBaseline.owner_id == owner,
            ClawFactBaseline.fact_key == key,
        )
    ).scalar_one_or_none()
    return row.fact_value if row is not None else None


class FactBaselineService:
    """对象式薄壳，方便依赖注入与测试替身。"""

    def __init__(self, session: Session, audit: AuditService, actor: Actor):
        self.session = session
        self.audit = audit
        self.actor = actor

    def upsert(self, *, fact_key: str, fact_value: str, **kw: Any) -> tuple[ClawFactBaseline, bool]:
        return upsert_fact(self.session, self.audit, self.actor,
                           fact_key=fact_key, fact_value=fact_value, **kw)

    def get(self, fact_key: str) -> str | None:
        return get_fact(self.session, self.actor, fact_key)
