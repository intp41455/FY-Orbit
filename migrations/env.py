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

#: The placeholder that ships in ``alembic.ini``. It means "no real URL was
#: configured" — it must never be mistaken for an explicit injection, or a bare
#: ``alembic upgrade head`` would stop honouring ``FY_DATABASE_URL``.
_PLACEHOLDER_URL = "driver://user:pass@localhost"


def _url() -> str:
    """URL precedence: **explicitly injected config** > FY_DATABASE_URL env > settings.

    主控纠错 2026-10-04（数据事故复盘）：旧实现无条件覆盖
    ``config.set_main_option("sqlalchemy.url", _url())``，导致调用方
    （如迁移循环测试）注入的临时库被无视、迁移 downgrade 直接跑在开发库上，
    把开发数据整库清空。

    ⚠️ 2026-10-04 二次纠错：上一版只做到「注入优先于 settings」，**env 仍排在
    注入之前**，而 docstring 却写着「显式注入优先」——**声明与实现不符**。
    实测到的真实事故路径：
      · `tests/unit/test_f6_engineering_delivery.py:249` 与
        `tests/unit/test_migration_chain_gate.py:172` 用
        ``cfg.set_main_option("sqlalchemy.url", "sqlite:///<temp>")`` 注入临时库；
      · 若开发者 shell 里导出了 ``FY_DATABASE_URL``（集成测试的常规前提，
        见 tests/integration/test_pg_*.py），旧顺序会让 env 压过注入；
      · 于是迁移的 upgrade/downgrade 跑在**开发库**上 → 整库清空。

    现按 docstring 的原始意图落地：**注入 > env > settings**。
    「注入」的判定必须排除 alembic.ini 里的占位符，否则 CLI 直跑
    （`alembic upgrade head`，无注入）会被占位符卡住而永远读不到 env。
    """
    # 1) 显式注入优先——这是测试与工具做隔离的唯一抓手，必须最优先。
    injected = config.get_main_option("sqlalchemy.url")
    if injected and _PLACEHOLDER_URL not in injected:
        return injected

    # 2) 无注入时，环境变量次之（集成测试/生产部署的常规入口）。
    env_url = os.environ.get("FY_DATABASE_URL")
    if env_url:
        return env_url

    # 3) 最后回落配置对象。
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
