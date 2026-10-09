"""REST API endpoints for selective data synchronization and client sync control."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...services.actor import Actor
from ...services.sync import SyncService
from ..deps import csrf_protected, get_actor, get_session

router = APIRouter(prefix="/api/sync", tags=["sync"])


class SyncConfigUpdateRequest(BaseModel):
    mode: str | None = Field(default=None, description="Sync mode: 'local_only' or 'sync_opt_in'")
    enabled_categories: list[str] | None = Field(default=None, description="Categories selected for sync")
    paused: bool | None = Field(default=None, description="Pause or resume synchronization")
    device_id: str | None = Field(default=None, description="Client device identifier")


class SyncPushItem(BaseModel):
    entity_type: str
    entity_id: str
    version: int = 1
    payload: dict[str, Any] | None = None
    is_tombstone: bool = False


class SyncPushRequest(BaseModel):
    items: list[SyncPushItem]
    client_device_id: str | None = None


class ConflictResolutionRequest(BaseModel):
    resolution: str = Field(..., description="'keep_local' or 'accept_remote'")


@router.get("/status")
def get_sync_status(
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve the current synchronization mode, preferences and conflict count."""
    service = SyncService(session)
    setting = service.get_or_create_setting(actor)
    conflicts = service.list_conflicts(actor, status="pending")

    return {
        "owner_id": actor.owner_id,
        "mode": setting.mode,
        "enabled_categories": setting.enabled_categories,
        "paused": setting.paused,
        "device_id": setting.device_id,
        "last_synced_at": setting.last_synced_at.isoformat() if setting.last_synced_at else None,
        "pending_conflicts_count": len(conflicts),
        "available_categories": [
            "profiles_and_corrections",
            "canvas_topology_tasks",
            "official_memories",
            "chart_records",
        ],
        "local_only_categories": [
            "raw_dialog_attachments",
            "credentials_and_keys",
            "audit_events",
            "local_hardware_probes",
        ],
    }


@router.post("/config")
def update_sync_config(
    body: SyncConfigUpdateRequest,
    actor: Actor = Depends(csrf_protected),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Update synchronization mode, opt-in categories or pause status."""
    service = SyncService(session)
    updated = service.update_setting(
        actor,
        mode=body.mode,
        enabled_categories=body.enabled_categories,
        paused=body.paused,
        device_id=body.device_id,
    )
    return {
        "status": "updated",
        "mode": updated.mode,
        "enabled_categories": updated.enabled_categories,
        "paused": updated.paused,
        "device_id": updated.device_id,
        "updated_at": updated.updated_at.isoformat(),
    }


@router.post("/push", status_code=status.HTTP_200_OK)
def push_sync_changes(
    body: SyncPushRequest,
    actor: Actor = Depends(csrf_protected),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Push versioned local changes to remote sync store."""
    service = SyncService(session)
    raw_items = [item.model_dump() for item in body.items]
    return service.push(actor, raw_items, client_device_id=body.client_device_id)


def _pull_impl(
    actor: Actor,
    session: Session,
    since: str | None,
    limit: int,
) -> dict[str, Any]:
    """Shared read-only pull logic for the GET and POST variants."""
    service = SyncService(session)
    return service.pull(actor, since_iso=since, limit=limit)


@router.get("/pull")
def pull_sync_changes(
    since: str | None = Query(default=None, description="ISO timestamp cursor for incremental changes"),
    limit: int = Query(default=100, ge=1, le=500),
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Pull incremental server mutations for enabled categories."""
    return _pull_impl(actor, session, since, limit)


@router.post("/pull")
def pull_sync_changes_post(
    since: str | None = Query(default=None, description="ISO timestamp cursor for incremental changes"),
    limit: int = Query(default=100, ge=1, le=500),
    actor: Actor = Depends(csrf_protected),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """POST variant of pull (same read semantics; CSRF applies to owner sessions)."""
    return _pull_impl(actor, session, since, limit)


@router.get("/conflicts")
def list_sync_conflicts(
    status_filter: str = Query(default="pending", alias="status"),
    actor: Actor = Depends(get_actor),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List pending or resolved sync conflicts."""
    service = SyncService(session)
    return service.list_conflicts(actor, status=status_filter)


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_sync_conflict(
    conflict_id: str,
    body: ConflictResolutionRequest,
    actor: Actor = Depends(csrf_protected),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Resolve a divergent synchronization conflict."""
    service = SyncService(session)
    return service.resolve_conflict(actor, conflict_id, resolution=body.resolution)
