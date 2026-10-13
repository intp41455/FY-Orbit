"""FIX A1：resume_count 上限（纵深防御，配套运行时循环守卫）。

``record_resume`` 达到 ``MAX_RESUME_COUNT`` 后必须拒绝继续恢复（抛 ValidationFailed），
防止失控的恢复循环无限自增。
"""

from __future__ import annotations

import sqlite3
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from find_yourself.runtime.checkpoint_sqlite import (
    MAX_RESUME_COUNT,
    SqliteCheckpointer,
)
from find_yourself.services.errors import ValidationFailed


class St(TypedDict, total=False):
    x: int


def _build() -> StateGraph:
    g = StateGraph(St)
    g.add_node("a", lambda s: {"x": s.get("x", 0) + 1})
    g.add_edge(START, "a")
    g.add_edge("a", END)
    return g


def _seed(db: str, thread_id: str) -> tuple[SqliteCheckpointer, dict]:
    """建一个真实检查点，返回 saver 与该检查点的 config。"""
    saver = SqliteCheckpointer(db)
    cfg = {"configurable": {"thread_id": thread_id}}
    _build().compile(checkpointer=saver).invoke({"x": 0}, config=cfg)
    tup = saver.get_tuple(cfg)
    assert tup is not None
    return saver, tup.config


def test_record_resume_increments_under_the_cap(tmp_path):
    db = str(tmp_path / "ck.db")
    saver, cfg = _seed(db, "th-under")

    saver.record_resume(cfg)
    saver.record_resume(cfg)

    tup = saver.get_tuple(cfg)
    assert tup.config["configurable"]["resume_count"] == 2


def test_record_resume_raises_at_the_cap(tmp_path):
    db = str(tmp_path / "ck.db")
    saver, cfg = _seed(db, "th-cap")

    # 直接置到「还剩一次」位置，再用公API 走到上限（避免上千次开连接）。
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE checkpoints SET resume_count = ? "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
            (MAX_RESUME_COUNT - 1,
             cfg["configurable"]["thread_id"],
             cfg["configurable"]["checkpoint_ns"],
             cfg["configurable"]["checkpoint_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    saver.record_resume(cfg)  # 恰好到上限
    assert saver.get_tuple(cfg).config["configurable"]["resume_count"] == MAX_RESUME_COUNT

    with pytest.raises(ValidationFailed):
        saver.record_resume(cfg)  # 再恢复一次必须被拒
