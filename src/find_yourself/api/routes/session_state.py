"""Session-state persistence HTTP surface (工单 P1-21).

* ``POST /api/session-state/snapshot``            — capture the session's
  restorable state (short-term memory window + tool registry + DSL run
  archive) into the primary database (upsert per ``session_key``),
* ``GET  /api/session-state/restore``             — load the snapshot back
  after a backend restart and replay it into the live runtime; returns both
  the stored snapshot and the freshly re-exported live state so callers can
  verify the round-trip field-by-field,
* ``GET  /api/session-state/windows/{key}``       — read the restored
  in-process memory window (404 until restore ran in this process).

Acceptance (P1-21): snapshot → restart backend → restore → the pre- and
post-restart state snapshots diff to empty (field-by-field JSON equality).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import csrf_protected, get_actor, get_db
from ...db.models import Message
from ...services.errors import NotFound
from ...services.short_term_memory import (
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_MAX_WINDOW_TOKENS,
    build_window,
)
from ...services.state_persistence import (
    build_snapshot_payload,
    cache_window,
    export_dsl_store,
    export_memory_window,
    export_tool_registry,
    import_dsl_store,
    import_memory_window,
    import_tool_registry,
)
from ...services.tool_registry import tool_registry
from . import dsl_canvas as _dsl_routes

router = APIRouter(prefix="/api/session-state", tags=["session-state"])


class SnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_key: str = Field(min_length=1, max_length=200)
    conversation_id: str | None = Field(default=None, max_length=64)
    keep_recent_turns: int = Field(default=DEFAULT_KEEP_RECENT_TURNS, ge=1, le=100)
    max_window_tokens: int = Field(default=DEFAULT_MAX_WINDOW_TOKENS, ge=100, le=100_000)


def _current_dsl_store():
    return _dsl_routes._store


def _load_conversation_messages(db: Session, conversation_id: str) -> list[Message]:
    return list(
        db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.deleted_at.is_(None))
            .order_by(Message.created_at.asc(), Message.id.asc())
        ).scalars().all()
    )


@router.post("/snapshot")
async def create_snapshot(
    body: SnapshotBody,
    actor: object = Depends(csrf_protected),
    db: Session = Depends(get_db),
) -> dict:
    """Capture the session state and persist it (upsert on session_key)."""
    window_data: dict[str, Any] = {
        "summary": "",
        "summarized_message_ids": [],
        "recent_messages": [],
        "token_estimate": 0,
        "original_token_estimate": 0,
        "compressed": False,
        "turns_total": 0,
        "turns_summarized": 0,
        "turns_recent": 0,
    }
    if body.conversation_id:
        messages = _load_conversation_messages(db, body.conversation_id)
        window = build_window(
            messages,
            keep_recent_turns=body.keep_recent_turns,
            max_window_tokens=body.max_window_tokens,
        )
        window_data = export_memory_window(window)

    payload = build_snapshot_payload(
        session_key=body.session_key,
        conversation_id=body.conversation_id,
        memory_window=window_data,
        tool_registry=export_tool_registry(tool_registry),
        dsl_runs=export_dsl_store(_current_dsl_store()),
    )

    from ...services.state_persistence import SessionStateService

    row = SessionStateService(db).save(
        session_key=body.session_key,
        conversation_id=body.conversation_id,
        payload=payload,
    )
    return {"status": "ok", "snapshot": row.payload}


@router.get("/restore")
async def restore_state(
    session_key: str = Query(min_length=1, max_length=200),
    actor: object = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    """Rebuild the live runtime from the persisted snapshot.

    Replays, in order: the short-term memory window (cached in-process for
    later consumers), the tool registry (idempotent re-register) and the DSL
    run archive (runs become visible again on ``/api/dsl-canvas/runs``).
    """
    from ...services.state_persistence import SessionStateService

    row = SessionStateService(db).load(session_key)
    if row is None:
        raise NotFound(
            "session_state_not_found",
            f"No session-state snapshot for key '{session_key}'",
            404,
        )
    payload = row.payload
    state = payload.get("state", {})

    window = import_memory_window(state.get("memory_window", {}))
    cache_window(session_key, window)

    tools_reloaded = import_tool_registry(state.get("tool_registry", {}), tool_registry)
    runs_reloaded = import_dsl_store(state.get("dsl_runs", {}), _current_dsl_store())

    # Re-export the live runtime right after the replay so the caller can
    # verify snapshot vs restored state field-by-field (P1-21 acceptance).
    live_state = {
        "memory_window": export_memory_window(window),
        "tool_registry": export_tool_registry(tool_registry),
        "dsl_runs": export_dsl_store(_current_dsl_store()),
    }
    return {
        "restored": True,
        "session_key": session_key,
        "snapshot": payload,
        "live_state": live_state,
        "reloaded": {"tools": tools_reloaded, "dsl_runs": runs_reloaded, "memory_window": True},
    }


@router.get("/windows/{session_key}")
async def read_window(
    session_key: str,
    actor: object = Depends(get_actor),
) -> dict:
    from ...services.state_persistence import get_cached_window, export_memory_window

    window = get_cached_window(session_key)
    if window is None:
        raise NotFound(
            "window_not_restored",
            f"No memory window restored in this process for key '{session_key}'",
            404,
        )
    return {"session_key": session_key, "window": export_memory_window(window)}
