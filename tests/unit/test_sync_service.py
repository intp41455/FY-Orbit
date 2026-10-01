"""Unit tests for SyncService and data classification governance.

Verifies:
1. Default sync mode is local_only; opt-in required before uploading.
2. Strict classification: credentials and raw dialog attachments cannot be enabled or pushed.
3. Concurrency conflict detection and explicit user resolution (keep_local vs accept_remote).
4. Tombstone deletions and incremental cursor pull.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from find_yourself.db.base import Base
from find_yourself.db.sync_models import (
    LOCAL_ONLY_CATEGORIES,
    OPT_IN_SYNC_CATEGORIES,
    SyncConflict,
    SyncJournal,
    SyncSetting,
)
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, ValidationFailed
from find_yourself.services.sync import SyncService


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with session_factory() as session:
        yield session


@pytest.fixture()
def actor() -> Actor:
    return Actor.owner(owner_id="user-sync-001")


def test_default_setting_is_local_only(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    setting = service.get_or_create_setting(actor)

    assert setting.owner_id == actor.owner_id
    assert setting.mode == "local_only"
    assert setting.paused is False
    assert "profiles_and_corrections" in setting.enabled_categories


def test_cannot_enable_local_only_category(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)

    with pytest.raises(ValidationFailed) as exc_info:
        service.update_setting(actor, enabled_categories=["credentials_and_keys"])
    assert "strictly local_only" in str(exc_info.value)

    with pytest.raises(ValidationFailed) as exc_info:
        service.update_setting(actor, enabled_categories=["raw_dialog_attachments"])
    assert "strictly local_only" in str(exc_info.value)


def test_cannot_set_invalid_sync_mode(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    with pytest.raises(ValidationFailed):
        service.update_setting(actor, mode="invalid_mode")


def test_push_rejected_when_mode_is_local_only(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    setting = service.get_or_create_setting(actor)
    assert setting.mode == "local_only"

    with pytest.raises(ValidationFailed) as exc_info:
        service.push(
            actor,
            items=[{
                "entity_type": "profiles_and_corrections",
                "entity_id": "prof-1",
                "version": 1,
                "payload": {"name": "Test Profile"},
            }],
        )
    assert "Upload rejected" in str(exc_info.value)
    assert "local_only" in str(exc_info.value)


def test_push_rejects_strictly_local_entities_even_if_opted_in(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    service.update_setting(actor, mode="sync_opt_in")

    with pytest.raises(ValidationFailed) as exc_info:
        service.push(
            actor,
            items=[{
                "entity_type": "credentials_and_keys",
                "entity_id": "key-secret-1",
                "version": 1,
                "payload": {"api_key": "sk-secret"},
            }],
        )
    assert "Security Boundary Violation" in str(exc_info.value)
    assert "credentials_and_keys" in str(exc_info.value)


def test_push_clean_and_idempotent(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    service.update_setting(actor, mode="sync_opt_in", enabled_categories=["profiles_and_corrections"])

    # 1. Clean push
    res1 = service.push(
        actor,
        items=[{
            "entity_type": "profiles_and_corrections",
            "entity_id": "prof-1",
            "version": 1,
            "payload": {"trait": "Analytical"},
        }],
        client_device_id="device-macbook",
    )
    assert res1["status"] == "success"
    assert res1["accepted_count"] == 1
    assert res1["conflicts_count"] == 0

    # 2. Idempotent push of same item
    res2 = service.push(
        actor,
        items=[{
            "entity_type": "profiles_and_corrections",
            "entity_id": "prof-1",
            "version": 1,
            "payload": {"trait": "Analytical"},
        }],
    )
    assert res2["accepted_count"] == 1
    assert res2["conflicts_count"] == 0


def test_conflict_detection_and_resolution(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    service.update_setting(actor, mode="sync_opt_in", enabled_categories=["canvas_topology_tasks"])

    # Device A pushes v1
    service.push(
        actor,
        items=[{
            "entity_type": "canvas_topology_tasks",
            "entity_id": "task-alpha",
            "version": 1,
            "payload": {"title": "Task Original"},
        }],
    )

    # Server advances to v2
    service.push(
        actor,
        items=[{
            "entity_type": "canvas_topology_tasks",
            "entity_id": "task-alpha",
            "version": 2,
            "payload": {"title": "Task Remote v2"},
        }],
    )

    # Device B attempts to push diverging v1
    res_conflict = service.push(
        actor,
        items=[{
            "entity_type": "canvas_topology_tasks",
            "entity_id": "task-alpha",
            "version": 1,
            "payload": {"title": "Task Divergent Local v1"},
        }],
    )
    assert res_conflict["conflicts_count"] == 1
    conflicts = service.list_conflicts(actor, status="pending")
    assert len(conflicts) == 1
    conf_id = conflicts[0]["conflict_id"]
    assert conflicts[0]["local_version"] == 1
    assert conflicts[0]["remote_version"] == 2

    # User chooses keep_local resolution
    res_resolve = service.resolve_conflict(actor, conflict_id=conf_id, resolution="keep_local")
    assert res_resolve["status"] == "resolved_local"

    # Verify a new version (v3) was recorded on server with local's content
    pull_res = service.pull(actor)
    v3_items = [c for c in pull_res["changes"] if c["entity_id"] == "task-alpha" and c["version"] == 3]
    assert len(v3_items) == 1
    assert v3_items[0]["payload"]["title"] == "Task Divergent Local v1"


def test_tombstone_and_pull_cursor(db_session: Session, actor: Actor) -> None:
    service = SyncService(db_session)
    service.update_setting(actor, mode="sync_opt_in", enabled_categories=["profiles_and_corrections"])

    # 1. Push normal item
    service.push(
        actor,
        items=[{
            "entity_type": "profiles_and_corrections",
            "entity_id": "prof-del",
            "version": 1,
            "payload": {"status": "active"},
        }],
    )

    # 2. Push tombstone deletion
    service.push(
        actor,
        items=[{
            "entity_type": "profiles_and_corrections",
            "entity_id": "prof-del",
            "version": 2,
            "is_tombstone": True,
        }],
    )

    # 3. Pull all
    pulled = service.pull(actor)
    changes = pulled["changes"]
    assert len(changes) == 2
    assert changes[1]["is_tombstone"] is True
    assert changes[1]["payload"] is None

    # 4. Pull using next_cursor
    cursor = changes[1]["created_at"]
    pulled_next = service.pull(actor, since_iso=cursor)
    assert len(pulled_next["changes"]) == 0
