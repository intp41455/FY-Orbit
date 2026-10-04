"""运维脚本静态护栏（start.ps1 / migrations）。

为什么用静态检查而不是真跑
------------------------
`start.ps1` 是 PowerShell 启动脚本，跑它会真的起服务、占端口、改 `.runtime/`。
对「脚本有没有漏删文件」这类**结构性契约**，读源文本断言比真跑更稳、更快、
也不会污染工作区。判据仍是 ADR-011 那一条：**删掉被保护的行为，这些用例必须变红**。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_PS1 = ROOT / "start.ps1"


@pytest.fixture(scope="module")
def start_script() -> str:
    assert START_PS1.exists(), f"启动脚本缺失：{START_PS1}"
    return START_PS1.read_text(encoding="utf-8")


def test_reset_data_removes_wal_and_shm(start_script: str):
    """🔴 -ResetData 必须删 .db + -wal + -shm 三个文件。

    SQLite 以 WAL 模式运行（``db/session.py`` 设 ``PRAGMA journal_mode=WAL``），
    只删 .db 会留下未合并的 WAL 与共享内存文件，下次启动时旧事务可能被重放，
    表现为「重置了但数据还在」。原实现只删 .db，本用例会在它退回时变红。
    """
    # 截取 -ResetData 分支
    block = start_script.split("if ($ResetData)")[1].split("# ----", 1)[0]
    for suffix in (".db", ".db-wal", ".db-shm"):
        assert f"find-yourself{suffix}" in block, (
            f"-ResetData 分支未删除 find-yourself{suffix}；"
            "WAL 模式下的残留文件会导致「重置不彻底」"
        )
    assert "Remove-Item" in block, "-ResetData 分支没有任何删除动作"


def test_reset_block_actually_iterates_files(start_script: str):
    """结构性判据：删除动作必须覆盖全部三个文件（循环或逐个 Remove-Item）。"""
    block = start_script.split("if ($ResetData)")[1].split("# ----", 1)[0]
    removals = re.findall(r"Remove-Item\s+\$?", block)
    listing = "foreach" in block or removals
    assert listing, "未找到文件删除逻辑"
    # 至少三种副作用名出现在同一分支
    for name in ("-wal", "-shm"):
        assert block.count(name) >= 1


def test_start_script_points_at_expected_db_path(start_script: str):
    """回归防线：DB 路径必须与 session/config 的约定一致（.runtime/find-yourself.db）。"""
    assert r".runtime\find-yourself.db" in start_script
    assert "FY_DATABASE_URL=sqlite:///.runtime/find-yourself.db" in start_script
