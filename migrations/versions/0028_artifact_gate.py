"""Alembic migration 0028: 产物版本门禁（需求 7）。

引入两张表：

* ``artifact_versions`` —— 「某产物的第 N 版」。内容摘要由真实字节算出、
  创建后不变；改内容必须建新的一版。
* ``artifact_gate_checks`` —— 一次独立检查的**证据**（命令/退出码/当时
  观测到的摘要），不是布尔开关。

关键设计（详见 ``db/artifact_gate_models.py`` 的模块 docstring）
--------------------------------------------------------------
1. **必检项不落库**。它来自服务端 ``GATE_POLICY``，不来自调用方参数——
   否则「声明无需检查」就是一条现成的绕过路径。所以本迁移**没有**
   ``required_checks`` 列。
2. **摘要类证据会被重新核对**。``artifact_gate_checks.observed_digest``
   记录检查当时实际观测到的摘要；判定时与版本的 ``content_digest`` 比对，
   「先验后改」在读取侧被挡。
3. **状态机不在库里**。``GATE_TRANSITIONS`` 是服务层的显式枚举，因为
   「同一个目标态从不同起点到达，合法性不同」（``in_review -> released``
   必须非法而 ``verified -> released`` 必须合法），用目标态白名单表达不了。

由 schema 兜底的：状态白名单、``(kind,artifact_id,version_no)`` 唯一、
``draft`` 必须无decided_at/submitted_at 而其余状态必须都有、摘要长度 64、
乐观锁从 1 起。

``0028`` 号段：0025/0026/0027 分别属需求 12/ 10 / 6，本迁移接在
``0027_team_approval`` 之后。空号 0014/0017/0018/0019 **刻意不复用**
（复用会让 git log 出现两个内容不同的同一号）。

Idempotency: 建表由 ``sa.inspect(bind)`` 守卫，沿用 0005-0027 的写法。

Revision ID: 0028_artifact_gate
Revises: 0027_team_approval
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_artifact_gate"
down_revision: str | None = "0027_team_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("artifact_versions"):
        op.create_table(
            "artifact_versions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("artifact_kind", sa.String(length=32), nullable=False),
            sa.Column("artifact_id", sa.String(length=200), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
            # 摘要由真实字节算出，创建后不变（改内容 = 建新的一版）。
            sa.Column("content_digest", sa.String(length=64), nullable=False),
            sa.Column("workspace_ref", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by", sa.String(length=200), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "artifact_kind IN ('asset','code_bundle')", name="ck_agv_kind"
            ),
            sa.CheckConstraint(
                "state IN ('draft','in_review','verified','released','rejected','blocked')",
                name="ck_agv_state",
            ),
            sa.CheckConstraint("version >= 1", name="ck_agv_positive_version"),
            sa.CheckConstraint("version_no >= 1", name="ck_agv_positive_version_no"),
            # 摘要必须是 64 位。不校验它，「证据与版本摘要比对」就退化成
            # 两个空字符串比较——恒真。
            sa.CheckConstraint("length(content_digest) = 64", name="ck_agv_digest_is_sha256"),
            # draft 必须「未提交、未判定」；其余状态必须「已提交、已判定」。
            # 没有这两条，一个「未经 submit 就 verified」的行能落库。
            sa.CheckConstraint(
                "(state <> 'draft' AND decided_at IS NOT NULL) OR "
                "(state = 'draft' AND decided_at IS NULL)",
                name="ck_agv_decided_shape",
            ),
            sa.CheckConstraint(
                "(state <> 'draft' AND submitted_at IS NOT NULL) OR "
                "(state = 'draft' AND submitted_at IS NULL)",
                name="ck_agv_submitted_shape",
            ),
            sa.UniqueConstraint(
                "artifact_kind", "artifact_id", "version_no",
                name="uq_agv_artifact_version",
            ),
        )
        op.create_index("ix_artifact_versions_owner_id", "artifact_versions", ["owner_id"])
        op.create_index("ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"])
        op.create_index("ix_agv_owner_kind", "artifact_versions", ["owner_id", "artifact_kind"])
        op.create_index(
            "ix_agv_artifact", "artifact_versions", ["artifact_kind", "artifact_id"]
        )
        op.create_index("ix_agv_state", "artifact_versions", ["state"])

    if not insp.has_table("artifact_gate_checks"):
        op.create_table(
            "artifact_gate_checks",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "artifact_version_id",
                sa.String(length=64),
                sa.ForeignKey("artifact_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("check_name", sa.String(length=64), nullable=False),
            # 没有「跳过」：跳过与通过在库里必须长得不一样。
            sa.Column("status", sa.String(length=16), nullable=False),
            # 检查当时实际观测到的摘要（不是抄版本上的值——抄上去就等于
            # 自己判自己及格）。判定时与 content_digest 比对。
            sa.Column("observed_digest", sa.String(length=64), nullable=True),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=False, server_default=""),
            sa.Column("ran_by", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint("status IN ('passed','failed')", name="ck_agc_status"),
            sa.CheckConstraint("version >= 1", name="ck_agc_positive_version"),
            sa.CheckConstraint(
                "observed_digest IS NULL OR length(observed_digest) = 64",
                name="ck_agc_observed_digest_is_sha256",
            ),
            # 一版一项只留一行：重跑覆盖。留多行就得回答「取哪一行」，
            # 而答错会让一份陈旧的通过证据复活。
            sa.UniqueConstraint(
                "artifact_version_id", "check_name", name="uq_agc_version_check"
            ),
        )
        op.create_index(
            "ix_artifact_gate_checks_artifact_version_id",
            "artifact_gate_checks",
            ["artifact_version_id"],
        )
        op.create_index(
            "ix_agc_version_status",
            "artifact_gate_checks",
            ["artifact_version_id", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("artifact_gate_checks"):
        op.drop_index("ix_agc_version_status", table_name="artifact_gate_checks")
        op.drop_index(
            "ix_artifact_gate_checks_artifact_version_id",
            table_name="artifact_gate_checks",
        )
        op.drop_table("artifact_gate_checks")

    if insp.has_table("artifact_versions"):
        op.drop_index("ix_agv_state", table_name="artifact_versions")
        op.drop_index("ix_agv_artifact", table_name="artifact_versions")
        op.drop_index("ix_agv_owner_kind", table_name="artifact_versions")
        op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions")
        op.drop_index("ix_artifact_versions_owner_id", table_name="artifact_versions")
        op.drop_table("artifact_versions")
