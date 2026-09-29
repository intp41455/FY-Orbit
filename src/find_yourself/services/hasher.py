"""Canonical JSON and digesting (FROZEN_CONTRACT §6.1).

Digest input is serialised with compact separators, sorted keys and
``ensure_ascii=False``; datetimes are normalised to UTC ISO-8601 before hashing.
The result is SHA-256 hex. Recomputing the digest of stored content must equal
the stored digest or approval is refused.
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any


def _normalise(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            from zoneinfo import ZoneInfo  # pragma: no cover
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(_utc()).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    return value


def _utc():
    from datetime import timezone
    return timezone.utc


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalise(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
