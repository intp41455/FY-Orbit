"""Context compression service with source tracking and tag retention (M11).

Ensures that whenever long context is compressed, summarized, or windowed,
the output dictionary maintains an explicit list of source message IDs and
preserves metadata tags so that every generated insight can trace back to
the underlying messages.
"""
from __future__ import annotations

from typing import Any

from ..db.models import Message


class ContextService:
    def compress_messages(
        self,
        messages: list[Message],
        max_tokens: int = 1500,
    ) -> dict[str, Any]:
        source_ids: list[str] = []
        tags: dict[str, list[str]] = {"roles": [], "domains": []}
        lines: list[str] = []

        total_chars = 0
        char_limit = max_tokens * 4

        for m in messages:
            source_ids.append(m.id)
            if m.role not in tags["roles"]:
                tags["roles"].append(m.role)

            line = f"[{m.role}]: {m.content.strip()}"
            if total_chars + len(line) > char_limit and lines:
                # Truncate oldest or summary marker
                lines.append("... [context compressed to fit limit] ...")
                break
            lines.append(line)
            total_chars += len(line)

        return {
            "text": "\n".join(lines),
            "source_ids": source_ids,
            "tags": tags,
            "token_estimate": total_chars // 4,
        }
