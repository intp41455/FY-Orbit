"""Alembic migration 0016: cabin gameplay saves (W2 数码小屋玩法循环与背景探险).

Introduces ``cabin_saves`` — the per-owner authoritative gameplay save backing
探险采集 / 日常照料 / 随机事件 / 任务 / 离线收益:

* ``owner_id`` is the primary key: one save per owner, all state owner-private
  (a row never belongs to another tenant, so there is nothing to leak).
* ``materials``/``coins``/``intimacy``/``house_level`` are **server-authoritative**
  — they are only ever written by ``services/cabin_gameplay.py`` settlement, never
  by a client payload (``PUT /api/cabin/save`` rejects them with 422).
* ``quest_state``/``spots_state``/``companion_state``/``dust_state``/``settings``/
  ``unlocked_furniture`` hold the JSON sub-documents the service settles against.
* ``daily_seed`` makes each day's random-event sequence reproducible.
* ``last_seen_at``/``last_interaction_at``/``chest_keys``/``login_streak``/
  ``last_login_date`` back offline settlement, idle decay, chest keys and the
  consecutive-login bonus.

Revision note: 0014 was never allocated (W3 landed as 0015, which revises 0013),
so this task's migration takes the next free number 0016 rather than 0014.

Idempotency: table creation is guarded by ``sa.inspect(bind)``, following the
0005-0015 idiom.

Revision ID: 0016_cabin_gameplay
Revises: 0015_knowledge_base
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0016_cabin_gameplay"
down_revision: str | None = "0015_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("cabin_saves"):
        op.create_table(
            "cabin_saves",
            sa.Column("owner_id", sa.String(length=200), primary_key=True),
            sa.Column("materials", sa.JSON(), nullable=False),
            sa.Column("coins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("intimacy", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("house_level", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("quest_state", sa.JSON(), nullable=False),
            sa.Column("spots_state", sa.JSON(), nullable=False),
            sa.Column("daily_seed", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("settings", sa.JSON(), nullable=False),
            sa.Column("unlocked_furniture", sa.JSON(), nullable=False),
            sa.Column("companion_state", sa.JSON(), nullable=False),
            sa.Column("dust_state", sa.JSON(), nullable=False),
            sa.Column("chest_keys", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("login_streak", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_login_date", sa.String(length=10), nullable=False, server_default=""),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("coins >= 0", name="cabin_save_coins_nonnegative"),
            sa.CheckConstraint(
                "intimacy >= 0 AND intimacy <= 100", name="cabin_save_intimacy_range"
            ),
            sa.CheckConstraint(
                "house_level >= 1 AND house_level <= 5", name="cabin_save_level_range"
            ),
            sa.CheckConstraint("version >= 1", name="cabin_save_version_positive"),
            sa.CheckConstraint("chest_keys >= 0", name="cabin_save_keys_nonnegative"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("cabin_saves"):
        op.drop_table("cabin_saves")
