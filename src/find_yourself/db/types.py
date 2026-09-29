"""Reusable column types.

- Money is always ``NUMERIC(12,6)`` (never float) on PostgreSQL; the same
  numeric type is used on tests so Python-side values stay ``Decimal``.
- Timestamps are timezone-aware UTC (``TIMESTAMP WITH TIME ZONE``).
- SHA-256 digests are fixed 64-char strings.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.types import TypeDecorator

MONEY = Numeric(12, 6)
SHA256 = String(64)
HASH64 = String(64)
ID = String(64)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TZDateTime(TypeDecorator):
    """TIMESTAMP WITH TIME ZONE; normalises naive input to UTC on the way in."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        # On SQLite (no TIMESTAMPTZ) the driver returns naive datetimes; on
        # PostgreSQL the driver returns aware UTC. In both cases normalise to
        # timezone-aware UTC so comparisons against ``datetime.now(timezone.utc)``
        # never raise ``TypeError: can't compare offset-naive and offset-aware``.
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Money(TypeDecorator):
    """Fixed-precision money, ``NUMERIC(12,6)``; coerces floats/strings to Decimal."""

    impl = Numeric(12, 6)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
