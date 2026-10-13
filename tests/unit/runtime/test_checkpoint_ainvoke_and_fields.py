"""FY-A-3（N2「状态管理」，对标 LangGraph checkpointer）：业务字段落盘 + 异步路径。

覆盖三件事：

* 用户设计的 11 字段里超出 LangGraph 原生的 7 个业务列
  （task_id/agent_id/status/intermediate_result/next_action/lineage/version）
  可经 ``config.configurable`` 写入并原样读回；
* 旧 schema 库（缺这些列）能被 ``_migrate_add_columns`` 平滑升级——不崩、
  不丢既有检查点、新列取默认值；
* 异步路径（``ainvoke`` / ``aget_tuple`` / ``alist`` / ``arecord_resume``）
  与同步路径行为一致，resume 上限在异步侧同样生效。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import TypedDict

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from find_yourself.runtime.checkpoint_sqlite import (
    MAX_RESUME_COUNT,
    SqliteCheckpointer,
)
from find_yourself.services.errors import ValidationFailed

#: 7 个业务字段的写入值（lineage 故意给名单以覆盖 JSON 编码分支）。
BUSINESS: dict[str, object] = {
    "task_id": "task-7",
    "agent_id": "agent-planner",
    "status": "in_progress",
    "intermediate_result": '{"draft": "partial"}',
    "next_action": "call research tool",
    "lineage": ["root", "planner"],
    "version": 3,
}

#: 本次新增的 7 列（不含上一轮已存在的 step_index/idempotency_key/resume_count）。
NEW_COLUMNS = (
    "task_id", "agent_id", "status", "intermediate_result",
    "next_action", "lineage", "version",
)

#: 迁移前的 checkpoints 表：只有 LangGraph 原生列 + created_at。
_OLD_SCHEMA = """
CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB NOT NULL,
    mtype TEXT,
    metadata BLOB,
    created_at REAL NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
"""


class St(TypedDict, total=False):
    x: int


def _app(saver: SqliteCheckpointer):
    g = StateGraph(St)
    g.add_node("a", lambda s: {"x": s.get("x", 0) + 1})
    g.add_edge(START, "a")
    g.add_edge("a", END)
    return g.compile(checkpointer=saver)


def _columns(db: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(checkpoints)")}
    finally:
        conn.close()


def _set_resume_count(db: str, cfg: dict, value: int) -> None:
    c = cfg["configurable"]
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE checkpoints SET resume_count = ? "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
            (value, c["thread_id"], c["checkpoint_ns"], c["checkpoint_id"]),
        )
        conn.commit()
    finally:
        conn.close()


# -- ① 7 个新字段可写入并读回 -------------------------------------------------


def test_new_schema_has_all_business_columns(tmp_path):
    db = str(tmp_path / "ck.db")
    SqliteCheckpointer(db)
    assert set(NEW_COLUMNS) <= _columns(db)


def test_business_fields_round_trip(tmp_path):
    db = str(tmp_path / "ck.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-fields", **BUSINESS}}

    _app(saver).invoke({"x": 0}, config=cfg)

    latest = saver.get_tuple({"configurable": {"thread_id": "th-fields"}})
    assert latest is not None
    got = latest.config["configurable"]
    assert got["task_id"] == "task-7"
    assert got["agent_id"] == "agent-planner"
    assert got["status"] == "in_progress"
    assert got["intermediate_result"] == '{"draft": "partial"}'
    assert got["next_action"] == "call research tool"
    # lineage 是名单：落盘为 JSON 文本，读回仍是名单语义。
    assert json.loads(got["lineage"]) == ["root", "planner"]
    assert got["version"] == 3


def test_business_fields_come_back_from_list(tmp_path):
    db = str(tmp_path / "ck.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-list", **BUSINESS}}

    _app(saver).invoke({"x": 0}, config=cfg)

    tuples = list(saver.list({"configurable": {"thread_id": "th-list"}}))
    assert tuples
    for tup in tuples:
        assert tup.config["configurable"]["task_id"] == "task-7"
        assert tup.config["configurable"]["version"] == 3
        assert json.loads(tup.config["configurable"]["lineage"]) == ["root", "planner"]


def test_resume_count_is_not_writable_from_configurable(tmp_path):
    """resume_count 由 record_resume 独占，外部写入不得绕过上限。"""
    db = str(tmp_path / "ck.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-ro", "resume_count": 999_999}}

    _app(saver).invoke({"x": 0}, config=cfg)

    latest = saver.get_tuple({"configurable": {"thread_id": "th-ro"}})
    assert latest is not None
    assert latest.config["configurable"]["resume_count"] == 0


# -- ② 旧 schema 库增量迁移 ---------------------------------------------------


def _seed_old_db(db: str) -> None:
    serde = JsonPlusSerializer()
    ck_type, ck_blob = serde.dumps_typed({
        "v": 1,
        "id": "ck-old",
        "ts": "2026-01-01T00:00:00+00:00",
        "channel_values": {"x": 41},
        "channel_versions": {"x": "1"},
        "versions_seen": {},
    })
    conn = sqlite3.connect(db)
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
            "parent_checkpoint_id, type, checkpoint, mtype, metadata, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("th-old", "", "ck-old", None, ck_type, ck_blob, None, None, 0.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_old_schema_db_is_upgraded(tmp_path):
    db = str(tmp_path / "old.db")
    _seed_old_db(db)
    assert not (set(NEW_COLUMNS) & _columns(db))

    SqliteCheckpointer(db)  # 迁移不得抛异常

    cols = _columns(db)
    assert set(NEW_COLUMNS) <= cols
    assert {"step_index", "idempotency_key", "resume_count"} <= cols


def test_old_row_survives_migration_with_defaults(tmp_path):
    db = str(tmp_path / "old.db")
    _seed_old_db(db)
    saver = SqliteCheckpointer(db)

    tup = saver.get_tuple({"configurable": {"thread_id": "th-old"}})
    assert tup is not None
    assert tup.checkpoint["channel_values"] == {"x": 41}
    got = tup.config["configurable"]
    assert got["checkpoint_id"] == "ck-old"
    # 旧行拿不到业务值，取列默认值而不是 None。
    assert got["resume_count"] == 0
    assert got["version"] == 1
    assert "task_id" not in got


def test_old_db_is_writable_after_migration(tmp_path):
    db = str(tmp_path / "old.db")
    _seed_old_db(db)
    saver = SqliteCheckpointer(db)

    # 另起一个 thread：迁移进来的旧行没有 LangGraph 的 step 元数据，
    # 不能拿去喂 invoke（那是历史数据的兼容问题，不是本次迁移要解决的）。
    cfg = {"configurable": {"thread_id": "th-old-fresh", **BUSINESS}}
    _app(saver).invoke({"x": 0}, config=cfg)

    latest = saver.get_tuple({"configurable": {"thread_id": "th-old-fresh"}})
    assert latest is not None
    assert latest.config["configurable"]["agent_id"] == "agent-planner"
    # 迁移进来的旧行仍然可读。
    assert saver.get_tuple({"configurable": {"thread_id": "th-old"}}) is not None


# -- ③ ainvoke 异步路径 --------------------------------------------------------


def test_ainvoke_persists_and_reads_back(tmp_path):
    db = str(tmp_path / "ck-async.db")
    saver = SqliteCheckpointer(db)
    app = _app(saver)
    cfg = {"configurable": {"thread_id": "th-async", **BUSINESS}}

    out = asyncio.run(app.ainvoke({"x": 0}, config=cfg))

    assert out["x"] == 1
    latest = asyncio.run(saver.aget_tuple({"configurable": {"thread_id": "th-async"}}))
    assert latest is not None
    assert latest.checkpoint["channel_values"]["x"] == 1
    assert latest.config["configurable"]["task_id"] == "task-7"
    assert latest.config["configurable"]["version"] == 3


def test_alist_yields_the_async_run(tmp_path):
    db = str(tmp_path / "ck-async-list.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-async-list"}}
    asyncio.run(_app(saver).ainvoke({"x": 0}, config=cfg))

    async def _ids() -> list[str]:
        return [
            t.config["configurable"]["checkpoint_id"]
            async for t in saver.alist({"configurable": {"thread_id": "th-async-list"}})
        ]

    ids = asyncio.run(_ids())
    assert ids
    assert ids == sorted(set(ids), key=ids.index)


def test_ainvoke_resumes_from_the_persisted_checkpoint(tmp_path):
    """异步跑完的检查点必须能被同步侧接续（两条路径共享同一份落盘状态）。"""
    db = str(tmp_path / "ck-async-resume.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-async-resume"}}
    asyncio.run(_app(saver).ainvoke({"x": 0}, config=cfg))

    state = _app(saver).invoke(None, config=cfg)

    assert state["x"] == 1


def test_arecord_resume_matches_sync_behaviour(tmp_path):
    db = str(tmp_path / "ck-async-cap.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-async-cap"}}
    _app(saver).invoke({"x": 0}, config=cfg)
    ck_cfg = saver.get_tuple(cfg).config

    asyncio.run(saver.arecord_resume(ck_cfg))

    assert saver.get_tuple(ck_cfg).config["configurable"]["resume_count"] == 1


def test_arecord_resume_raises_at_the_cap(tmp_path):
    db = str(tmp_path / "ck-async-cap2.db")
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": "th-async-cap2"}}
    _app(saver).invoke({"x": 0}, config=cfg)
    ck_cfg = saver.get_tuple(cfg).config
    _set_resume_count(db, ck_cfg, MAX_RESUME_COUNT)

    with pytest.raises(ValidationFailed):
        asyncio.run(saver.arecord_resume(ck_cfg))
