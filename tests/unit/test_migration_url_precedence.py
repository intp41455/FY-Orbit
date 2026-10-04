"""迁移 URL 优先级回归测试（数据事故防线）。

背景（真实事故路径，非假设）
--------------------------
`migrations/env.py::_url()` 决定 alembic 连哪个库。迁移循环测试用
``cfg.set_main_option("sqlalchemy.url", "sqlite:///<temp>")`` 注入临时库来隔离，
而集成测试的前提是 shell 里导出 ``FY_DATABASE_URL``（见 ``tests/integration/test_pg_*.py``）。

一旦 **env 压过注入**，开发者本地同时满足这两件事时：
注入的临时库被无视 → ``alembic downgrade`` 跑在 **FY_DATABASE_URL 指向的库**上
→ 开发库被整库清空。

docstring 一直声称「显式注入优先」，但实现的顺序曾是 env 在前 ——
**声明与实现不符**，这正是 ADR-011 说的第二类假绿（文档假绿）。

本文件把正确顺序钉死：**注入 > env > settings**。
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from alembic.config import Config

ENV_PY = Path(__file__).resolve().parents[2] / "migrations" / "env.py"
PLACEHOLDER = "driver://user:pass@localhost"


def _load_env_py(monkeypatch: pytest.MonkeyPatch, injected: str | None) -> types.ModuleType:
    """Import migrations/env.py under a fake ``alembic.context``.

    ``from alembic import context`` resolves the *attribute* on the ``alembic``
    package, so patching that attribute (not just ``sys.modules``) is what makes
    the fake take effect.
    """
    import alembic

    cfg = Config()
    if injected is not None:
        cfg.set_main_option("sqlalchemy.url", injected)

    fake = types.ModuleType("alembic.context")
    fake.config = cfg
    fake.is_offline_mode = lambda: True
    fake.configure = lambda **kw: None
    fake.begin_transaction = contextlib.nullcontext
    fake.run_migrations = lambda: None

    monkeypatch.setattr(alembic, "context", fake)
    monkeypatch.setitem(sys.modules, "alembic.context", fake)

    spec = importlib.util.spec_from_file_location("_probe_env_under_test", ENV_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_injected_url_beats_env(monkeypatch: pytest.MonkeyPatch):
    """🔴 核心判据：显式注入必须压过 FY_DATABASE_URL（否则开发库会被清空）。"""
    monkeypatch.setenv("FY_DATABASE_URL", "postgresql://dev-db-should-not-win/db")
    module = _load_env_py(monkeypatch, injected="sqlite:///injected-temp.db")
    assert module._url() == "sqlite:///injected-temp.db", (
        "env 劫持了显式注入 —— 迁移测试会跑在开发库上，数据事故路径仍然存在"
    )


def test_env_used_when_nothing_injected(monkeypatch: pytest.MonkeyPatch):
    """无注入时 env 必须生效（集成测试与生产部署的常规入口）。"""
    monkeypatch.setenv("FY_DATABASE_URL", "postgresql://real-env/db")
    module = _load_env_py(monkeypatch, injected=None)
    assert module._url() == "postgresql://real-env/db"


def test_alembic_ini_placeholder_is_not_treated_as_injection(monkeypatch: pytest.MonkeyPatch):
    """alembic.ini 的占位符不算注入——否则 CLI 直跑时永远读不到 env。"""
    monkeypatch.setenv("FY_DATABASE_URL", "postgresql://from-env/db")
    module = _load_env_py(monkeypatch, injected=PLACEHOLDER)
    assert module._url() == "postgresql://from-env/db", (
        "占位符被误判为注入，导致 `alembic upgrade head` 不再读 FY_DATABASE_URL"
    )


def test_docstring_matches_implementation():
    """硬判据：docstring 声称的优先级顺序必须与代码顺序一致（防再次自相矛盾）。"""
    src = ENV_PY.read_text(encoding="utf-8")
    # 取 `_url()` 自己的 docstring —— 不是模块级那个（两者都在文件里，
    # 直接 split('"""') 会拿到模块 docstring，是写这条用例时踩过的坑）。
    body = src.split("def _url()", 1)[1]
    doc = body.split('"""', 2)[1]

    precedence_line = [ln for ln in doc.splitlines() if "precedence" in ln.lower()]
    assert precedence_line, f"_url() docstring 必须显式声明优先级，实际：{doc[:120]!r}"
    line = precedence_line[0].lower()
    assert line.index("inject") < line.index("env"), (
        "docstring 的优先级顺序与实现不符（注入必须写在 env 前面）"
    )

    # 代码里：注入分支必须在 env 分支之前
    injected_at = body.index("get_main_option")
    env_at = body.index('os.environ.get("FY_DATABASE_URL")')
    assert injected_at < env_at, (
        "代码顺序反了：env 分支出现在注入分支之前 —— 数据事故路径重现"
    )
