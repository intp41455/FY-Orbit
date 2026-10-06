"""Server-side caller identity (FROZEN_CONTRACT §2, §5.1).

An :class:`Actor` is established ONLY by the auth layer after verifying a
session or service identity. Request-body ``owner_id`` / ``role`` / ``domain``
values never promote an actor — that is BUG-03. Services take an ``Actor`` and
decide authorization from it, not from client claims.
"""

from datetime import datetime
from dataclasses import dataclass, field

from ..db.types import utcnow
from .errors import PermissionDenied, Unauthenticated


@dataclass(frozen=True)
class Actor:
    subject_type: str  # "owner" | "service"
    owner_id: str = ""
    service_id: str = ""
    service_kind: str = ""  # worker|agent|tool_gateway|executor|release
    csrf_token: str = ""
    # domains this service identity is bound to (empty for owner = all)
    bound_domains: tuple[str, ...] = field(default_factory=tuple)
    bound_task_id: str | None = None
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    expires_at: datetime | None = None
    max_budget_cents: int | None = None

    @staticmethod
    def owner(owner_id: str, csrf_token: str = "") -> "Actor":
        return Actor(subject_type="owner", owner_id=owner_id, csrf_token=csrf_token)

    @staticmethod
    def service(service_id: str, kind: str, domains: list[str] | None = None,
                *, task_id: str | None = None,
                allowed_tools: list[str] | tuple[str, ...] | None = None,
                expires_at: datetime | None = None,
                max_budget_cents: int | None = None) -> "Actor":
        return Actor(
            subject_type="service", service_id=service_id, service_kind=kind,
            bound_domains=tuple(domains or []),
            bound_task_id=task_id,
            allowed_tools=tuple(allowed_tools or []),
            expires_at=expires_at,
            max_budget_cents=max_budget_cents,
        )

    def require_owner(self) -> None:
        """Only the authenticated owner may approve/decide (§5.3)."""
        if self.subject_type != "owner":
            raise PermissionDenied(
                "owner_only", "Only the authenticated owner session can perform this action", 403
            )

    def require_authenticated(self) -> None:
        if self.subject_type not in {"owner", "service"}:
            raise Unauthenticated("unauthenticated", "Authentication required", 401)
        if self.is_expired():
            raise Unauthenticated("service_expired", "Service credentials have expired", 401)

    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()

    def can_access_domain(self, domain: str) -> bool:
        if self.subject_type == "owner":
            return True
        return domain in self.bound_domains

    def require_domain(self, domain: str) -> None:
        if not self.can_access_domain(domain):
            raise PermissionDenied("domain_forbidden", f"Not authorized for domain: {domain}", 403)

    def can_use_tool(self, tool_name: str) -> bool:
        if self.subject_type == "owner":
            return True
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def require_tool(self, tool_name: str) -> None:
        if not self.can_use_tool(tool_name):
            raise PermissionDenied("tool_forbidden", f"Not authorized for tool: {tool_name}", 403)

    def require_task(self, task_id: str) -> None:
        if self.subject_type == "owner":
            return
        if self.bound_task_id is not None and self.bound_task_id != task_id:
            raise PermissionDenied("task_mismatch",
                                   f"Service credential is bound to task {self.bound_task_id}, not {task_id}", 403)

    def capability_subject(self) -> str:
        """能力网关（services/capability）使用的主体标识。

        收编说明（补齐包1 A-能力网关-01）：本方法**不改变任何既有 RBAC 语义**，
        只把 actor 归一成网关四元组授予里的 ``subject`` 字符串，供
        ``CapabilityBroker`` 在裁决时核对「请求主体 == 凭据主体」。
        """
        if self.subject_type == "owner":
            return f"owner:{self.owner_id}" if self.owner_id else "owner:"
        return f"service:{self.service_id}" if self.service_id else "service:"
