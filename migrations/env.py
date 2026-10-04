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
    # disable_existing_loggers=False: fileConfig's default (True) silently
    # disables every module logger created before the migration ran — after an
    # in-process alembic run (tests, embedded tooling) the application would log
    # nothing at all.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    """URL precedence: FY_DATABASE_URL env > explicitly injected config > settings.

    主控纠错 2026-10-04（数据事故复盘）：旧实现无条件覆盖
    ``config.set_main_option("sqlalchemy.url", _url())``，导致调用方
    （如迁移循环测试）注入的临时库被无视、迁移 downgrade 直接跑在开发库上，
    把开发数据整库清空。现在显式注入优先，测试与工具可安全隔离。
    """
    env_url = os.environ.get("FY_DATABASE_URL")
    if env_url:
        return env_url
    injected = config.get_main_option("sqlalchemy.url")
    # alembic.ini 里的占位符不算注入
    if injected and "driver://user:pass@localhost" not in injected:
        return injected
    try:
        from find_yourself.config import settings
        return settings().database_url
    except Exception:  # pragma: no cover - fallback for bare alembic runs
        return "sqlite:///.runtime/find-yourself.db"


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
