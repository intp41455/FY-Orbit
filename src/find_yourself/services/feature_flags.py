"""Dynamic feature flag service with short TTL cache and fail-closed safety (Batch F / §10.5).

Guarantees:
1. No-restart runtime toggling backed by database persistence.
2. Short in-memory TTL cache (default 5.0 seconds) for performance while reflecting updates quickly.
3. Strict fail-closed default: non-existent flags return False.
4. Immediate cache invalidation on write.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db.feature_flag_models import FeatureFlagRecord
from ..db.types import utcnow
from .actor import Actor

logger = logging.getLogger("find_yourself.services.feature_flags")


class FeatureFlagService:
    def __init__(
        self,
        session_maker_or_session: sessionmaker[Session] | Session | None = None,
        session: Session | None = None,
        ttl_seconds: float = 5.0,
        cache_ttl_seconds: float | None = None,
    ):
        if cache_ttl_seconds is not None:
            ttl_seconds = cache_ttl_seconds
        self._ttl_seconds = ttl_seconds

        if isinstance(session_maker_or_session, Session):
            self._session = session_maker_or_session
            self._session_maker = None
        elif isinstance(session_maker_or_session, sessionmaker):
            self._session_maker = session_maker_or_session
            self._session = session
        else:
            self._session_maker = None
            self._session = session

        # In-memory cache: name -> (enabled: bool, expire_at: float)
        self._cache: dict[str, tuple[bool, float]] = {}

    def _get_session(self) -> tuple[Session, bool]:
        if self._session is not None:
            return self._session, False
        if self._session_maker is not None:
            return self._session_maker(), True
        raise RuntimeError("FeatureFlagService requires either a session or session_maker")

    def is_enabled(self, name: str) -> bool:
        """Check if a feature flag is enabled.

        Returns False if the flag does not exist (fail closed).
        Uses a short TTL cache to avoid hitting the database on every check.
        """
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached is not None and now < cached[1]:
            return cached[0]

        session, should_close = self._get_session()
        try:
            row = session.execute(
                select(FeatureFlagRecord).where(FeatureFlagRecord.name == name)
            ).scalar_one_or_none()

            enabled = bool(row.enabled) if row is not None else False
            self._cache[name] = (enabled, now + self._ttl_seconds)
            return enabled
        except Exception as exc:
            logger.warning("Error reading feature flag '%s', defaulting to False: %s", name, exc)
            return False
        finally:
            if should_close:
                session.close()

    def set_flag(
        self,
        actor_or_name: Actor | str,
        name_or_enabled: str | bool,
        enabled_or_none: bool | None = None,
        updated_by: str | None = None,
    ) -> FeatureFlagRecord:
        """Create or update a feature flag, invalidating cache immediately."""
        if isinstance(actor_or_name, Actor):
            actor_or_name.require_authenticated()
            flag_name = str(name_or_enabled)
            flag_enabled = bool(enabled_or_none)
            updater = actor_or_name.owner_id or actor_or_name.service_id or "system"
        else:
            flag_name = str(actor_or_name)
            flag_enabled = bool(name_or_enabled)
            updater = updated_by or "system"

        session, should_close = self._get_session()
        try:
            row = session.get(FeatureFlagRecord, flag_name)
            if row is None:
                row = FeatureFlagRecord(
                    name=flag_name,
                    enabled=flag_enabled,
                    updated_at=utcnow(),
                    updated_by=updater,
                )
                session.add(row)
            else:
                row.enabled = flag_enabled
                row.updated_at = utcnow()
                row.updated_by = updater

            session.commit()
            # Immediately update cache
            self._cache[flag_name] = (flag_enabled, time.monotonic() + self._ttl_seconds)
            return row
        finally:
            if should_close:
                session.close()

    def list_flags(self) -> dict[str, bool]:
        """List all defined feature flags."""
        session, should_close = self._get_session()
        try:
            rows = list(session.execute(select(FeatureFlagRecord)).scalars())
            return {r.name: r.enabled for r in rows}
        finally:
            if should_close:
                session.close()

    def clear_cache(self, name: str | None = None) -> None:
        """Explicitly clear the in-memory cache."""
        if name is not None:
            self._cache.pop(name, None)
        else:
            self._cache.clear()
