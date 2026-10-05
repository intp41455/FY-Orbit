"""Unit tests for Feature Flags service and config hot-reloading (批次 F).

Covers:
1. FeatureFlag fail-closed default (returns False for unknown flag).
2. FeatureFlag cache hit within TTL (5s) and invalidation upon modification.
3. FeatureFlag audit trail recording (updated_by, updated_at).
4. Config hot-reload via settings(refresh=True) and reload_settings().
5. Config security validation enforcement upon reload (production rules cannot be bypassed).
"""

import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from find_yourself.config import reload_settings, settings
from find_yourself.db.base import Base
from find_yourself.db.feature_flag_models import FeatureFlagRecord
from find_yourself.services.feature_flags import FeatureFlagService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def test_feature_flag_fail_closed_by_default(db_session):
    """Unknown flags must return False without crashing."""
    service = FeatureFlagService(db_session, cache_ttl_seconds=5.0)
    service.clear_cache()
    assert service.is_enabled("non_existent_flag") is False


def test_feature_flag_set_and_query(db_session):
    """Setting a flag enables it immediately."""
    service = FeatureFlagService(db_session, cache_ttl_seconds=5.0)
    service.clear_cache()

    service.set_flag("new_cabin_minigame", True, updated_by="admin_alice")
    assert service.is_enabled("new_cabin_minigame") is True

    # Check database record
    record = db_session.query(FeatureFlagRecord).filter_by(name="new_cabin_minigame").first()
    assert record is not None
    assert record.enabled is True
    assert record.updated_by == "admin_alice"
    assert record.updated_at is not None

    # Disable flag
    service.set_flag("new_cabin_minigame", False, updated_by="admin_bob")
    assert service.is_enabled("new_cabin_minigame") is False


def test_feature_flag_cache_and_expiration(db_session):
    """Feature flag uses short TTL in-memory cache."""
    service = FeatureFlagService(db_session, cache_ttl_seconds=0.2)
    service.clear_cache()

    service.set_flag("cached_flag", True, updated_by="system")
    assert service.is_enabled("cached_flag") is True

    # Directly modify DB row behind the cache's back to test TTL
    db_session.query(FeatureFlagRecord).filter_by(name="cached_flag").update({"enabled": False})
    db_session.commit()

    # Cached value is still True before TTL expires
    assert service.is_enabled("cached_flag") is True

    # Wait for TTL to expire
    time.sleep(0.25)
    assert service.is_enabled("cached_flag") is False


def test_feature_flag_list_flags(db_session):
    """list_flags returns all persisted flags."""
    service = FeatureFlagService(db_session, cache_ttl_seconds=5.0)
    service.clear_cache()

    service.set_flag("flag_a", True, updated_by="user1")
    service.set_flag("flag_b", False, updated_by="user2")

    flags = service.list_flags()
    assert flags["flag_a"] is True
    assert flags["flag_b"] is False


def test_config_refresh_and_reload():
    """settings(refresh=True) and reload_settings() pick up environment variable changes."""
    original_owner = settings().owner_id

    try:
        with patch.dict(os.environ, {"FY_OWNER_ID": "test_owner_new"}):
            # Cached settings still returns old value if not refreshed
            assert settings().owner_id == original_owner

            # With refresh=True
            reloaded = settings(refresh=True)
            assert reloaded.owner_id == "test_owner_new"

            # With reload_settings()
            with patch.dict(os.environ, {"FY_OWNER_ID": "test_owner_another"}):
                reloaded2 = reload_settings()
                assert reloaded2.owner_id == "test_owner_another"
    finally:
        reload_settings()


def test_config_security_cannot_be_bypassed_on_reload():
    """Security rules must remain enforced during reload."""
    with patch.dict(
        os.environ,
        {
            "FY_ENVIRONMENT": "production",
            "FY_SESSION_SECRET": "short",  # < 32 characters, invalid for production
        },
    ):
        with pytest.raises(ValueError, match="FY_SESSION_SECRET"):
            settings(refresh=True)

    # Restore clean settings
    reload_settings()
