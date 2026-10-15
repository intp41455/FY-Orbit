"""启动期数据库迁移（P0 save-500 的系统性修复）。

背景：此前两个启动入口（``run.py``、``desktop/sidecar/sidecar_main.py``）
只做 ``create_all``（建缺失的表），从不执行 ``migrations/`` —— 任何
ALTER 型迁移（如 0048 的 ``dsl_flows.draft_version``）在存量库上永远缺席，
相关写入以 500 收场（DSL 草稿保存、流程创建）。

本模块在启动时执行 ``alembic upgrade head``。仓库迁移全部按幂等护栏
编写（建表前 ``has_table``、加列前查列），因此对新库、存量库、半截库
都能收敛，无需区分。请勿在此吞异常：迁移失败必须 loud，带着坏库
启动只会把 500 往后推。
"""
from __future__ import annotations

import os
from pathlib import Path


def _candidates() -> list[Path]:
    """按优先级找 migrations/ + alembic.ini 所在根目录。"""
    here = Path(__file__).resolve()
    roots: list[Path] = []
    # 源码运行：src/find_yourself/db/migrate.py → 上 3 级即仓库根。
    roots.append(here.parents[3])
    # 冻结包：migrations 随包放在 exe 所在目录（见 build.ps1 --add-data）。
    if getattr(__import__("sys"), "frozen", False):
        exe_dir = Path(__import__("sys").executable).resolve().parent
        roots += [exe_dir, exe_dir.parent]
    # 当前工作目录（兜底）。
    roots.append(Path.cwd())
    # 环境变量显式指定（测试与运维）。
    env = os.environ.get("FY_MIGRATIONS_DIR")
    if env:
        roots.insert(0, Path(env))
    seen: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.append(r)
    return seen


def find_migrations_root() -> Path | None:
    """找到同时含 alembic.ini 与 migrations/ 的目录，无则 None。"""
    for root in _candidates():
        try:
            if (root / "alembic.ini").is_file() and (root / "migrations").is_dir():
                return root
        except OSError:
            continue
    return None


def upgrade_head(db_url: str | None = None) -> str:
    """执行 ``alembic upgrade head``，返回升级到的 revision。

    ``db_url`` 为空时沿用 migrations/env.py 的默认优先级
    （显式注入 > FY_DATABASE_URL > settings）。
    migrations 脚本缺失时抛 FileNotFoundError（调用方决定是 fail-fast
    还是降级——run.py 选择 fail-fast，sidecar 选择告警继续，见各调用处注释）。
    """
    root = find_migrations_root()
    if root is None:
        raise FileNotFoundError(
            "找不到 alembic.ini + migrations/（dev 应在仓库根，"
            "冻结包应在 exe 目录）；拒绝带着未知 schema 启动"
        )
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(cfg, "head")

    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(cfg).get_heads()
    return ",".join(heads)
