"""T6-B 持久化检查点（补 G1）：sqlite 文件落盘的 LangGraph checkpointer。

G1 现场：``runtime/graph.py`` 此前 ``cp = checkpointer or MemorySaver()``——检查点
只在内存，进程一崩阶段进度全丢（S4 直接违反）。本模块提供 **零新依赖** 的
持久化替代：标准库 ``sqlite3`` 单文件库表，配合 LangGraph 自带 serde
（``JsonPlusSerializer``）存取，进程重启后检查点仍在。

设计决定：

* **不引入** ``langgraph-checkpoint-sqlite``——产品是本地单机零服务形态，
  检查点存储用标准库 sqlite3 自持，与主库（SQLAlchemy）解耦但同为"落盘 DB"，
  满足红线 1（不许内存态）。
* **每次操作独立开连接并在 finally 显式 close**——sqlite3 的上下文管理器只管
  事务不管关闭，漏 close 会锁住文件（Windows 上直接阻塞删除/重开）。
  检查点节奏是每个 super-step 一次，开关销开销可忽略，换取完全的线程安全。
* ``latest`` 语义按**插入序**（sqlite rowid）而非 checkpoint_id 排序——
  不依赖 id 的字典序性质，任何 langgraph 版本的 id 格式都正确。

异步图（``ainvoke``）已支持：``sqlite3`` 是阻塞调用，异步侧（``aput`` /
``aput_writes`` / ``aget_tuple`` / ``alist`` / ``arecord_resume``）统一用
``asyncio.to_thread`` 委托到同一份同步实现——单一事实来源，两条路径的行为
（包括 ``record_resume`` 的 resume 上限）不可能漂移。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from typing import Any, AsyncIterator, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from ..contracts import ValidationFailed

# 上限：为失控的恢复循环兜底（纵深防御，与运行时循环守卫配套）。
MAX_RESUME_COUNT = 1000

#: 检查点的业务扩展列（用户设计里超出 LangGraph 原生那部分，共 10 列；
#: 连同 9 列 LangGraph 原生列，``_SCHEMA`` 合计 19 列）。
#: 单一事实来源：``_SCHEMA`` 建新库、``_migrate_add_columns`` 升旧库、
#: ``put`` 写入、``get_tuple`` / ``list`` 读回，四处都从这张表派生。
_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_index", "INTEGER"),
    ("idempotency_key", "TEXT"),
    ("resume_count", "INTEGER DEFAULT 0"),
    ("task_id", "TEXT"),
    ("agent_id", "TEXT"),
    ("status", "TEXT"),
    ("intermediate_result", "TEXT"),
    ("next_action", "TEXT"),
    ("lineage", "TEXT"),          # JSON 数组文本：血缘链上的 agent id 名单
    ("version", "INTEGER DEFAULT 1"),
)

#: 允许调用方经 ``configurable`` 写入的列。``resume_count`` 由 ``record_resume``
#: 独占自增，不接受外部写入（否则上限语义可被绕过）。
_WRITABLE_COLUMNS: tuple[str, ...] = tuple(
    name for name, _ddl in _EXTRA_COLUMNS if name != "resume_count"
)

#: 读回时回填到 ``configurable`` 的列（含只读的 resume_count）。
_READ_COLUMNS: tuple[str, ...] = tuple(name for name, _ddl in _EXTRA_COLUMNS)

_EXTRA_SELECT = ", ".join(_READ_COLUMNS)


def _encode_extra(value: Any) -> Any:
    """列表/字典（``lineage`` 这类）落盘前序列化为 JSON 文本，其余原样存。"""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    step_index INTEGER,              -- NEW: atomic step number within this checkpoint
    idempotency_key TEXT,           -- NEW: hash(task_id + step_id + op) for deduplication
    resume_count INTEGER DEFAULT 0, -- NEW: how many times this checkpoint was resumed
    task_id TEXT,                   -- NEW: owning business task id
    agent_id TEXT,                  -- NEW: owning agent id
    status TEXT,                    -- NEW: business status snapshot (in_progress/failed/...)
    intermediate_result TEXT,       -- NEW: partial result payload (text or JSON)
    next_action TEXT,               -- NEW: planned next step, drives resumption
    lineage TEXT,                   -- NEW: JSON array of ancestor agent ids
    version INTEGER DEFAULT 1,      -- NEW: checkpoint record schema version
    type TEXT,
    checkpoint BLOB NOT NULL,
    mtype TEXT,
    metadata BLOB,
    created_at REAL NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS ix_ckp_thread
    ON checkpoints (thread_id, checkpoint_ns);
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx)
);
"""


class SqliteCheckpointer(BaseCheckpointSaver):
    """按 (thread_id, checkpoint_ns) 存取检查点，文件落盘、重启后可续。"""

    def __init__(self, path: str):
        super().__init__()
        self._path = str(path)
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- internals ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        import os

        parent = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(parent, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            # Migration: add new columns if they don't exist in old databases
            self._migrate_add_columns(conn)
            # Create idempotency index separately (may fail if columns missing)
            self._create_idem_index(conn)
        finally:
            conn.close()

    def _migrate_add_columns(self, conn: sqlite3.Connection) -> None:
        """Incrementally upgrade an old ``checkpoints`` table to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` never touches an existing table, so every
        column added after a database was first created has to be grafted on by
        ``ALTER TABLE``. The column list is derived from ``_EXTRA_COLUMNS`` so a
        new column is migrated by editing that single table.
        """
        # Check which columns exist
        cursor = conn.execute("PRAGMA table_info(checkpoints)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        for col_name, col_def in _EXTRA_COLUMNS:
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE checkpoints ADD COLUMN {col_name} {col_def}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column already exists or other issue

    def _create_idem_index(self, conn: sqlite3.Connection) -> None:
        """Create idempotency index, handling cases where column doesn't exist."""
        try:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_ckp_idem
                ON checkpoints (thread_id, checkpoint_ns, idempotency_key)
                WHERE idempotency_key IS NOT NULL
            """)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Index already exists or columns missing

    @staticmethod
    def _cfg_parts(config: dict | None) -> tuple[str, str, str | None]:
        configurable = (config or {}).get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        if not thread_id:
            raise ValueError("checkpoint config requires configurable.thread_id")
        ns = str(configurable.get("checkpoint_ns") or "")
        checkpoint_id = configurable.get("checkpoint_id")
        return thread_id, ns, (str(checkpoint_id) if checkpoint_id else None)

    @staticmethod
    def _extra_config(row: Sequence[Any], offset: int) -> dict[str, Any]:
        """Rebuild the ``_EXTRA_COLUMNS`` slice of a SELECT row as a config dict.

        ``offset`` is the column count of the base columns in front of them
        (``get_tuple`` has 7, ``list`` has 8). ``None`` means "column absent /
        never written" and is skipped so callers only see real values.
        """
        extra = zip(_READ_COLUMNS, row[offset:offset + len(_READ_COLUMNS)])
        return {name: value for name, value in extra if value is not None}

    # -- BaseCheckpointSaver contract ---------------------------------------

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict:
        thread_id, ns, parent_id = self._cfg_parts(config)
        ck_type, ck_blob = self.serde.dumps_typed(checkpoint)
        md_type, md_blob = self.serde.dumps_typed(metadata)
        import time

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints "
                    "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                    " type, checkpoint, mtype, metadata, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (thread_id, ns, checkpoint["id"], parent_id,
                     ck_type, ck_blob, md_type, md_blob, time.time()),
                )
                conn.commit()
                # Write the business columns carried by config.configurable.
                # Kept as a follow-up UPDATE rather than extra INSERT columns so
                # the base statement stays byte-identical to the LangGraph
                # contract; absent keys simply leave the column at its default.
                cfg = (config or {}).get("configurable", {})
                updates = []
                params = []
                for col in _WRITABLE_COLUMNS:
                    value = cfg.get(col)
                    if value is None:
                        continue
                    updates.append(f"{col} = ?")
                    params.append(_encode_extra(value))
                if updates:
                    params.extend([thread_id, ns, checkpoint["id"]])
                    conn.execute(
                        f"UPDATE checkpoints SET {', '.join(updates)} "
                        "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                        params,
                    )
                    conn.commit()
            finally:
                conn.close()
        return {
            "configurable": {
                **(config or {}).get("configurable", {}),
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, ns, checkpoint_id = self._cfg_parts(config)
        if checkpoint_id is None:
            return
        rows = []
        for idx, (channel, value) in enumerate(writes):
            v_type, v_blob = self.serde.dumps_typed(value)
            rows.append((thread_id, ns, checkpoint_id, task_id, task_path, idx,
                         channel, v_type, v_blob))
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO checkpoint_writes "
                    "(thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, "
                    " channel, type, blob) VALUES (?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id, ns, checkpoint_id = self._cfg_parts(config)
        conn = self._connect()
        try:
            base_cols = ("rowid, checkpoint_id, parent_checkpoint_id, type, checkpoint, "
                         "mtype, metadata")
            if checkpoint_id is not None:
                row = conn.execute(
                    f"SELECT {base_cols}, {_EXTRA_SELECT} FROM checkpoints "
                    "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, ns, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT {base_cols}, {_EXTRA_SELECT} FROM checkpoints "
                    "WHERE thread_id=? AND checkpoint_ns=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (thread_id, ns),
                ).fetchone()
            if row is None:
                return None
            (rowid, ck_id, parent_id, ck_type, ck_blob, md_type, md_blob) = row[:7]
            pending_rows = conn.execute(
                "SELECT task_id, channel, type, blob FROM checkpoint_writes "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? ORDER BY idx",
                (thread_id, ns, ck_id),
            ).fetchall()
        finally:
            conn.close()

        pending: list[tuple[str, str, Any]] = [
            (w_task_id, w_channel, self.serde.loads_typed((w_type, w_blob)))
            for (w_task_id, w_channel, w_type, w_blob) in pending_rows
        ]
        checkpoint = self.serde.loads_typed((ck_type, ck_blob))
        metadata = self.serde.loads_typed((md_type, md_blob)) if md_blob is not None else {}
        parent_config = (
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns,
                              "checkpoint_id": parent_id}}
            if parent_id else None
        )
        # Build extensible config with the business columns
        cfg = {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": ck_id}
        cfg.update(self._extra_config(row, 7))
        return CheckpointTuple(
            config={"configurable": cfg},
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending,
        )

    def list(
        self,
        config: dict | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is not None:
            thread_id, ns, _ = self._cfg_parts(config)
            where, params = "WHERE thread_id=? AND checkpoint_ns=?", [thread_id, ns]
        else:
            where, params = "", []
        if before is not None:
            b_thread, b_ns, b_id = self._cfg_parts(before)
            if b_id:
                where += (" AND " if where else "WHERE ") + \
                    "rowid < COALESCE((SELECT MAX(rowid) FROM checkpoints WHERE " \
                    "thread_id=? AND checkpoint_ns=? AND checkpoint_id=?), 0)"
                params += [b_thread, b_ns, b_id]
        sql = ("SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
               "type, checkpoint, mtype, metadata, "
               f"{_EXTRA_SELECT}, rowid FROM checkpoints "
               f"{where} ORDER BY rowid DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        for row in rows:
            (t_id, t_ns, ck_id, parent_id, ck_type, ck_blob, md_type, md_blob) = row[:8]
            metadata = self.serde.loads_typed((md_type, md_blob)) if md_blob is not None else {}
            if filter and any(metadata.get(k) != v for k, v in filter.items()):
                continue
            cfg = {"thread_id": t_id, "checkpoint_ns": t_ns, "checkpoint_id": ck_id}
            cfg.update(self._extra_config(row, 8))
            yield CheckpointTuple(
                config={"configurable": cfg},
                checkpoint=self.serde.loads_typed((ck_type, ck_blob)),
                metadata=metadata,
                parent_config=(
                    {"configurable": {"thread_id": t_id, "checkpoint_ns": t_ns,
                                      "checkpoint_id": parent_id}}
                    if parent_id else None
                ),
                pending_writes=[],
            )

    # -- extras --------------------------------------------------------------

    def delete_thread(self, thread_id: str, checkpoint_ns: str = "") -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=?",
                    (thread_id, checkpoint_ns),
                )
                conn.execute(
                    "DELETE FROM checkpoint_writes WHERE thread_id=? AND checkpoint_ns=?",
                    (thread_id, checkpoint_ns),
                )
                conn.commit()
            finally:
                conn.close()

    def record_resume(self, config: dict, note: str = "") -> None:
        """Increment resume_count when checkpoint is used for resumption."""
        thread_id, ns, ck_id = self._cfg_parts(config)
        if ck_id is None:
            return
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT resume_count FROM checkpoints "
                    "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, ns, ck_id),
                ).fetchone()
                current = row[0] if row and row[0] is not None else 0
                if current >= MAX_RESUME_COUNT:
                    raise ValidationFailed(
                        "resume_count_exceeded",
                        f"checkpoint resume_count reached cap {MAX_RESUME_COUNT}",
                    )
                conn.execute(
                    "UPDATE checkpoints SET resume_count = resume_count + 1 "
                    "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, ns, ck_id),
                )
                conn.commit()
            finally:
                conn.close()

    # -- async variants -------------------------------------------------------
    # ``sqlite3`` is blocking, so every async entry point hops onto a worker
    # thread and delegates to the synchronous implementation above. One code
    # path means the async graph (``ainvoke``) cannot drift from ``invoke`` —
    # including the ``record_resume`` cap, which raises through ``to_thread``.

    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict:
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: dict | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for tup in rows:
            yield tup

    async def arecord_resume(self, config: dict, note: str = "") -> None:
        """Async twin of :meth:`record_resume` (same cap, same exception)."""
        await asyncio.to_thread(self.record_resume, config, note)
