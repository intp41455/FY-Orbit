"""Alembic environment.

The database URL comes from ``FY_DATABASE_URL`` (or settings) — never from a
hardcoded connection string. ``target_metadata`` is the ORM metadata so future
autogenerate diffs stay accurate. The initial revision builds the full schema;
production always runs ``alembic upgrade head`` and never ``create_all``.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from find_yourself.db.base import Base
from find_yourself.db import models  # noqa: F401  (register models on metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("FY_DATABASE_URL")
    if not url:
        try:
            from find_yourself.config import settings
            url = settings().database_url
        except Exception:  # pragma: no cover - fallback for bare alembic runs
            url = "sqlite:///.runtime/find-yourself.db"
    return url


config.set_main_option("sqlalchemy.url", _url())


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
