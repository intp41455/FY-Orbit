"""迁移链结构门禁。

为什么需要这个文件
------------------
并行施工时，「迁移号别撞」一直靠人主动 `ls` 对手写了什么。实测过四次
（0025 独占 → 0026 出现 → 0027 空闲 → 0028 落盘），每次都对，但这四次
都是**靠某个执行者记得去查**，不是靠机制保证。

不做的后果不是立刻爆炸，而是**下一次撞号要靠人偶然发现** —— 而撞号的
表现是「git log 里出现两个内容不同的同一号」，追溯时极其困难。

本文件把「单头 + 无悬挂引用 + 编号不复用」从人的记忆变成 CI 门禁。

判据（为什么是这三条）
--------------------
1. **单头**：多个head 意味着 alembic upgrade head 会因「目标不明确」而失败，
   或者更糟——不同环境升到不同 head，schema 永久分叉。
2. **无悬挂引用**：某个迁移的 down_revision 指向不存在的节点，则该迁移
   永远不会被执行，而 chain 看起来还是完整的。**这是最隐蔽的一种。**
3. **编号不复用**：被删除或跳过的编号不得被重新占用。git log 里出现两个
   内容不同的 0014，比断链更难查—— 断链会报错，编号复用不会。
"""

from __future__ import annotations

import gc
import re
import time
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# 已被删除/跳过的编号，永不复用。来源：2026-10-04 主控实测迁移链拓扑。
# 0014/0017/0018/0019 在链上不存在；复用会让 git log 出现两个内容不同的同号。
RETIRED_REVISIONS = frozenset({"0014", "0017", "0018", "0019"})

# 迁移文件名的编号前缀，形如 0025_hitl_interrupts -> 0025
_REVISION_PREFIX = re.compile(r"^(\d+)_")


def _script_directory() -> ScriptDirectory:
    if not ALEMBIC_INI.is_file():
        pytest.fail(f"alembic.ini 不存在: {ALEMBIC_INI}")
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def _revisions() -> list:
    return list(_script_directory().walk_revisions())


def test_migration_chain_has_single_head() -> None:
    """迁移链必须只有一个 head。

    多 head 时 `alembic upgrade head` 的行为未定义（不同 alembic 版本分别会
    报错或按顺序全升），而不同环境升到不同 head 会导致 schema 永久分叉。
    """
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"迁移链必须单head，实际 {len(heads)} 个: {sorted(heads)}。"
        f"多head 会让不同环境升到不同 schema。"
    )


def test_migration_chain_has_no_dangling_down_revision() -> None:
    """任何 down_revision 都必须指向链上真实存在的节点。

    悬挂引用最隐蔽：链看起来完整、alembic 也能列出全部 revision，但那个节点
    永远不会被 upgrade 执行，于是「迁移建出来的表」和「create_all 建出来的表」
    长期不一致，且没有任何报错。
    """
    revisions = _revisions()
    known = {r.revision for r in revisions}
    dangling = [
        (r.revision, r.down_revision)
        for r in revisions
        if r.down_revision is not None and r.down_revision not in known
    ]
    assert not dangling, (
        f"以下迁移的 down_revision 指向不存在的节点: {dangling}。"
        f"这些迁移永远不会被执行，且不会有任何报错。"
    )


def test_migration_chain_has_exactly_one_root() -> None:
    """链必须有且仅有一个根（down_revision 为 None）。

    多个根= 链分叉成互不相干的几条，每条各自 upgrade 会产出不同的 schema。
    没有根 = 环状引用，alembic 无法确定起点。
    """
    roots = [r.revision for r in _revisions() if r.down_revision is None]
    assert len(roots) == 1, f"迁移链必须恰好有一个根节点，实际: {sorted(roots)}"


def test_every_revision_is_reachable_from_head() -> None:
    """从 head 出发必须能到达全部节点。

    防止「有孤立迁移文件存在但不在主链上」—— alembic 不会报任何错，
    但那份迁移的表永远建不出来。
    """
    revisions = _revisions()
    head = _script_directory().get_heads()[0]
    by_revision = {r.revision: r for r in revisions}

    reached: set[str] = set()
    cursor: str | None = head
    while cursor is not None and cursor not in reached:
        reached.add(cursor)
        cursor = by_revision[cursor].down_revision

    orphans = sorted(set(by_revision) - reached)
    assert not orphans, (
        f"以下迁移不在从 head 出发的链上（孤立文件）: {orphans}。"
        f"alembic 不会报错，但它们的表永远不会被建出来。"
    )


def test_retired_revision_numbers_are_not_reused() -> None:
    """已退役的编号不得被重新占用。

    这是最隐蔽的一类冲突：链依然单head、无悬挂、全部可达 —— 所有门禁都绿，
    但 git log 里出现两个内容不同的同一号，追溯时无法判断哪个生效。
    """
    on_disk: dict[str, list[str]] = {}
    for path in VERSIONS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        m = _REVISION_PREFIX.match(path.stem)
        assert m, f"迁移文件名不符合 NNN_ 约定: {path.name}"
        on_disk.setdefault(m.group(1), []).append(path.name)

    collisions: dict[str, list[str]] = {}
    for number, files in on_disk.items():
        if len(files) > 1:
            collisions[number] = files
    assert not collisions, (
        f"同一编号下有多个迁移文件: {collisions}。"
        f"这会让 git log 出现两个内容不同的同号，追溯时无法判断哪个生效。"
    )

    reused = sorted(set(on_disk) & RETIRED_REVISIONS)
    assert not reused, (
        f"以下编号已退役，不得复用: {reused}。"
        f"复用会让 git log 里出现两个内容不同的同一号。"
    )


def test_migration_chain_actually_runs_on_a_fresh_database() -> None:
    """从空库 upgrade 到 head 必须真能跑通，且表真的建出来。

    **为什么这条最重要**：前五条都是对「元数据」的断言，而元数据正确
    不等于迁移能跑通。例如某个迁移文件 import 了一个不存在的模型 ——
    元数据完全正常，但 upgrade 会在执行时炸。

    只查 get_heads() 是假绿：它只证明 alembic 能列出 revision，不证明能执行。
    """
    from alembic import command

    tmp = REPO_ROOT / ".runtime" / "_gate_fresh_db_check"
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / "fresh.db"
    if db_path.exists():
        db_path.unlink()

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    try:
        command.upgrade(cfg, "head")

        # 关键：连接必须在 unlink 之前关掉。Windows 上 SQLite 文件被打开时
        # unlink 会报 WinError 32 —— 而那是「清理失败」，不是「断言失败」，
        # 但在 pytest 里两者都会表现为 FAILED，容易被误读成迁移链坏了。
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.connect() as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                }
        finally:
            engine.dispose()
            # SQLite 仍可能持有句柄，dispose 后再兜一层
            gc.collect()

        assert tables, "upgrade 到 head 后一张表都没有，迁移链是空的"
        # alembic 自身的版本表，证明 upgrade 真的记录了版本
        assert "alembic_version" in tables, "缺少 alembic_version 表，upgrade 未生效"
    finally:
        # 清理失败不得让用例失败 —— 那是环境问题，不是迁移链问题。
        for attempt in range(3):
            if not db_path.exists():
                break
            try:
                db_path.unlink()
            except OSError:
                gc.collect()
                time.sleep(0.2 * (attempt + 1))
        if tmp.exists() and not any(tmp.iterdir()):
            try:
                tmp.rmdir()
            except OSError:
                pass  # 目录非空或被占用，都不影响结论
