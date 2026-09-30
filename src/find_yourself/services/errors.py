"""Typed, machine-readable errors.

Never leak stack traces, connection strings, secrets or other tenants' objects.
Every error carries a stable ``code`` and an HTTP status the API layer maps to
the unified error envelope (FROZEN_CONTRACT §1).
"""


class DomainError(Exception):
    http_status: int = 409
    default_code: str = "domain_error"

    def __init__(self, code: str, message: str | None = None, http_status: int | None = None):
        if message is None:
            self.message = code
            self.code = getattr(self, "default_code", "domain_error")
        else:
            self.code = code
            self.message = message
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)


class NotFound(DomainError):
    http_status = 404
    default_code = "not_found"


class PermissionDenied(DomainError):
    http_status = 403
    default_code = "permission_denied"


class Unauthenticated(DomainError):
    http_status = 401
    default_code = "unauthenticated"


class Conflict(DomainError):
    http_status = 409
    default_code = "conflict"


class ValidationFailed(DomainError):
    http_status = 422
    default_code = "validation_failed"
