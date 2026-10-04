"""Alembic migration 0011: local user system (Route B multi-tenant).

Introduces:

* ``users`` — real end-user accounts (argon2id password hashes). The ``id``
  doubles as the ``owner_id`` used across all ownership-scoped tables.
* ``user_consents`` — GDPR consent records captured at registration. Consent
  rows are retained through account deletion (compliance proof), decoupled
  from the erased identity.
* ``auth_sessions.token_hash`` — sha256 of the opaque cookie token; sessions
  no longer need to store their bearer value in plaintext.

Backfill: a single bootstrap user with id ``owner`` is created so that every
pre-existing row whose ``owner_id`` is the legacy literal ``"owner"`` keeps a
resolving account. Its password hash is the invalid marker ``$locked$`` —
``verify_password`` treats malformed hashes as a plain failure, so the
bootstrap account cannot be logged into; it exists for data lineage only.

Idempotency: table creation and the column add are guarded by
``sa.inspect(bind)``, following the 0005-0010 idiom.

Revision ID: 0011_users_and_consents
Revises: 0010_audit_anchor_external
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011_users_and_consents"
down_revision: str | None = "0010_audit_anchor_external"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("email", sa.String(length=200), nullable=False),
            sa.Column("password_hash", sa.String(length=200), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.CheckConstraint(
                "status IN ('active', 'deleted')", name="ck_user_status"
            ),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if not insp.has_table("user_consents"):
        op.create_table(
            "user_consents",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=64),
                      sa.ForeignKey("users.id"), nullable=False),
            sa.Column("doc_id", sa.String(length=80), nullable=False,
                      server_default="privacy-policy"),
            sa.Column("doc_version", sa.String(length=40), nullable=False, server_default="v1"),
            sa.Column("agreed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        )
        op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"])

    cols = {c["name"] for c in insp.get_columns("auth_sessions")}
    if "token_hash" not in cols:
        op.add_column("auth_sessions",
                      sa.Column("token_hash", sa.String(length=64), nullable=True))

    # Backfill the bootstrap owner so legacy rows (owner_id='owner') resolve.
    users = sa.Table(
        "users", sa.MetaData(),
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("email", sa.String(length=200)),
        sa.Column("password_hash", sa.String(length=200)),
        sa.Column("display_name", sa.String(length=120)),
        sa.Column("status", sa.String(length=16)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer()),
    )
    conn = bind
    sel = sa.select(users.c.id).where(users.c.id == "owner")
    if conn.execute(sel).first() is None:
        conn.execute(users.insert().values(
            id="owner",
            email="bootstrap@local",
            # Not a valid argon2 hash: verify_password fails closed on it, so
            # this lineage-only account can never be logged into.
            password_hash="$locked$",
            display_name="Legacy single-owner (bootstrap)",
            status="active",
            created_at=sa.func.now(),
            version=1,
        ))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("auth_sessions")}
    if "token_hash" in cols:
        op.drop_column("auth_sessions", "token_hash")
    if insp.has_table("user_consents"):
        op.drop_table("user_consents")
    if insp.has_table("users"):
        op.drop_table("users")
