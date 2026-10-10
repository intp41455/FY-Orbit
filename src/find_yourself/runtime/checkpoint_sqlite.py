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

异步图（``ainvoke``）不在本切片范围：现有调用面（evaluation / recovery / 测试）
全部是同步 ``invoke``；异步变体沿用基类默认（不实现即显式失败，不许假成功）。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    step_index INTEGER,              -- NEW: atomic step number within this checkpoint
    idempotency_key TEXT,           -- NEW: hash(task_id + step_id + op) for deduplication
    resume_count INTEGER DEFAULT 0, -- NEW: how many times this checkpoint was resumed
    type TEXT,
    checkpoint BLOB NOT NULL,
    mtype TEXT,
    metadata BLOB,
    created_at REAL NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS ix_ckp_thread
    ON checkpoints (thread_id, checkpoint_ns);
CREATE INDEX IF NOT EXISTS ix_ckp_idem
    ON checkpoints (thread_id, checkpoint_ns, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
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
        finally:
            conn.close()

    @staticmethod
    def _cfg_parts(config: dict | None) -> tuple[str, str, str | None]:
        configurable = (config or {}).get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        if not thread_id:
            raise ValueError("checkpoint config requires configurable.thread_id")
        ns = str(configurable.get("checkpoint_ns") or "")
        checkpoint_id = configurable.get("checkpoint_id")
        return thread_id, ns, (str(checkpoint_id) if checkpoint_id else None)

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
                # Update new columns if present in configurable
                cfg = (config or {}).get("configurable", {})
                updates = []
                params = []
                if "step_index" in cfg and cfg["step_index"] is not None:
                    updates.append("step_index = ?")
                    params.append(cfg["step_index"])
                if "idempotency_key" in cfg and cfg["idempotency_key"] is not None:
                    updates.append("idempotency_key = ?")
                    params.append(cfg["idempotency_key"])
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
            if checkpoint_id is not None:
                row = conn.execute(
                    "SELECT rowid, checkpoint_id, parent_checkpoint_id, type, checkpoint, "
                    "mtype, metadata, step_index, idempotency_key, resume_count FROM checkpoints "
                    "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, ns, checkpoint_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT rowid, checkpoint_id, parent_checkpoint_id, type, checkpoint, "
                    "mtype, metadata, step_index, idempotency_key, resume_count FROM checkpoints "
                    "WHERE thread_id=? AND checkpoint_ns=? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (thread_id, ns),
                ).fetchone()
            if row is None:
                return None
            (rowid, ck_id, parent_id, ck_type, ck_blob, md_type, md_blob,
             step_index, idempotency_key, resume_count) = row
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
        # Build extensible config with new checkpoint fields
        cfg = {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": ck_id}
        if step_index is not None:
            cfg["step_index"] = step_index
        if idempotency_key is not None:
            cfg["idempotency_key"] = idempotency_key
        if resume_count is not None:
            cfg["resume_count"] = resume_count
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
               "type, checkpoint, mtype, metadata, step_index, idempotency_key, "
               "resume_count, rowid FROM checkpoints "
               f"{where} ORDER BY rowid DESC")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        for (t_id, t_ns, ck_id, parent_id, ck_type, ck_blob, md_type, md_blob,
             step_index, idempotency_key, resume_count, _rowid) in rows:
            metadata = self.serde.loads_typed((md_type, md_blob)) if md_blob is not None else {}
            if filter and any(metadata.get(k) != v for k, v in filter.items()):
                continue
            cfg = {"thread_id": t_id, "checkpoint_ns": t_ns, "checkpoint_id": ck_id}
            if step_index is not None:
                cfg["step_index"] = step_index
            if idempotency_key is not None:
                cfg["idempotency_key"] = idempotency_key
            if resume_count is not None:
                cfg["resume_count"] = resume_count
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
                conn.execute(
                    "UPDATE checkpoints SET resume_count = resume_count + 1 "
                    "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    (thread_id, ns, ck_id),
                )
                conn.commit()
            finally:
                conn.close()
