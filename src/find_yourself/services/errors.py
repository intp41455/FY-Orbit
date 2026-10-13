"""Typed, machine-readable errors.

异常类实体已下沉至 L1 契约单源 :mod:`find_yourself.contracts`（ADR-0001 D3/D6），
此处仅为向后兼容的 re-export 垫片，供既有调用方继续 ``from ..services.errors import ...``。
新代码请直接 ``from ..contracts import ...``。
"""

from ..contracts import (
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    Unauthenticated,
    ValidationFailed,
)

__all__ = [
    "Conflict",
    "DomainError",
    "NotFound",
    "PermissionDenied",
    "Unauthenticated",
    "ValidationFailed",
]
