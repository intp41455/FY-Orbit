"""画布持久化快照；owner 隔离；前端需显式接入此接口。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...db.autosave_models import CanvasAutosave
from ...db.types import utcnow
from ...services.actor import Actor
from ...services.errors import Conflict
from ..deps import csrf_protected, get_actor, get_db

router = APIRouter(prefix="/api/dsl-canvas/autosave", tags=["dsl-canvas-autosave"])


class AutosaveDocument(BaseModel):
    canvas_id: str = Field(min_length=1, max_length=200)
    content: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(ge=1)
    checksum: str | None = Field(default=None, max_length=128)


def _scope(actor: Actor, canvas_id: str):
    actor.require_owner()
    return (CanvasAutosave.owner_id == actor.owner_id, CanvasAutosave.canvas_id == canvas_id)


def _conflict(doc: AutosaveDocument, row: CanvasAutosave) -> dict:
    return {"saved": False, "canvas_id": doc.canvas_id, "version": row.version,
            "updated_at": row.updated_at,
            "conflict": {"current_version": doc.version, "server_version": row.version,
                         "server_content": row.content, "server_updated_at": row.updated_at}}


@router.post("/save")
def autosave(doc: AutosaveDocument, actor: Actor = Depends(csrf_protected),
             db: Session = Depends(get_db)) -> dict:
    scope = _scope(actor, doc.canvas_id)
    row = db.scalar(select(CanvasAutosave).where(*scope))
    now = utcnow()
    checksum = hashlib.sha256(json.dumps(doc.content, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    if row is None:
        if doc.version != 1:
            raise Conflict("autosave_missing", "快照不存在；请重新加载后从版本 1 创建")
        row = CanvasAutosave(owner_id=actor.owner_id, canvas_id=doc.canvas_id,
                             content=doc.content, checksum=checksum, version=1,
                             created_at=now, updated_at=now,
                             history=[{"version": 1, "checksum": checksum,
                                       "updated_at": now.isoformat()}])
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            current = db.scalar(select(CanvasAutosave).where(*scope))
            if current is None:
                raise
            return _conflict(doc, current)
    else:
        if row.version != doc.version:
            return _conflict(doc, row)
        history = (row.history + [{"version": doc.version + 1, "checksum": checksum,
                                   "updated_at": now.isoformat()}])[-100:]
        result = db.execute(update(CanvasAutosave).where(*scope, CanvasAutosave.version == doc.version)
                            .values(content=doc.content, checksum=checksum,
                                    version=doc.version + 1, updated_at=now, history=history)
                            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            db.expire_all()
            current = db.scalar(select(CanvasAutosave).where(*scope))
            if current is None:
                raise Conflict("autosave_deleted", "快照已被删除；请重新加载")
            return _conflict(doc, current)
        db.commit()
        db.refresh(row)
    return {"saved": True, "canvas_id": doc.canvas_id, "version": row.version,
            "updated_at": row.updated_at, "conflict": None}


@router.get("/load/{canvas_id}")
def load_autosave(canvas_id: str, actor: Actor = Depends(get_actor),
                  db: Session = Depends(get_db)) -> dict | None:
    row = db.scalar(select(CanvasAutosave).where(*_scope(actor, canvas_id)))
    if row is None:
        return None
    return {key: getattr(row, key) for key in (
        "id", "canvas_id", "owner_id", "content", "version", "checksum", "created_at", "updated_at")}


@router.get("/status/{canvas_id}")
def check_status(canvas_id: str, since_version: int | None = None,
                 actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(CanvasAutosave).where(*_scope(actor, canvas_id)))
    return {"canvas_id": canvas_id, "modified": bool(row and (
        since_version is None or row.version > since_version)), "version": row.version if row else 0,
        "updated_at": row.updated_at if row else None}


@router.get("/history/{canvas_id}")
def get_history(canvas_id: str, limit: int = Query(default=10, ge=1, le=100),
                actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> dict:
    row = db.scalar(select(CanvasAutosave).where(*_scope(actor, canvas_id)))
    items = list(reversed(row.history))[:limit] if row else []
    return {"canvas_id": canvas_id, "count": len(items), "items": items,
            "retention_limit": 100, "history_kind": "metadata_only"}


@router.delete("/{canvas_id}")
def delete_autosave(canvas_id: str, actor: Actor = Depends(csrf_protected),
                    db: Session = Depends(get_db)) -> dict:
    result = db.execute(delete(CanvasAutosave).where(*_scope(actor, canvas_id)))
    db.commit()
    return {"canvas_id": canvas_id, "deleted": bool(result.rowcount)}
