"""initial schema: all frozen-contract tables + pgvector/FTS/vector indexes

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-30

This is the single initial, versioned migration. Production runs
``alembic upgrade head`` and never ``Base.metadata.create_all`` at runtime. The
table DDL is emitted from the ORM metadata so CHECK/FK/unique/version/money
constraints cannot drift from the models. Postgres-only artifacts (pgvector
extension, generated tsvector column, GIN full-text index, HNSW vector index)
are applied behind a dialect guard; SQLite test runs skip them.
"""

from collections.abc import Sequence

from alembic import op

from find_yourself.db.base import Base
from find_yourself.db import models  # noqa: F401  (register tables on metadata)

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        # pgvector extension for semantic search.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Emit every table with its CHECK / FK / UNIQUE / optimistic-version columns.
    Base.metadata.create_all(bind=bind)

    if is_pg:
        # Generated full-text vector over searchable memory/message content.
        op.execute(
            """
            ALTER TABLE search_documents
              ADD COLUMN IF NOT EXISTS tsvector tsvector
              GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content_hash::text, ''))) STORED
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_search_fts ON search_documents USING GIN (tsvector)")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_search_vec ON search_documents USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
