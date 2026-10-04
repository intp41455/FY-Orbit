"""Read-only adapter for the personal-kb historical archive (§4.4, M12, M13).

Strict read-only: opens SQLite with URI ``mode=ro``, never executes writes.
Inspects stats first so users choose scope before importing.

P3 · 硬编码路径治理：路径**不再**默认硬编码为某台机器的 ``D:\\person-kb\\kb.db``，
而是「显式参数 > 环境变量 ``FY_PERSON_KB_DB``」两段式取值；两者都没有时
适配器如实报 ``available=False``（未配置），绝不假装可读。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Generator

#: 环境变量名：部署时通过它注入个人知识库 SQLite 路径。
PERSON_KB_DB_ENV = "FY_PERSON_KB_DB"


class PersonKbAdapter:
    def __init__(self, db_path: str | None = None):
        # P3 · 取值顺序：显式参数 > 环境变量 > 空（未配置，如实报不可用）。
        resolved = db_path if db_path is not None else os.environ.get(PERSON_KB_DB_ENV, "")
        self.db_path = (resolved or "").strip()
        if not self.db_path:
            return
        if not os.path.exists(self.db_path):
            # Check alternative default names if directory provided
            if os.path.isdir(self.db_path):
                candidate = os.path.join(self.db_path, "kb.db")
                if os.path.exists(candidate):
                    self.db_path = candidate

    def is_configured(self) -> bool:
        """P3 · 未配置路径时如实返回 False（供调用方预检，而不是打开时报错）。"""
        return bool(self.db_path)

    def _get_ro_connection(self) -> sqlite3.Connection:
        # Use SQLite URI read-only mode to guarantee zero accidental modifications
        norm_path = os.path.abspath(self.db_path).replace("\\", "/")
        uri = f"file:///{norm_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def inspect_stats(self) -> dict[str, Any]:
        """Read-only statistics for pre-flight review."""
        if not self.db_path or not os.path.exists(self.db_path):
            return {
                "available": False,
                "path": self.db_path or "",
                "detail": "" if self.db_path else
                f"未配置个人知识库路径：请设置环境变量 {PERSON_KB_DB_ENV} 或显式传入 db_path",
                "sessions_count": 0,
                "messages_count": 0,
                "platforms": [],
            }
        conn = self._get_ro_connection()
        try:
            cur = conn.cursor()
            # Inspect sessions count and platforms
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT platform) FROM sessions")
            sess_row = cur.fetchone()
            sessions_count = sess_row[0] if sess_row else 0

            cur.execute("SELECT DISTINCT platform FROM sessions WHERE platform IS NOT NULL")
            platforms = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) FROM messages")
            msg_row = cur.fetchone()
            messages_count = msg_row[0] if msg_row else 0

            return {
                "available": True,
                "path": self.db_path,
                "sessions_count": sessions_count,
                "messages_count": messages_count,
                "platforms": sorted(platforms),
            }
        finally:
            conn.close()

    def iter_conversations(
        self,
        platforms: list[str] | None = None,
        limit: int | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield sessions and their ordered messages in read-only stream."""
        if not self.db_path or not os.path.exists(self.db_path):
            return
        conn = self._get_ro_connection()
        try:
            cur = conn.cursor()
            query = "SELECT id, platform, title, created_at FROM sessions"
            params: list[Any] = []
            if platforms:
                placeholders = ", ".join("?" for _ in platforms)
                query += f" WHERE platform IN ({placeholders})"
                params.extend(platforms)
            query += " ORDER BY created_at ASC"
            if limit:
                query += f" LIMIT {int(limit)}"

            cur.execute(query, params)
            sessions = cur.fetchall()

            msg_cur = conn.cursor()
            for s in sessions:
                msg_cur.execute(
                    "SELECT id, role, content, created_at FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                    (s["id"],),
                )
                msgs = [
                    {
                        "id": str(m["id"]),
                        "role": str(m["role"]),
                        "content": str(m["content"]),
                        "created_at": str(m["created_at"]),
                    }
                    for m in msg_cur.fetchall()
                ]
                yield {
                    "id": str(s["id"]),
                    "platform": str(s["platform"] or "unknown"),
                    "title": str(s["title"] or ""),
                    "created_at": str(s["created_at"]),
                    "messages": msgs,
                }
        finally:
            conn.close()
