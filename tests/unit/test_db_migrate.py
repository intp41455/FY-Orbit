"""P0 save-500：启动期必须执行 alembic upgrade head。

回归场景：存量库由 create_all 建表、从未跑过迁移（无 alembic_version，
dsl_flows 缺 draft_version 列）—— 此前启动入口只 create_all，
草稿保存/流程创建以 500 收场。
"""
from __future__ import annotations

import sqlite3

from find_yourself.db.migrate import find_migrations_root, upgrade_head

LEGACY_DDL = """CREATE TABLE dsl_flows (
  id VARCHAR(64) NOT NULL PRIMARY KEY,
  owner_id VARCHAR(200),
  name VARCHAR(200),
  flow_type VARCHAR(16),
  draft_doc JSON,
  published_doc JSON,
  published_version INTEGER,
  created_at DATETIME,
  updated_at DATETIME
)"""


def _cols(db: str, table: str) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]
    finally:
        con.close()


def test_find_migrations_root_in_repo() -> None:
    assert find_migrations_root() is not None


def test_upgrade_head_repairs_legacy_db(tmp_path) -> None:
    db = str(tmp_path / "legacy.db")
    con = sqlite3.connect(db)
    con.execute(LEGACY_DDL)
    con.commit()
    con.close()
    assert "draft_version" not in _cols(db, "dsl_flows")

    head = upgrade_head("sqlite:///%s" % db)

    assert "draft_version" in _cols(db, "dsl_flows")
    assert head != ""
    con = sqlite3.connect(db)
    try:
        versions = con.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        con.close()
    assert [v[0] for v in versions] == head.split(",")
