"""统一能力网关 · 四元组授权存取 (补齐包1 A-能力网关-04)。

四元组：**能力 × 资源（路径前缀 / 域名白名单 / 进程白名单）× 时限 × 可撤回**。

* **默认最小必要（默认拒绝）**：本表只存「显式授予/显式拒绝」；没有任何匹配
  allow 时裁决就是拒绝（broker 的 default_deny），本模块不提供任何隐式放行。
* **变更即时生效并审计**：授予/撤销即时写库并即时可查（同一 Session 内生效）；
  审计由 broker 经 :class:`find_yourself.services.capability.audit.CapabilityAudit`
  落哈希链，本模块不留第二套日志。
* **显式拒绝覆盖显式授予**：同请求同时命中 allow 与 deny 时，deny 胜出
  （多路取最严的一部分；匹配逻辑见 :meth:`GrantStore.matching`）。

持久化形态：``capability_grants`` 表。ORM 模型按 B 包 0034 同款惯例放在
service 层而**不**导入 ``db/models.py``——``0001`` 的 ``Base.metadata.create_all``
只建它 import 链上的表，因此 ``0037`` 迁移才是这张表的唯一建表者。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Select, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ...db.base import Base
from ...db.types import TZDateTime, utcnow
from ..errors import NotFound, PermissionDenied, ValidationFailed
from .levels import LEVEL_ORDER
from .types import (
    EFFECT_ALLOW,
    EFFECT_DENY,
    EFFECTS,
    RESOURCE_KINDS,
    CapabilityRequest,
    CapabilityTypeSpec,
    domain_matches,
    path_matches_prefix,
    process_matches,
)


@dataclass
class GrantSpec:
    """一条授权的四元组（+作用域收窄字段）。

    ``ttl_seconds`` 与 ``expires_at`` 二选一；都不给 = 永不过期
    （仅低风险能力允许，深级别能力由 broker 强制短时效——见 broker.grant）。
    """

    subject: str                 # 被授主体（owner id / agent id / service id；"*" = 全体）
    capability: str              # 能力名；支持前缀通配（cross_agent.*）
    effect: str = EFFECT_ALLOW   # allow | deny
    resource_kind: str = "*"     # path | domain | process | level | *
    resource_pattern: str = "*"  # 路径前缀 / 域名通配 / 进程通配
    level: str | None = None     # 限定执行级别（越级授予时必须 = 越级目标级别）
    escalation: bool = False     # True = 这是「越级显式授权」，只满足越级请求
    task_id: str | None = None   # 绑定任务；None = 不限任务
    ttl_seconds: int | None = None
    expires_at: datetime | None = None
    revocable: bool = True       # 不可撤回的授予必须显式声明（默认可撤回）
    note: str = ""


class CapabilityGrantRow(Base):
    """能力授予行（capability_grants）——0037 迁移是唯一建表者。"""

    __tablename__ = "capability_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200), index=True)
    capability: Mapped[str] = mapped_column(String(120), index=True)
    effect: Mapped[str] = mapped_column(String(8))            # allow | deny
    resource_kind: Mapped[str] = mapped_column(String(16))    # path|domain|process|level|*
    resource_pattern: Mapped[str] = mapped_column(String(400), default="*")
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    revocable: Mapped[bool] = mapped_column(Boolean, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class GrantStore:
    """四元组授予/拒绝/撤销/过期的存取与匹配（纯数据层，审计在 broker）。"""

    def __init__(self, session: Session):
        self.s = session

    # -- 校验 -----------------------------------------------------------------

    @staticmethod
    def validate_spec(spec: GrantSpec, type_spec: CapabilityTypeSpec | None) -> None:
        """授权四元组的合法性（能力类型存在性 / 效果 / 资源种类 / 级别）。"""
        if spec.effect not in EFFECTS:
            raise ValidationFailed("invalid_effect", f"effect must be one of {EFFECTS}")
        if not spec.subject:
            raise ValidationFailed("invalid_subject", "grant subject must not be empty")
        if not spec.capability:
            raise ValidationFailed("invalid_capability", "grant capability must not be empty")
        if type_spec is None:
            raise ValidationFailed(
                "unknown_capability",
                f"capability type not registered: {spec.capability}",
            )
        if spec.resource_kind not in RESOURCE_KINDS:
            raise ValidationFailed("invalid_resource_kind", f"resource_kind must be one of {RESOURCE_KINDS}")
        # 资源种类必须与能力类型一致（type_spec.resource_kind=None 的能力不给资源维度）。
        if type_spec.resource_kind is None:
            if spec.resource_kind not in ("*",):
                raise ValidationFailed(
                    "resource_kind_mismatch",
                    f"capability {type_spec.name} has no resource dimension; resource_kind must be '*'",
                )
        elif spec.resource_kind not in ("*", type_spec.resource_kind):
            raise ValidationFailed(
                "resource_kind_mismatch",
                f"capability {type_spec.name} expects resource_kind '{type_spec.resource_kind}', got {spec.resource_kind!r}",
            )
        if spec.level is not None and spec.level not in LEVEL_ORDER:
            raise ValidationFailed("invalid_level", f"unknown level: {spec.level!r}")
        if spec.escalation and (spec.level is None or spec.effect != EFFECT_ALLOW):
            raise ValidationFailed(
                "invalid_escalation_grant",
                "escalation grants must be allow-grants bound to a concrete level",
            )

    # -- 写 -------------------------------------------------------------------

    def grant(self, spec: GrantSpec, *, created_by: str, type_spec: CapabilityTypeSpec | None = None) -> CapabilityGrantRow:
        """落一条授予/拒绝。TTL 与绝对到期互斥，即时生效（flush 即可查）。"""
        self.validate_spec(spec, type_spec)
        if spec.ttl_seconds is not None and spec.expires_at is not None:
            raise ValidationFailed("invalid_ttl", "pass ttl_seconds or expires_at, not both")
        expires_at = spec.expires_at
        if spec.ttl_seconds is not None:
            if spec.ttl_seconds <= 0:
                raise ValidationFailed("invalid_ttl", "ttl_seconds must be positive")
            expires_at = utcnow() + timedelta(seconds=spec.ttl_seconds)
        row = CapabilityGrantRow(
            id=f"cg_{uuid4().hex[:20]}",
            subject=spec.subject,
            capability=spec.capability,
            effect=spec.effect,
            resource_kind=spec.resource_kind,
            resource_pattern=spec.resource_pattern,
            level=spec.level,
            escalation=spec.escalation,
            task_id=spec.task_id,
            expires_at=expires_at,
            revocable=spec.revocable,
            created_by=created_by,
            note=spec.note or None,
            extra={},
            created_at=utcnow(),
        )
        self.s.add(row)
        self.s.flush()
        return row

    def revoke(self, grant_id: str, *, by: str) -> CapabilityGrantRow:
        """撤销一条授予（即时生效）。声明为不可撤回的授予拒绝撤销并说明。"""
        row = self.s.get(CapabilityGrantRow, grant_id)
        if row is None or row.revoked_at is not None:
            raise NotFound("grant_not_found", f"active grant not found: {grant_id}")
        if not row.revocable:
            raise PermissionDenied(
                "grant_not_revocable",
                f"grant {grant_id} was explicitly created irrevocable; it cannot be revoked via API",
            )
        row.revoked_at = utcnow()
        self.s.flush()
        return row

    # -- 读 -------------------------------------------------------------------

    def get(self, grant_id: str) -> CapabilityGrantRow | None:
        return self.s.get(CapabilityGrantRow, grant_id)

    def active(
        self,
        *,
        subject: str | None = None,
        capability: str | None = None,
        include_escalation: bool = True,
    ) -> list[CapabilityGrantRow]:
        """当前有效的授予（未撤销、未过期），供审计/展示与匹配。"""
        stmt: Select = select(CapabilityGrantRow).where(CapabilityGrantRow.revoked_at.is_(None))
        if subject is not None:
            stmt = stmt.where(CapabilityGrantRow.subject.in_((subject, "*")))
        if capability is not None:
            stmt = stmt.where(CapabilityGrantRow.capability.in_((capability, "*")))
        rows = list(self.s.execute(stmt.order_by(CapabilityGrantRow.created_at.asc())).scalars())
        now = utcnow()
        out = []
        for row in rows:
            if row.expires_at is not None and _aware(row.expires_at) <= now:
                continue
            if not include_escalation and row.escalation:
                continue
            out.append(row)
        return out

    @staticmethod
    def _capability_matches(pattern: str, capability: str) -> bool:
        if pattern == "*" or pattern == capability:
            return True
        if pattern.endswith(".*") and capability.startswith(pattern[:-1]):
            return True
        return False

    @staticmethod
    def _resource_matches(row: CapabilityGrantRow, req: CapabilityRequest) -> bool:
        kind, pattern = row.resource_kind, row.resource_pattern
        if kind == "*":
            return True
        resource = req.resource
        if kind == "level":
            # 以级别为资源的授权（越级授予）：级别在 grant.level 上，模式不参与。
            return row.level is not None and row.level == req.level
        if resource is None:
            return False
        if kind == "path":
            return path_matches_prefix(resource, pattern)
        if kind == "domain":
            return domain_matches(resource, pattern)
        if kind == "process":
            return process_matches(resource, pattern)
        return False

    def matching(self, req: CapabilityRequest, *, now: datetime | None = None) -> list[CapabilityGrantRow]:
        """命中该请求的**有效**授予（allow 与 deny 混合返回，由裁决取最严）。

        匹配条件（全部满足才算命中）：
        * 主体：``row.subject in (req.subject, "*")``；
        * 能力：全等 / 前缀通配 / ``*``；
        * 任务：grant 绑定了任务则请求必须同任务；grant 未绑定则任意任务可命中；
        * 级别：grant 限定级别时，请求级别必须一致；越级请求**只**被
          ``escalation=True`` 且级别一致的授予满足；
        * 资源：按种类做前缀/域名/进程匹配；
        * 时限：未撤销且未过期。
        """
        now = now or utcnow()
        stmt = select(CapabilityGrantRow).where(
            CapabilityGrantRow.subject.in_((req.subject or "", "*")),
            CapabilityGrantRow.revoked_at.is_(None),
        )
        rows = list(self.s.execute(stmt).scalars())
        out: list[CapabilityGrantRow] = []
        for row in rows:
            if not self._capability_matches(row.capability, req.capability):
                continue
            if row.task_id is not None and row.task_id != (req.task_id or ""):
                continue
            if row.expires_at is not None and _aware(row.expires_at) <= now:
                continue
            if row.escalation:
                # 越级授予只满足「带级别的请求」，且级别必须一致。
                if req.level is None or row.level != req.level:
                    continue
            else:
                # 普通授予不满足越级请求（越级必须显式 escalation 授予）。
                if req.level is not None and row.level is not None and row.level != req.level:
                    continue
                if row.level is not None and req.level is None:
                    # 限定了级别的普通授予满足默认级别请求当且仅当级别一致——
                    # 默认级别请求的 level=None，与 grant.level 无法比对，视为不匹配，
                    # 避免「L3 授予」意外放宽 L1 能力。
                    continue
            if not self._resource_matches(row, req):
                continue
            out.append(row)
        return out

    def find_deny(self, req: CapabilityRequest, *, now: datetime | None = None) -> CapabilityGrantRow | None:
        """命中的显式拒绝（deny 覆盖一切 allow）。"""
        for row in self.matching(req, now=now):
            if row.effect == EFFECT_DENY:
                return row
        return None

    def find_allow(
        self,
        req: CapabilityRequest,
        *,
        now: datetime | None = None,
        escalation_only: bool = False,
    ) -> CapabilityGrantRow | None:
        """命中的显式授予（取资源模式最窄的一条：资源约束最具体者优先）。

        ``escalation_only=True`` 时只考虑显式越级授予（GrantGate 对越级请求
        用这个形态取 allow，防止普通授予意外满足越级请求）。
        """
        best: CapabilityGrantRow | None = None
        for row in self.matching(req, now=now):
            if row.effect != EFFECT_ALLOW:
                continue
            if escalation_only and not row.escalation:
                continue
            if best is None or _specificity(row) < _specificity(best):
                best = row
        return best


def _specificity(row: CapabilityGrantRow) -> int:
    """授予的「宽泛度」分数，越小越具体（裁决报告用，不参与放行）。"""
    score = 0
    if row.subject == "*":
        score += 8
    if row.capability == "*":
        score += 4
    elif row.capability.endswith(".*"):
        score += 2
    if row.resource_kind == "*":
        score += 4
    if row.task_id is None:
        score += 1
    return score


__all__ = [
    "CapabilityGrantRow",
    "GrantSpec",
    "GrantStore",
    "EFFECT_ALLOW",
    "EFFECT_DENY",
]
