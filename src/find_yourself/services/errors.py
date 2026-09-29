"""Typed, machine-readable errors.

Never leak stack traces, connection strings, secrets or other tenants' objects.
Every error carries a stable ``code`` and an HTTP status the API layer maps to
the unified error envelope (FROZEN_CONTRACT §1).
"""


class DomainError(Exception):
    http_status: int = 409

    def __init__(self, code: str, message: str, http_status: int | None = None):
        self.code = code
        self.message = message
        if http_status is not None:
            self.http_status = http_status
        super().__init__(message)


class NotFound(DomainError):
    http_status = 404


class PermissionDenied(DomainError):
    http_status = 403


class Unauthenticated(DomainError):
    http_status = 401


class Conflict(DomainError):
    http_status = 409


class ValidationFailed(DomainError):
    http_status = 422
