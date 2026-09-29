"""Server-side caller identity (FROZEN_CONTRACT §2, §5.1).

An :class:`Actor` is established ONLY by the auth layer after verifying a
session or service identity. Request-body ``owner_id`` / ``role`` / ``domain``
values never promote an actor — that is BUG-03. Services take an ``Actor`` and
decide authorization from it, not from client claims.
"""

from dataclasses import dataclass, field

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

    @staticmethod
    def owner(owner_id: str, csrf_token: str = "") -> "Actor":
        return Actor(subject_type="owner", owner_id=owner_id, csrf_token=csrf_token)

    @staticmethod
    def service(service_id: str, kind: str, domains: list[str] | None = None) -> "Actor":
        return Actor(
            subject_type="service", service_id=service_id, service_kind=kind,
            bound_domains=tuple(domains or []),
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

    def can_access_domain(self, domain: str) -> bool:
        if self.subject_type == "owner":
            return True
        return domain in self.bound_domains
