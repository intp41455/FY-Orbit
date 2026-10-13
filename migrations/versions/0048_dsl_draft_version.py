"""Alembic migration 0048: ``dsl_flows`` 增加草稿乐观锁版本列 ``draft_version``.

N3 状态同步（切片 3）：画布草稿保存从「无条件覆盖」改为「按版本号校验后覆盖」，
客户端把 GET 拿到的 ``draft_version`` 作为 ``expected_version`` 回传，服务端比对
不一致即 409 Conflict，避免两个标签页互相覆盖对方的编辑。

为什么与 ``published_version`` 分列而不是复用
--------------------------------------------
``published_version`` 属于**发布线**（append-only，发布一次 +1，回滚不动它）；
``draft_version`` 属于**草稿线**（保存草稿 +1，回滚取回草稿也 +1）。两条线独立
单调递增，语义不同，合并会让「回滚草稿」污染发布版本号。

``server_default="0"``：既有行的草稿版本号一律视为 0，且 0 同时表示「初始态 /
从未保存过草稿」——与 ORM 侧 ``default=0`` 对齐，让存量库升级后首个带
``expected_version=0`` 的客户端请求能正常命中。

编号说明：任务书给的是「上一条是 0038」，但落笔时链上已有他人 0039–0047
（``0047_hitl_vote`` 才是真head），且0039 已被 ``0039_rag_presets_and_graph``
占用。编号撞车会让 ``alembic upgrade head`` 直接失败（双head）或让两个内容不同
的同号进 git log，因此本迁移顺延为 **0048**，接在真head 之后。

Revision ID: 0048_dsl_draft_version
Revises: 0047_hitl_vote
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_dsl_draft_version"
down_revision: str | None = "0047_hitl_vote"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLOWS = "dsl_flows"
COLUMN = "draft_version"


def _has_column(bind, table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(FLOWS):
        return
    if _has_column(bind, FLOWS, COLUMN):
        return
    with op.batch_alter_table(FLOWS) as batch:
        batch.add_column(sa.Column(
            COLUMN, sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(FLOWS) or not _has_column(bind, FLOWS, COLUMN):
        return
    with op.batch_alter_table(FLOWS) as batch:
        batch.drop_column(COLUMN)
