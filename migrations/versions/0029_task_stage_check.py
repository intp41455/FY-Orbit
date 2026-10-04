"""Alembic migration 0029: ``tasks.stage`` 的 CHECK 白名单（契约 §3.2 合规补齐）。

为什么需要这个迁移
------------------
``tasks`` 表上有 4 个状态白名单 CHECK（``ck_task_domain`` / ``ck_task_mode`` /
``ck_task_strategy`` / ``ck_task_status``），唯独 ``stage`` 没有。``stage`` 是
**持久化字段**（``db/models.py``，默认 ``requirements``），写入路径散落在
workflow 活动与端口里（``workflows/workflow.py`` 写 ``Stage.*``，
``api/routes/tasks.py`` 与 ``services/pg_ports.py`` 写字面量 ``requirements``）。

没有 CHECK 的后果不是立刻报错，而是``stage`` 可以落进任意字符串：一旦有人
把 ``workflows/models.py::Stage`` 里的某个成员改名（例如 ``reconciling`` ->
``reconcilation``），写侧会写新值、读侧与历史行仍是旧值，**两边都不会有任何
报错**，直到某个下游按 stage 分支时行为静默改变。加一条 DB CHECK 就把这种
漂移挡在写入那一刻。

白名单从哪来（单一真源）
------------------------
白名单**不手抄**：它从 ``db/models.py::TASK_STAGES`` 取，而 ``TASK_STAGES``
本身由 ``workflows/models.py::Stage`` 枚举派生。于是「DB 允许的值」与「
workflow 状态机的值」由构造保证一致，不存在第二份可漂移的列表。

既有数据的处置（不静默）
------------------------
迁移会先扫描 ``tasks.stage`` 是否有非法值：
* 有 → **如实报错**并列出违规行，中止升级。不自动回填：``stage`` 是任务当前
  进度的语义，猜一个「最接近的合法值」会把错误数据伪装成正确数据。
* 无 → 正常加约束。

SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``，因此走
``batch_alter_table``（copy-and-move）。约束名按**短名** ``ck_task_stage``
声明：batch 会沿用 ``db/base.py::NAMING_CONVENTION`` 把它解析成
``ck_tasks_ck_task_stage``，与 ``create_all`` 建出来的名字逐字相同——否则
fresh-DB 与 migrated-DB 的 schema 会长期分叉。
**不要传完整名给 batch**：batch 会把「反射回来的既有名」再套一次前缀，导致
downgrade 找不到约束（实测会变成 ``ck_tasks_ck_tasks_ck_task_stage``）。

幂等：fresh database 上 0001 已经跑过 ``Base.metadata.create_all``，``tasks``
到达时**已经带** ``ck_tasks_ck_task_stage``，故 upgrade 检测到已存在即跳过。

Revision ID: 0029_task_stage_check
Revises: 0028_artifact_gate
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from find_yourself.db.models import TASK_STAGES

revision: str = "0029_task_stage_check"
down_revision: str | None = "0028_artifact_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tasks"
_CONSTRAINT_SHORT = "ck_task_stage"
# ``Base.metadata`` 的 naming_convention 会给 CHECK 名加上 ``ck_<table>_`` 前缀。
_CONSTRAINT_DB = f"ck_{_TABLE}_{_CONSTRAINT_SHORT}"

# 从单一真源派生表达式，避免迁移里出现第二份手抄的白名单。
_STAGE_IN = "stage IN (" + ", ".join(f"'{value}'" for value in TASK_STAGES) + ")"

# 报错时最多列出多少条违规样本，避免整表都是脏数据时刷屏。
_MAX_SAMPLES = 20


def _existing_check_names(bind, table: str) -> set[str]:
    return {c.get("name") for c in sa.inspect(bind).get_check_constraints(table)}


def _reject_illegal_stage(bind) -> None:
    """非法 stage 是数据事故，必须显式失败而不是静默通过。

    用参数化 ``IN`` 表达白名单；``TASK_STAGES`` 是编译期常量，但走 bind 参数
    可以避免把值拼进 SQL 文本。
    """
    placeholders = ", ".join(f":s{i}" for i in range(len(TASK_STAGES)))
    params = {f"s{i}": value for i, value in enumerate(TASK_STAGES)}
    statement = sa.text(
        f"SELECT id, stage FROM {_TABLE} "
        f"WHERE stage NOT IN ({placeholders}) ORDER BY id"
    )
    rows = bind.execute(statement, params).fetchall()
    if not rows:
        return
    offending_values = sorted({str(row[1]) for row in rows})
    samples = [(str(row[0]), str(row[1])) for row in rows[:_MAX_SAMPLES]]
    raise RuntimeError(
        f"迁移 0029_task_stage_check 中止：{_TABLE}.stage 存在 {len(rows)} 行非法值 "
        f"{offending_values}，不在 TASK_STAGES 白名单内。\n"
        f"违规样本(id, stage，最多 {_MAX_SAMPLES} 条): {samples}\n"
        f"处置：stage 是任务进度的语义，本迁移**不会**自动回填以免把错误数据"
        f"伪装成正确数据。请先人工核对这些行应归入哪个合法 stage 并完成数据"
        f"修复，再重跑 `alembic upgrade head`。"
    )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        # fresh DB 由 0001 建表；表不存在说明链被异常裁剪，nothing to extend。
        return

    if _CONSTRAINT_DB in _existing_check_names(bind, _TABLE):
        # fresh database：0001 的 create_all 已带此约束，正确跳过。
        return

    # 既有非法数据必须显式失败（见模块 docstring「既有数据的处置」）。
    _reject_illegal_stage(bind)

    with op.batch_alter_table(_TABLE) as bt:
        bt.create_check_constraint(_CONSTRAINT_SHORT, _STAGE_IN)


def downgrade() -> None:
    """回退：仅移除本次新增的 stage CHECK，不动 ``tasks`` 的任何数据与列。

    ``stage`` 列与其中的值都早于本迁移存在；回退「约束」不应删除用户数据。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table(_TABLE):
        return

    names = _existing_check_names(bind, _TABLE)
    if not ({_CONSTRAINT_DB, _CONSTRAINT_SHORT} & names):
        return

    with op.batch_alter_table(_TABLE) as bt:
        # 短名交给 naming_convention 解析成实际存储名，避免重复前缀。
        bt.drop_constraint(_CONSTRAINT_SHORT, type_="check")
