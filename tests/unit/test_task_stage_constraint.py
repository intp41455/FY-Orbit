"""``Task.stage`` 的 CHECK 白名单 + 与 workflow ``Stage`` 枚举的收敛门禁。

为什么需要这个文件
------------------
``tasks`` 表有 4 个状态白名单 CHECK（``ck_task_domain`` / ``ck_task_mode`` /
``ck_task_strategy`` / ``ck_task_status``），唯独持久化字段 ``stage`` 没有
（契约 §3.2「状态字段用 CHECK 约束」的实际缺口）。缺口的表现不是报错，而是
``stage`` 能落进任意字符串：一旦 ``workflows/models.py::Stage`` 里某成员被
改名，**写侧写新值、历史行与读侧仍是旧值，两边都不报错**，直到下游按 stage
分支时行为静默改变。

本文件把三件事变成 CI 门禁：
1. DB 真的拒非法 stage（不是只有模型层声明）；
2. ``TASK_STAGES``（DB 白名单）与 ``Stage`` 枚举是**同一份真源**，派生自枚举、
   不手抄；
3. 迁移 0029 真能升、真能降、遇到既有非法数据**显式失败**而不是静默通过。

关于 ``runtime/graph.py::TaskGraphState.stage``
----------------------------------------------
它**不是**持久化字段：graph 是 LangGraph 的 phase-internal 状态，其 ``stage``
记的是图节点里程碑（``validate`` / ``finish`` / ``finished`` /
``exec_<route>``），与 ``tasks.stage`` 落库的 ``Stage`` 枚举是**两套词汇**
（见 ``test_graph_stage_vocabulary_*`` 与 file 末尾的 xfail）。本文件用
xfail(strict) 把这个真实分歧显式钉住，而不是写一条恒真的假断言把它盖掉。
"""

from __future__ import annotations

import gc
import re
import time
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from find_yourself.db.models import TASK_STAGES, Task
from find_yourself.db.types import utcnow
from find_yourself.workflows.models import Stage

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
GRAPH_SOURCE = REPO_ROOT / "src" / "find_yourself" / "runtime" / "graph.py"

STAGE_VALUES = frozenset(member.value for member in Stage)
# ``Base.metadata`` 的 naming_convention 把 CheckConstraint(name="ck_task_stage")
# 解析成带表名前缀的实际存储名。
STAGE_CHECK_DB_NAME = "ck_tasks_ck_task_stage"


# ---------------------------------------------------------------------------
# 1. 收敛：DB 白名单 == 枚举（禁止手抄后漂移）
# ---------------------------------------------------------------------------
def test_task_stages_equals_stage_enum_members() -> None:
    """``TASK_STAGES`` 必须与 ``Stage`` 枚举成员集合完全一致。

    这是「单一真源」的核心断言：若有人把白名单手抄一遍、之后只改了枚举或
    只改了白名单，这条会立刻变红。
    """
    assert set(TASK_STAGES) == STAGE_VALUES, (
        f"DB 白名单与 Stage 枚举不一致：\n"
        f"  只在 TASK_STAGES: {sorted(set(TASK_STAGES) - STAGE_VALUES)}\n"
        f"  只在 Stage: {sorted(STAGE_VALUES - set(TASK_STAGES))}"
    )


def test_task_stages_is_derived_from_enum_in_order() -> None:
    """``TASK_STAGES`` 必须**按枚举声明顺序**逐字派生（不是另一份手写列表）。"""
    assert TASK_STAGES == tuple(member.value for member in Stage)


def test_check_constraint_is_declared_on_task_model() -> None:
    """模型层必须真的声明了 stage 的 CHECK（而不只是存在一个常量）。"""
    names = {
        c.name for c in Task.__table__.constraints if isinstance(c, CheckConstraint)
    }
    assert STAGE_CHECK_DB_NAME in names, (
        f"tasks 表缺少 stage 的 CHECK 约束；现有 CHECK: {sorted(n for n in names if n)}"
    )


# ---------------------------------------------------------------------------
# 2. DB 真的执行白名单（合法全过 / 非法全拒）
# ---------------------------------------------------------------------------
def test_db_has_stage_check_constraint(session) -> None:
    """迁移/create_all 建出的库里，stage 的 CHECK 真的存在且表达式针对 stage。"""
    checks = inspect(session.get_bind()).get_check_constraints("tasks")
    stage_checks = [c for c in checks if c.get("name") == STAGE_CHECK_DB_NAME]
    assert stage_checks, f"DB 里没有 {STAGE_CHECK_DB_NAME}；实际: {[c.get('name') for c in checks]}"
    assert "stage IN (" in (stage_checks[0].get("sqltext") or ""), (
        "CHECK 表达式没有约束 stage 列"
    )


@pytest.mark.parametrize("stage", sorted(STAGE_VALUES))
def test_every_legal_stage_is_accepted(session, stage: str) -> None:
    """每一个合法 stage 都必须能写入（白名单不能漏掉枚举成员）。"""
    session.add(
        Task(
            id=f"t-ok-{stage}", owner_id="o", goal="g", stage=stage,
            deadline=utcnow() + timedelta(hours=1), idempotency_key=f"k-{stage}",
        )
    )
    session.flush()
    session.rollback()


@pytest.mark.parametrize(
    "bad",
    ["bogus", "REVIEW", "Review", "", "review ", "validate", "exec_single_agent"],
)
def test_illegal_stage_is_rejected_by_db(session, bad: str) -> None:
    """任意非白名单字符串必须被 DB 拒绝（IntegrityError）。

    含 ``validate`` / ``exec_single_agent``：它们是 ``runtime/graph.py`` 的
    图内里程碑词汇，**不是**持久化枚举——落库必须被拒，以钉住两套词汇的边界。
    """
    with pytest.raises(IntegrityError):
        session.add(
            Task(
                id=f"t-bad-{abs(hash(bad))}", owner_id="o", goal="g", stage=bad,
                deadline=utcnow() + timedelta(hours=1), idempotency_key=f"kb-{abs(hash(bad))}",
            )
        )
        session.flush()


def test_default_stage_is_a_legal_member(session) -> None:
    """不显式给 stage 时，默认值本身必须是合法枚举成员。"""
    row = Task(
        id="t-default", owner_id="o", goal="g",
        deadline=utcnow() + timedelta(hours=1), idempotency_key="k-default",
    )
    session.add(row)
    session.flush()
    assert row.stage in STAGE_VALUES


# ---------------------------------------------------------------------------
# 3. 迁移 0029：可升、可降、遇非法数据显式失败
# ---------------------------------------------------------------------------
def _fresh_config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _stage_check_names(engine) -> set[str]:
    return {
        c.get("name")
        for c in inspect(engine).get_check_constraints("tasks")
        if c.get("name") and "stage" in c.get("name")
    }


def _unlink_retry(path: Path) -> None:
    for attempt in range(3):
        if not path.exists():
            return
        try:
            path.unlink()
        except OSError:
            gc.collect()
            time.sleep(0.2 * (attempt + 1))


def test_migration_0029_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    """0029 必须真能升、真能降、再真能升（downgrade 不是空壳）。"""
    db_path = tmp_path / "m29.db"
    cfg = _fresh_config(db_path)
    try:
        command.upgrade(cfg, "head")
        eng = create_engine(f"sqlite:///{db_path}")
        try:
            assert _stage_check_names(eng) == {STAGE_CHECK_DB_NAME}, "升级后 stage CHECK 缺失"
        finally:
            eng.dispose()
            gc.collect()

        command.downgrade(cfg, "0028_artifact_gate")
        eng = create_engine(f"sqlite:///{db_path}")
        try:
            assert _stage_check_names(eng) == set(), "downgrade 没有真正移除 stage CHECK"
            # 其余 4 个 CHECK 必须仍在——downgrade 不得误伤既有约束。
            remaining = {
                c.get("name") for c in inspect(eng).get_check_constraints("tasks")
            }
            assert "ck_tasks_ck_task_status" in remaining
        finally:
            eng.dispose()
            gc.collect()

        command.upgrade(cfg, "head")
        eng = create_engine(f"sqlite:///{db_path}")
        try:
            assert _stage_check_names(eng) == {STAGE_CHECK_DB_NAME}, "二次升级未恢复 stage CHECK"
        finally:
            eng.dispose()
            gc.collect()
    finally:
        _unlink_retry(db_path)


def test_migration_0029_downgrade_preserves_existing_rows(tmp_path: Path) -> None:
    """downgrade 只移除约束，绝不动 tasks 里的数据。"""
    db_path = tmp_path / "m29_data.db"
    cfg = _fresh_config(db_path)
    try:
        command.upgrade(cfg, "head")
        eng = create_engine(f"sqlite:///{db_path}")
        try:
            sm = sessionmaker(bind=eng, expire_on_commit=False, future=True)
            with sm() as s:
                s.add(
                    Task(
                        id="t-keep", owner_id="o", goal="g", stage="review",
                        deadline=utcnow() + timedelta(hours=1), idempotency_key="k-keep",
                    )
                )
                s.commit()

            command.downgrade(cfg, "0028_artifact_gate")

            with sm() as s:
                row = s.get(Task, "t-keep")
                assert row is not None, "downgrade 删除了用户数据"
                assert row.stage == "review", "downgrade 改动了 stage 值"
        finally:
            eng.dispose()
            gc.collect()
    finally:
        _unlink_retry(db_path)


def test_migration_0029_rejects_illegal_legacy_data(tmp_path: Path) -> None:
    """既有非法 stage 必须让升级**显式失败**，不得静默通过。

    复现路径：先降到 0028（无 CHECK），写入一行非法 stage，再升到 head。
    期望：抛错、点名违规值、不改数据、约束不被加上。
    """
    db_path = tmp_path / "m29_bad.db"
    cfg = _fresh_config(db_path)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0028_artifact_gate")

        eng = create_engine(f"sqlite:///{db_path}")
        try:
            sm = sessionmaker(bind=eng, expire_on_commit=False, future=True)
            with sm() as s:
                # 此刻 DB 上没有 stage CHECK，非法值可以落库。
                s.add(
                    Task(
                        id="t-legacy-bad", owner_id="o", goal="g", stage="legacy_typo",
                        deadline=utcnow() + timedelta(hours=1), idempotency_key="k-bad",
                    )
                )
                s.commit()

            with pytest.raises(RuntimeError) as excinfo:
                command.upgrade(cfg, "head")
            msg = str(excinfo.value)
            assert "legacy_typo" in msg, f"报错没有点名违规值: {msg}"

            # 失败必须是无副作用的：数据还在、约束没被偷偷加上。
            with sm() as s:
                row = s.get(Task, "t-legacy-bad")
                assert row is not None and row.stage == "legacy_typo", "报错路径改动了数据"
            assert _stage_check_names(eng) == set(), "报错路径却把约束加上了"
        finally:
            eng.dispose()
            gc.collect()
    finally:
        _unlink_retry(db_path)


# ---------------------------------------------------------------------------
# 4. runtime/graph.py::TaskGraphState.stage 词汇表（钉住真实分歧）
# ---------------------------------------------------------------------------
# 从 graph.py 源码抽 ``"stage": "字面量"``（f-string 一律不匹配，单独处理）。
_GRAPH_STAGE_LITERAL_RE = re.compile(r'"stage":\s*"([^"]+)"')


def _graph_stage_literals() -> frozenset[str]:
    return frozenset(_GRAPH_STAGE_LITERAL_RE.findall(GRAPH_SOURCE.read_text(encoding="utf-8")))


def test_graph_stage_literal_vocabulary_is_pinned() -> None:
    """钉住 graph.py 里 ``stage`` 的字符串字面量集合。

    有人往图节点里加一个新的 stage 字面量时这条会红，逼迫复审
    「这是图内里程碑还是持久化枚举」，避免图内词汇悄悄长出新成员。
    """
    assert _graph_stage_literals() == frozenset(
        {"requirements", "planning", "validate", "finish", "finished"}
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知真实分歧：runtime/graph.py 的 TaskGraphState.stage 是图节点里程碑"
        "（validate/finish/finished/exec_<route>），与持久化 Stage 枚举是两套词汇；"
        "收敛需要改 graph.py（本次任务文件范围外）。此 xfail 记录该差距，"
        "若将来 graph stage 被收敛，本用例会 XPASS(strict) 变红以提示移除。"
    ),
)
def test_graph_stage_vocabulary_subset_of_enum() -> None:
    """团队要求的最小收敛断言：图 stage 取值 ⊆ Stage 枚举。"""
    dynamic = frozenset({"exec_single_agent", "exec_research", "exec_tool_step", "exec_delegate"})
    assert (_graph_stage_literals() | dynamic) <= STAGE_VALUES
