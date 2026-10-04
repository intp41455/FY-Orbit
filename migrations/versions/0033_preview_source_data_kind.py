"""Alembic migration 0033: ``preview_sources.kind`` 白名单加 ``data``（P2 · Layer 4）.

为什么需要这个迁移
------------------
P2 给统一预览源注册协议补 **Layer 4（结构化数据 → 图表渲染）**：
``services/preview_sources.py::register_data`` 以 ``kind="data"`` 落库登记
工作区里的 .json/.csv 数据源。而 0012 建表时的 CHECK
``ck_preview_sources_psrc_kind`` 只允许 ``('static', 'process')``——
migrated-DB 上任何 ``data`` 行都会被拒绝，同时 fresh-DB（``create_all``）
的模型白名单已经含 ``data``。不改 CHECK 就会造成 §10.3.3 明令禁止的
fresh/migrated schema 漂移。

单一真源
--------
白名单与 ``db/workbench_models.py::PreviewSourceRecord`` 的 ``psrc_kind``
CHECK 保持逐字一致（``'static', 'process', 'data'``），迁移内不出现第二份
可漂移的列表。

幂等与 fresh-DB
---------------
fresh database 上 ``create_all`` 建出的 CHECK **已经**是三值白名单；
upgrade 通过反射 sqltext 探测，已含 ``data`` 即跳过。migrated-DB 才真的
重建约束。

SQLite / 批处理的坑（0029 实测教训，勿重蹈）
--------------------------------------------
SQLite 不支持 ``ALTER TABLE ... DROP CONSTRAINT``，走
``batch_alter_table``（copy-and-move）。传给 batch 的是**声明短名**
``psrc_kind``，由 ``db/base.py::NAMING_CONVENTION`` 解析成
``ck_preview_sources_psrc_kind``，与 ``create_all`` 建出的名字逐字相同。
**绝不能把反射回来的完整名再传给 batch**，否则 downgrade 会出现双重前缀
（``ck_..._ck_...``）而找不到约束——0032 的在飞实现正踩在此坑上（本迁移
的往返测试只做相邻一跳，不受其 downgrade 缺陷影响）。

Revision ID: 0033_preview_source_data_kind
Revises: 0032_collaboration_replies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_preview_source_data_kind"
down_revision: str | None = "0032_collaboration_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "preview_sources"
# 声明短名：batch_alter_table 按 NAMING_CONVENTION 解析成
# ck_preview_sources_psrc_kind，与 create_all 逐字相同（勿传反射完整名）。
_CONSTRAINT_SHORT = "psrc_kind"
_KIND_NEW = "kind IN ('static', 'process', 'data')"
_KIND_OLD = "kind IN ('static', 'process')"


def _existing_checks(bind) -> dict[str, str]:
    """{约束名: sqltext}——探测既有 CHECK 用的唯一事实。"""
    return {
        c["name"]: str(c.get("sqltext") or "")
        for c in sa.inspect(bind).get_check_constraints(_TABLE)
        if c.get("name")
    }


def _has_data_clause(checks: dict[str, str]) -> bool:
    return any("'data'" in sql for sql in checks.values())


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(_TABLE):
        return

    checks = _existing_checks(bind)
    if _has_data_clause(checks):
        # fresh DB：create_all 已带三值白名单，正确跳过。
        return

    with op.batch_alter_table(_TABLE) as bt:
        # 传短名，让 naming_convention 解析成实际存储名（勿传反射名，见模块注）。
        if any(name.endswith(_CONSTRAINT_SHORT) for name in checks):
            bt.drop_constraint(_CONSTRAINT_SHORT, type_="check")
        bt.create_check_constraint(_CONSTRAINT_SHORT, _KIND_NEW)


def downgrade() -> None:
    """回退到 ('static', 'process') 白名单。

    既有 ``data`` 行的处置（不静默）：如果库里已存在 ``kind='data'`` 的行，
    收窄白名单会让它们变成非法数据——如实报错列出样本，**不**自动删除或改写
    （删数据永远不是迁移工具的默认权力）。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(_TABLE):
        return

    checks = _existing_checks(bind)
    if not _has_data_clause(checks):
        return

    rows = bind.execute(
        sa.text(f"SELECT id FROM {_TABLE} WHERE kind = 'data' LIMIT 20")
    ).fetchall()
    if rows:
        raise RuntimeError(
            f"迁移 0033 downgrade 中止：{_TABLE} 存在 {len(rows)} 行 kind='data'，"
            f"收窄白名单会使其非法。请先处理这些行再降级。样本 id: "
            f"{[str(r[0]) for r in rows]}"
        )

    with op.batch_alter_table(_TABLE) as bt:
        bt.drop_constraint(_CONSTRAINT_SHORT, type_="check")
        bt.create_check_constraint(_CONSTRAINT_SHORT, _KIND_OLD)
