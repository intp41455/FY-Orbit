"""Alembic migration 0031: 插件包生态（需求 14 第一切片：签名 / 扫描 / 上架门禁）。

两件事：

1. **给 ``skills`` 增签名与扫描列**（expand-contract 的 expand 步，全部可空或带
   默认，旧行无需回填即合法）：

   * ``signature`` / ``signature_algorithm`` / ``signing_key_id`` —— 包签名三元组。
     未签名技能三者皆 NULL 合法；``ck_skills_ck_skill_signature_shape`` 保证
     「``signature_verified`` 为真」时三者必须齐备（不存在一个没来源的『已校验』）。
   * ``signature_verified`` / ``scan_report`` / ``scan_passed`` / ``gate_profile``
     —— 服务端写入的校验与扫描结论；调用方无从伪造。

2. **新建 ``plugin_signing_keys``** —— 只登记**公钥**（私钥结构性不存在，见
   ``db/plugin_models.py`` 模块 docstring）。``plugin_models`` 不被 0001 导入，
   所以 fresh-DB 上这张表**不由** ``create_all`` 建出，必须由本迁移建。

fresh-DB 与 migrated-DB 的一致性
--------------------------------
0001 走 ``Base.metadata.create_all``，而 ``skills`` 的新列现在写在 ``db/models.py``
里，因此 **fresh-DB 在 0001 阶段就已带这些列与 CHECK**；``plugin_signing_keys``
则在 0031 才建。为让两条路径收敛到同一 schema，``skills`` 的加列/加约束一律
**先探测再执行**（``sa.inspect``）：fresh-DB 已存在即跳过，migrated-DB 才真的加。
约束名用**短名**（``ck_skill_gate_profile`` / ``ck_skill_signature_shape``），由
``db/base.py::NAMING_CONVENTION`` 解析成 ``ck_skills_*``，与 ``create_all`` 逐字相同
——不要传完整名，否则 batch 会套第二次前缀（0029 已实测踩过）。

Revision ID: 0031_plugin_ecosystem
Revises: 0030_collaboration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from find_yourself.db.models import SKILL_GATE_PROFILES

revision: str = "0031_plugin_ecosystem"
down_revision: str | None = "0030_collaboration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "skills"
_SIGNING_TABLE = "plugin_signing_keys"

# 新增列：名字 -> sa.Column。nullable 直接加；NOT NULL 的用 server_default 让
# 既有行合法（batch + SQLite 也要求 NOT NULL ADD COLUMN 带默认）。
_NEW_COLUMNS: dict[str, sa.Column] = {
    "signature": sa.Column("signature", sa.Text(), nullable=True),
    "signature_algorithm": sa.Column("signature_algorithm", sa.String(length=32), nullable=True),
    "signing_key_id": sa.Column("signing_key_id", sa.String(length=64), nullable=True),
    "signature_verified": sa.Column(
        "signature_verified", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
    "scan_report": sa.Column(
        "scan_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
    ),
    "scan_passed": sa.Column(
        "scan_passed", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
    "gate_profile": sa.Column(
        "gate_profile", sa.String(length=16), nullable=False, server_default=sa.text("'instruction'")
    ),
}

_GATE_IN = "gate_profile IN (" + ", ".join(f"'{v}'" for v in SKILL_GATE_PROFILES) + ")"


def _check_names(bind, table: str) -> set[str]:
    if not sa.inspect(bind).has_table(table):
        return set()
    return {c.get("name") for c in sa.inspect(bind).get_check_constraints(table)}


def _column_names(bind, table: str) -> set[str]:
    if not sa.inspect(bind).has_table(table):
        return set()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- 1) skills: 签名 / 扫描列 --------------------------------------
    if insp.has_table(_TABLE):
        cols = _column_names(bind, _TABLE)
        checks = _check_names(bind, _TABLE)
        missing_cols = [name for name in _NEW_COLUMNS if name not in cols]
        need_gate = "ck_skill_gate_profile" not in checks and "ck_skills_ck_skill_gate_profile" not in checks
        need_shape = "ck_skill_signature_shape" not in checks and "ck_skills_ck_skill_signature_shape" not in checks

        if missing_cols or need_gate or need_shape:
            with op.batch_alter_table(_TABLE) as bt:
                for name in missing_cols:
                    bt.add_column(_NEW_COLUMNS[name])
                if need_gate:
                    bt.create_check_constraint("ck_skill_gate_profile", _GATE_IN)
                if need_shape:
                    bt.create_check_constraint(
                        "ck_skill_signature_shape",
                        "NOT signature_verified OR (signature IS NOT NULL AND "
                        "signature_algorithm IS NOT NULL AND signing_key_id IS NOT NULL)",
                    )

    # ---- 2) plugin_signing_keys: 只存公钥 --------------------------------
    if not insp.has_table(_SIGNING_TABLE):
        op.create_table(
            _SIGNING_TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=200), nullable=False),
            sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="ed25519"),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint("algorithm IN ('ed25519')", name="ck_psk_algorithm"),
            sa.CheckConstraint("state IN ('active','revoked')", name="ck_psk_state"),
            sa.CheckConstraint("version >= 1", name="ck_psk_version_positive"),
            sa.CheckConstraint("length(public_key) > 0", name="ck_psk_public_key_nonempty"),
            sa.UniqueConstraint("public_key", name="uq_psk_public_key"),
        )
        op.create_index("ix_plugin_signing_keys_owner_id", _SIGNING_TABLE, ["owner_id"])
        op.create_index("ix_psk_owner_state", _SIGNING_TABLE, ["owner_id", "state"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # ---- 2') drop plugin_signing_keys -----------------------------------
    if insp.has_table(_SIGNING_TABLE):
        for name in ("ix_psk_owner_state", "ix_plugin_signing_keys_owner_id"):
            if any(i.get("name") == name for i in sa.inspect(bind).get_indexes(_SIGNING_TABLE)):
                op.drop_index(name, table_name=_SIGNING_TABLE)
        op.drop_table(_SIGNING_TABLE)

    # ---- 1') skills: 去掉本迁移新增的约束与列 -----------------------------
    if insp.has_table(_TABLE):
        checks = _check_names(bind, _TABLE)
        cols = _column_names(bind, _TABLE)
        present_constraints = [
            short
            for short, full in (
                ("ck_skill_gate_profile", "ck_skills_ck_skill_gate_profile"),
                ("ck_skill_signature_shape", "ck_skills_ck_skill_signature_shape"),
            )
            if short in checks or full in checks
        ]
        present_columns = [name for name in _NEW_COLUMNS if name in cols]
        if present_constraints or present_columns:
            with op.batch_alter_table(_TABLE) as bt:
                for short in present_constraints:
                    bt.drop_constraint(short, type_="check")
                for name in present_columns:
                    bt.drop_column(name)
