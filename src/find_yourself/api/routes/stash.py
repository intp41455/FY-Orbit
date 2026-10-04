"""Work-stash (记录暂存区) HTTP surface (工单 P1-04).

User working-record staging with restart-safe persistence (rows live in the
primary database, never in process memory):

* ``POST   /api/stash``          — stage a record (content + optional
  title / content_type / metadata),
* ``GET    /api/stash``          — list the caller's staged records (newest
  first, bounded by ``limit``),
* ``GET    /api/stash/{id}``     — read one record back,
* ``DELETE /api/stash/{id}``     — remove one record,
* ``DELETE /api/stash``          — clear all of the caller's records.

Acceptance: curl 暂存 → restart backend → data still there; response bodies
archived as evidence.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from ..deps import csrf_protected, get_actor, get_db
from ...db.staging_models import WorkStash
from ...services.actor import Actor
from ...services.errors import NotFound, ValidationFailed
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/stash", tags=["work-stash"])

_CONTENT_TYPE_MAX = 100


def _serialise(row: WorkStash) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "content_type": row.content_type,
        "metadata": row.stash_metadata or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _get_owned(db: Session, actor: Actor, stash_id: str) -> WorkStash:
    row = db.get(WorkStash, stash_id)
    if row is None or row.owner_id != actor.owner_id:
        raise NotFound("stash_not_found", "Staged record not found", 404)
    return row


class StashCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    title: str = Field(default="", max_length=200)
    content_type: str = Field(default="text/plain", max_length=_CONTENT_TYPE_MAX)
    metadata: dict = Field(default_factory=dict)


@router.post("")
async def stage_record(
    body: StashCreateBody,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    if not body.content.strip():
        raise ValidationFailed("stash_empty_content", "Staged content must not be blank")
    row = WorkStash(
        id=f"ws_{uuid.uuid4().hex}",
        owner_id=actor.owner_id or "",
        title=body.title,
        content=body.content,
        content_type=body.content_type,
        stash_metadata=body.metadata,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "ok", "record": _serialise(row)}


@router.get("")
async def list_records(
    limit: int = Query(default=50, ge=1, le=200),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(WorkStash)
        .where(WorkStash.owner_id == actor.owner_id)
        .order_by(WorkStash.created_at.desc(), WorkStash.id.desc())
        .limit(limit)
    ).scalars().all()
    return {"records": [_serialise(r) for r in rows], "count": len(rows)}


@router.get("/{stash_id}")
async def read_record(
    stash_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    row = _get_owned(db, actor, stash_id)
    return {"record": _serialise(row)}


@router.delete("/{stash_id}")
async def delete_record(
    stash_id: str,
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    row = _get_owned(db, actor, stash_id)
    db.delete(row)
    db.commit()
    return {"status": "ok", "deleted": stash_id}


@router.delete("")
async def clear_records(
    actor: Actor = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    result = db.execute(delete(WorkStash).where(WorkStash.owner_id == actor.owner_id))
    db.commit()
    return {"status": "ok", "deleted": result.rowcount or 0}
