"""Session-state persistence layer (工单 P1-21).

Defines the persistable session-state snapshot and the adapters that
export/import the three runtime state sources it covers. Everything here is
additive: the existing services are consumed through their public APIs only
(``short_term_memory.MemoryWindow``, ``tool_registry.ToolRegistryService``,
``dsl_canvas.DslRunStore`` / ``RunResult`` / ``NodeLog``) — none of their
internals are modified.

Snapshot payload schema (version 1)::

    {
      "schema_version": 1,
      "session_key": "conv-...",          # unique, upsert key
      "conversation_id": "conv-...",
      "captured_at": "<iso8601>",          # volatile metadata, excluded from diff
      "state": {
        "memory_window": {
          "summary": "...",
          "summarized_message_ids": [...],
          "recent_messages": [{"id", "role", "content"}, ...],
          "token_estimate": int, "original_token_estimate": int,
          "compressed": bool,
          "turns_total": int, "turns_summarized": int, "turns_recent": int
        },
        "tool_registry": {
          "tools": [ {"name", "description", "parameters", "entry"}, ... ]
        },
        "dsl_runs": { "runs": [ <RunResult.to_dict()>, ... ] }
      }
    }

``captured_at`` is bookkeeping only; every field under ``state`` is
deterministic, so a faithful restore must reproduce ``state`` exactly — that
equality (field-by-field) is the P1-21 acceptance criterion.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.session_state_models import SessionStateSnapshot
from .dsl_canvas import DslRunStore, NodeLog, RunResult
from .short_term_memory import MemoryWindow


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SNAPSHOT_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# MemoryWindow adapters (short-term memory, P1-07)
# ---------------------------------------------------------------------------


def export_memory_window(window: MemoryWindow) -> dict[str, Any]:
    """Serialise a :class:`MemoryWindow` into a JSON-safe dict."""
    return {
        "summary": window.summary,
        "summarized_message_ids": list(window.summarized_message_ids),
        "recent_messages": [
            {
                "id": getattr(m, "id", ""),
                "role": getattr(m, "role", ""),
                "content": getattr(m, "content", ""),
            }
            for m in window.recent_messages
        ],
        "token_estimate": window.token_estimate,
        "original_token_estimate": window.original_token_estimate,
        "compressed": window.compressed,
        "turns_total": window.turns_total,
        "turns_summarized": window.turns_summarized,
        "turns_recent": window.turns_recent,
    }


def import_memory_window(data: dict[str, Any]) -> MemoryWindow:
    """Rebuild a :class:`MemoryWindow` from :func:`export_memory_window` output.

    ``recent_messages`` are restored as lightweight message-like objects
    (``id``/``role``/``content`` attributes) — sufficient for
    ``MemoryWindow.render()`` and downstream window building.
    """
    recent = [
        SimpleNamespace(id=m.get("id", ""), role=m.get("role", ""), content=m.get("content", ""))
        for m in data.get("recent_messages", [])
    ]
    return MemoryWindow(
        summary=data.get("summary", ""),
        summarized_message_ids=list(data.get("summarized_message_ids", [])),
        recent_messages=recent,
        token_estimate=int(data.get("token_estimate", 0)),
        original_token_estimate=int(data.get("original_token_estimate", 0)),
        compressed=bool(data.get("compressed", False)),
        turns_total=int(data.get("turns_total", 0)),
        turns_summarized=int(data.get("turns_summarized", 0)),
        turns_recent=int(data.get("turns_recent", 0)),
    )


# In-process cache of restored windows, so post-restart consumers can pick a
# session's short-term memory up where the snapshot left it.
_restored_windows: dict[str, MemoryWindow] = {}


def cache_window(session_key: str, window: MemoryWindow) -> None:
    _restored_windows[session_key] = window


def get_cached_window(session_key: str) -> MemoryWindow | None:
    return _restored_windows.get(session_key)


# ---------------------------------------------------------------------------
# Tool registry adapters (P1-05)
# ---------------------------------------------------------------------------


def export_tool_registry(registry: Any) -> dict[str, Any]:
    """Serialise the registered tools (stable fields only, sorted by name).

    ``registered_at``/``updated_at`` are intentionally omitted: re-registering
    after a restart stamps fresh timestamps, and they carry no functional
    state. The restored registry is functionally identical.
    """
    return {
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
                "entry": t["entry"],
            }
            for t in registry.list_tools()
        ],
    }


def import_tool_registry(data: dict[str, Any], registry: Any) -> int:
    """Re-register every snapshotted tool (idempotent). Returns the count."""
    count = 0
    for tool in data.get("tools", []):
        registry.register(
            name=tool["name"],
            description=tool["description"],
            parameters=tool["parameters"],
            entry=tool["entry"],
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# DSL run-store adapters (P1-18)
# ---------------------------------------------------------------------------


def export_dsl_store(store: DslRunStore) -> dict[str, Any]:
    """Serialise every archived run via the store's public read APIs."""
    runs = []
    for run_id in store.list_ids():
        payload = store.get(run_id)
        if payload is not None:
            runs.append(payload)
    return {"runs": runs}


def _node_log_from_dict(d: dict[str, Any]) -> NodeLog:
    return NodeLog(
        node_id=d.get("node_id", ""),
        node_type=d.get("node_type", ""),
        verb=d.get("verb"),
        status=d.get("status", "succeeded"),
        input=d.get("input"),
        output=d.get("output"),
        error=d.get("error"),
        started_at=d.get("started_at", ""),
        finished_at=d.get("finished_at", ""),
    )


def _run_result_from_dict(d: dict[str, Any]) -> RunResult:
    return RunResult(
        run_id=d["run_id"],
        status=d.get("status", "succeeded"),
        doc=d.get("dsl", {}),
        logs=[_node_log_from_dict(l) for l in d.get("logs", [])],
        output=d.get("output"),
        error=d.get("error"),
        created_at=d.get("created_at", ""),
    )


def import_dsl_store(data: dict[str, Any], store: DslRunStore) -> int:
    """Replay snapshotted runs back into a (fresh) :class:`DslRunStore`.

    Reconstruction uses the public ``RunResult``/``NodeLog`` dataclasses plus
    ``store.save`` — the stored payload round-trips unchanged, including
    ``created_at`` and the per-node logs.
    """
    count = 0
    for payload in data.get("runs", []):
        store.save(_run_result_from_dict(payload))
        count += 1
    return count


# ---------------------------------------------------------------------------
# Snapshot assembly + DB persistence (upsert / load)
# ---------------------------------------------------------------------------


def build_snapshot_payload(
    *,
    session_key: str,
    conversation_id: str | None,
    memory_window: dict[str, Any],
    tool_registry: dict[str, Any],
    dsl_runs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "session_key": session_key,
        "conversation_id": conversation_id,
        "captured_at": _now(),
        "state": {
            "memory_window": memory_window,
            "tool_registry": tool_registry,
            "dsl_runs": dsl_runs,
        },
    }


class SessionStateService:
    """Upsert/load session-state snapshot rows in the primary database."""

    def __init__(self, db: Session):
        self._db = db

    def save(
        self,
        *,
        session_key: str,
        conversation_id: str | None,
        payload: dict[str, Any],
    ) -> SessionStateSnapshot:
        row = self._db.execute(
            select(SessionStateSnapshot).where(SessionStateSnapshot.session_key == session_key)
        ).scalar_one_or_none()
        if row is None:
            row = SessionStateSnapshot(
                id=f"sss_{uuid.uuid4().hex}",
                session_key=session_key,
                conversation_id=conversation_id,
                schema_version=payload.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
                payload=payload,
            )
            self._db.add(row)
        else:
            row.conversation_id = conversation_id
            row.schema_version = payload.get("schema_version", SNAPSHOT_SCHEMA_VERSION)
            row.payload = payload
        self._db.commit()
        self._db.refresh(row)
        return row

    def load(self, session_key: str) -> SessionStateSnapshot | None:
        return self._db.execute(
            select(SessionStateSnapshot).where(SessionStateSnapshot.session_key == session_key)
        ).scalar_one_or_none()
