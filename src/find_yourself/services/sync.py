"""Service layer for selective data synchronization and data classification governance.

Implements Phase A & D requirements of 13_双端产品续作与验收清单.md:
1. Strict classification: raw dialogues/attachments and credentials are local_only.
2. Mode check: default 'local_only'; user must explicitly opt in with category selection.
3. Tombstone preservation for deletes.
4. Non-silent conflict management with user resolution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ..db.sync_models import (
    LOCAL_ONLY_CATEGORIES,
    OPT_IN_SYNC_CATEGORIES,
    SyncConflict,
    SyncJournal,
    SyncSetting,
)
from ..db.types import utcnow
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationFailed


class SyncService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create_setting(self, actor: Actor, default_device_id: str | None = None) -> SyncSetting:
        """Retrieve owner's sync preferences, initializing default 'local_only' if not present."""
        setting = self.session.get(SyncSetting, actor.owner_id)
        if not setting:
            setting = SyncSetting(
                owner_id=actor.owner_id,
                mode="local_only",
                enabled_categories=["profiles_and_corrections", "canvas_topology_tasks"],
                paused=False,
                device_id=default_device_id or f"dev-{uuid4().hex[:8]}",
            )
            self.session.add(setting)
            self.session.commit()
        return setting

    def update_setting(
        self,
        actor: Actor,
        mode: str | None = None,
        enabled_categories: list[str] | None = None,
        paused: bool | None = None,
        device_id: str | None = None,
    ) -> SyncSetting:
        """Update synchronization preferences."""
        setting = self.get_or_create_setting(actor)

        if mode is not None:
            if mode not in ("local_only", "sync_opt_in"):
                raise ValidationFailed(f"Invalid sync mode '{mode}'. Must be 'local_only' or 'sync_opt_in'.")
            setting.mode = mode

        if enabled_categories is not None:
            for cat in enabled_categories:
                if cat in LOCAL_ONLY_CATEGORIES:
                    raise ValidationFailed(
                        f"Category '{cat}' is strictly local_only and cannot be enabled for cloud sync."
                    )
                if cat not in OPT_IN_SYNC_CATEGORIES:
                    raise ValidationFailed(f"Unknown or unsupportable category '{cat}'.")
            setting.enabled_categories = list(set(enabled_categories))

        if paused is not None:
            setting.paused = paused

        if device_id is not None:
            setting.device_id = device_id

        setting.updated_at = utcnow()
        self.session.commit()
        return setting

    def record_local_journal(
        self,
        actor: Actor,
        entity_type: str,
        entity_id: str,
        version: int,
        payload: dict[str, Any] | None,
        is_tombstone: bool = False,
        device_id: str | None = None,
    ) -> SyncJournal:
        """Record a versioned mutation in the local journal."""
        payload_str = json.dumps(payload or {}, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

        # Check existing version
        existing = self.session.execute(
            select(SyncJournal).where(
                SyncJournal.owner_id == actor.owner_id,
                SyncJournal.entity_type == entity_type,
                SyncJournal.entity_id == entity_id,
                SyncJournal.version == version,
            )
        ).scalar_one_or_none()

        if existing:
            return existing

        journal = SyncJournal(
            id=f"jrn-{uuid4().hex[:12]}",
            owner_id=actor.owner_id,
            entity_type=entity_type,
            entity_id=entity_id,
            version=version,
            payload_hash=payload_hash,
            payload=payload if not is_tombstone else None,
            is_tombstone=is_tombstone,
            device_id=device_id,
        )
        self.session.add(journal)
        self.session.commit()
        return journal

    def push(
        self,
        actor: Actor,
        items: list[dict[str, Any]],
        client_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Push client changes to sync store.
        
        Strictly enforces:
        1. Mode check: if 'local_only', upload is rejected.
        2. Paused check.
        3. Local-only category block (credentials, raw texts).
        4. Category opt-in check.
        5. Concurrency conflict tracking (no silent overwrite).
        """
        setting = self.get_or_create_setting(actor)

        if setting.mode == "local_only":
            raise ValidationFailed(
                "Upload rejected: current synchronization mode is 'local_only'. "
                "Explicit opt-in is required before uploading data to cloud."
            )

        if setting.paused:
            return {
                "status": "paused",
                "message": "Synchronization is currently paused by user.",
                "accepted_count": 0,
                "conflicts_count": 0,
            }

        accepted: list[str] = []
        conflicts: list[dict[str, Any]] = []
        skipped: list[str] = []

        for item in items:
            entity_type = item.get("entity_type", "")
            entity_id = item.get("entity_id", "")
            client_version = item.get("version", 1)
            payload = item.get("payload")
            is_tombstone = bool(item.get("is_tombstone", False))

            # 1. Strict classification defense
            if entity_type in LOCAL_ONLY_CATEGORIES:
                raise ValidationFailed(
                    f"Security Boundary Violation: Entity type '{entity_type}' is strictly local_only "
                    f"and cannot be uploaded to remote sync servers under any circumstances."
                )

            # 2. Check if category is enabled in user settings
            if entity_type not in setting.enabled_categories:
                skipped.append(f"{entity_type}:{entity_id}")
                continue

            payload_str = json.dumps(payload or {}, sort_keys=True)
            payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

            # 3. Check latest server version
            latest_journal = self.session.execute(
                select(SyncJournal)
                .where(
                    SyncJournal.owner_id == actor.owner_id,
                    SyncJournal.entity_type == entity_type,
                    SyncJournal.entity_id == entity_id,
                )
                .order_by(SyncJournal.version.desc())
            ).scalars().first()

            if latest_journal:
                if latest_journal.version > client_version:
                    # Remote has newer version! Record conflict
                    conflict = SyncConflict(
                        id=f"cnf-{uuid4().hex[:12]}",
                        owner_id=actor.owner_id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        local_version=client_version,
                        local_payload=payload,
                        remote_version=latest_journal.version,
                        remote_payload=latest_journal.payload,
                        resolution_status="pending",
                    )
                    self.session.add(conflict)
                    conflicts.append({
                        "conflict_id": conflict.id,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "local_version": client_version,
                        "remote_version": latest_journal.version,
                    })
                    continue
                elif latest_journal.version == client_version and latest_journal.payload_hash != payload_hash:
                    # Same version, divergent content! Record conflict
                    conflict = SyncConflict(
                        id=f"cnf-{uuid4().hex[:12]}",
                        owner_id=actor.owner_id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        local_version=client_version,
                        local_payload=payload,
                        remote_version=latest_journal.version,
                        remote_payload=latest_journal.payload,
                        resolution_status="pending",
                    )
                    self.session.add(conflict)
                    conflicts.append({
                        "conflict_id": conflict.id,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "local_version": client_version,
                        "remote_version": latest_journal.version,
                    })
                    continue
                elif latest_journal.version == client_version and latest_journal.payload_hash == payload_hash:
                    # Already up to date (idempotent)
                    accepted.append(f"{entity_type}:{entity_id}:v{client_version}")
                    continue

            # Accept clean mutation
            new_journal = SyncJournal(
                id=f"jrn-{uuid4().hex[:12]}",
                owner_id=actor.owner_id,
                entity_type=entity_type,
                entity_id=entity_id,
                version=client_version,
                payload_hash=payload_hash,
                payload=payload if not is_tombstone else None,
                is_tombstone=is_tombstone,
                device_id=client_device_id or setting.device_id,
            )
            self.session.add(new_journal)
            accepted.append(f"{entity_type}:{entity_id}:v{client_version}")

        setting.last_synced_at = utcnow()
        self.session.commit()

        return {
            "status": "success",
            "accepted_count": len(accepted),
            "conflicts_count": len(conflicts),
            "skipped_count": len(skipped),
            "accepted_items": accepted,
            "conflicts": conflicts,
            "last_synced_at": setting.last_synced_at.isoformat() if setting.last_synced_at else None,
        }

    def pull(
        self,
        actor: Actor,
        since_iso: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Pull server mutations since a cursor timestamp for enabled categories."""
        setting = self.get_or_create_setting(actor)

        if setting.mode == "local_only" or setting.paused:
            return {
                "changes": [],
                "has_more": False,
                "sync_mode": setting.mode,
                "paused": setting.paused,
            }

        query = select(SyncJournal).where(
            SyncJournal.owner_id == actor.owner_id,
            SyncJournal.entity_type.in_(setting.enabled_categories),
        )

        if since_iso:
            try:
                since_dt = datetime.fromisoformat(since_iso)
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=timezone.utc)
                query = query.where(SyncJournal.created_at > since_dt)
            except ValueError:
                raise ValidationFailed(f"Invalid timestamp format for since_iso: '{since_iso}'")

        query = query.order_by(SyncJournal.created_at.asc()).limit(limit + 1)
        results = self.session.execute(query).scalars().all()

        has_more = len(results) > limit
        items = results[:limit]

        changes = []
        for r in items:
            changes.append({
                "journal_id": r.id,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "version": r.version,
                "payload_hash": r.payload_hash,
                "payload": r.payload,
                "is_tombstone": r.is_tombstone,
                "device_id": r.device_id,
                "created_at": r.created_at.isoformat(),
            })

        latest_timestamp = items[-1].created_at.isoformat() if items else since_iso

        return {
            "changes": changes,
            "next_cursor": latest_timestamp,
            "has_more": has_more,
            "sync_mode": setting.mode,
            "paused": setting.paused,
        }

    def list_conflicts(self, actor: Actor, status: str = "pending") -> list[dict[str, Any]]:
        """List unresolved or resolved conflicts for the owner."""
        conflicts = self.session.execute(
            select(SyncConflict).where(
                SyncConflict.owner_id == actor.owner_id,
                SyncConflict.resolution_status == status,
            ).order_by(SyncConflict.created_at.desc())
        ).scalars().all()

        return [
            {
                "conflict_id": c.id,
                "entity_type": c.entity_type,
                "entity_id": c.entity_id,
                "local_version": c.local_version,
                "local_payload": c.local_payload,
                "remote_version": c.remote_version,
                "remote_payload": c.remote_payload,
                "resolution_status": c.resolution_status,
                "created_at": c.created_at.isoformat(),
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            }
            for c in conflicts
        ]

    def resolve_conflict(
        self,
        actor: Actor,
        conflict_id: str,
        resolution: str,
    ) -> dict[str, Any]:
        """User explicitly resolves a synchronization conflict."""
        if resolution not in ("keep_local", "accept_remote"):
            raise ValidationFailed(f"Invalid resolution '{resolution}'. Must be 'keep_local' or 'accept_remote'.")

        conflict = self.session.get(SyncConflict, conflict_id)
        if not conflict or conflict.owner_id != actor.owner_id:
            raise NotFound("SyncConflict", conflict_id)

        if conflict.resolution_status != "pending":
            raise Conflict("conflict_already_resolved", f"Conflict {conflict_id} has already been resolved.")

        conflict.resolution_status = "resolved_local" if resolution == "keep_local" else "resolved_remote"
        conflict.resolved_at = utcnow()

        # If user chose keep_local, record a new incremented journal version on server
        if resolution == "keep_local" and conflict.local_payload:
            new_version = max(conflict.local_version, conflict.remote_version) + 1
            payload_str = json.dumps(conflict.local_payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
            journal = SyncJournal(
                id=f"jrn-{uuid4().hex[:12]}",
                owner_id=actor.owner_id,
                entity_type=conflict.entity_type,
                entity_id=conflict.entity_id,
                version=new_version,
                payload_hash=payload_hash,
                payload=conflict.local_payload,
                is_tombstone=False,
                device_id="resolved_conflict",
            )
            self.session.add(journal)

        self.session.commit()

        return {
            "conflict_id": conflict.id,
            "status": conflict.resolution_status,
            "resolved_at": conflict.resolved_at.isoformat(),
        }
