"""P13 · CLI 备份库名修复 + FY_ENVIRONMENT 陷阱收口（主控裁决包）。

P13-a（灾备正确性 bug）：``pg_dump -d`` 曾硬编码 ``findyourself``——设了
``FY_POSTGRES_DB`` 指向别的库时会**静默备错库**且不报错。修复后库名唯一真源
= ``FY_POSTGRES_DB``；本测试**真实改库名跑一次**验证（非 mock）。

P13-b：``--environment`` 不再从 ``FY_ENVIRONMENT`` 环境变量继承（解耦）；
对象存储分支的 Settings 校验失败给出**明确诊断**（含修复指引），不再裸抛
pydantic 堆栈。

容器不可用时真实链路用例诚实 SKIP（守卫类用例不依赖容器，照常执行）。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTAINER = os.environ.get("P11_DRILL_CONTAINER", "fy-p11-pg")
PG_USER = "fy"
PG_PASSWORD = "fy_dev_change_me_not_for_prod"
HOST_PORT = os.environ.get("P11_DRILL_PORT", "5434")
PYEXE = str(REPO / ".venv" / "Scripts" / "python.exe")


def _docker_ok() -> bool:
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        return False
    r = subprocess.run(["docker", "container", "inspect", "-f",
                        "{{.State.Running}}", CONTAINER],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _cli(args: list[str], env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    env.pop("FY_ENVIRONMENT", None)
    env.pop("FY_POSTGRES_DB", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PYEXE, "-m", "find_yourself.cli", *args],
                          capture_output=True, text=True, timeout=300, env=env,
                          cwd=str(REPO))


def _psql(sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", PG_USER,
                           "-d", db, "-tAc", sql],
                          capture_output=True, text=True, timeout=60)


# ---- P13-a：改库名必须真的备那个库（真实链路） --------------------------------
@pytest.mark.skipif(not _docker_ok(),
                    reason="需要运行中的演练容器（诚实跳过，不 mock 备份）")
def test_backup_follows_fy_postgres_db(tmp_path):
    marker = f"p13_marker_{uuid.uuid4().hex[:8]}"
    probe_db = f"p13_probe_{uuid.uuid4().hex[:6]}"
    target = tmp_path / "backup"
    manifest = target / "manifest.json"
    try:
        # ① 造一个「别的库」并放独有标记表
        assert _psql(f"CREATE DATABASE {probe_db}").returncode == 0
        assert _psql(f"CREATE TABLE {marker} (id int)", db=probe_db).returncode == 0

        # ② FY_POSTGRES_DB 指向探针库 → 备份必须备**它**（dump 里有标记表）
        r = _cli(["backup", "--execute", "--target", str(target),
                  "--manifest", str(manifest), "--include", "db",
                  "--container", CONTAINER],
                 env_extra={"FY_POSTGRES_DB": probe_db})
        assert r.returncode == 0, r.stdout + r.stderr
        dump_text = (target / next(p.name for p in target.glob("db-*.sql"))).read_text(
            encoding="utf-8", errors="replace")
        assert marker in dump_text, "备份没有跟着 FY_POSTGRES_DB 走——仍备的是默认库"
        assert "CREATE TABLE" in dump_text

        # ③ 不设 FY_POSTGRES_DB → 默认仍备 findyourself（回归保护）
        r2 = _cli(["backup", "--execute", "--target", str(target / "default"),
                   "--manifest", str(target / "default" / "manifest.json"),
                   "--include", "db", "--container", CONTAINER])
        assert r2.returncode == 0, r2.stdout + r2.stderr
        dump2 = (target / "default" / next(
            p.name for p in (target / "default").glob("db-*.sql"))).read_text(
            encoding="utf-8", errors="replace")
        assert "CREATE TABLE" in dump2
        assert marker not in dump2, "默认库的备份不应包含探针库的标记表"
    finally:
        _psql(f"DROP DATABASE IF EXISTS {probe_db}")


# ---- P13-b：--environment 不继承 FY_ENVIRONMENT ------------------------------
def test_environment_arg_does_not_inherit_fy_environment(monkeypatch):
    from find_yourself.cli import build_parser

    monkeypatch.setenv("FY_ENVIRONMENT", "recovery")  # 非法值：曾经会被 CLI 静默继承
    args = build_parser().parse_args(["restore", "--manifest", "x.json"])
    assert args.environment == "local", "--environment 必须不再从 FY_ENVIRONMENT 继承"


# ---- P13-b：Settings 校验失败给明确诊断（真实触发，非 mock） ------------------
@pytest.mark.skipif(not _docker_ok(),
                    reason="需要运行中的演练容器（真实触发对象存储分支）")
def test_restore_with_bad_fy_environment_gives_clear_diagnosis(tmp_path):
    """复现 P11 演练踩到的原始陷阱：export FY_ENVIRONMENT=recovery 后 restore。

    修复后行为：不再裸抛 pydantic 堆栈，而是给出含「FY_ENVIRONMENT」与修复
    指引的明确诊断（exit 2，对象恢复 FAIL，DB 恢复不受影响）。
    """
    target = tmp_path / "backup"
    manifest = target / "manifest.json"
    bak = _cli(["backup", "--execute", "--target", str(target),
                "--manifest", str(manifest), "--include", "db",
                "--container", CONTAINER])
    assert bak.returncode == 0, bak.stdout + bak.stderr
    r = _cli(["restore", "--execute", "--environment", "recovery",
              "--manifest", str(manifest), "--container", CONTAINER, "--keep-db"],
             env_extra={"FY_ENVIRONMENT": "recovery"})
    combined = r.stdout + r.stderr
    assert r.returncode == 2  # 对象恢复 FAIL（DB 恢复本身成功）
    assert "FY_ENVIRONMENT" in combined, f"必须给出明确诊断，实际：{combined[-400:]}"
    assert "命令行参数" in combined
    assert "pydantic" not in combined.lower() or "提示" in combined
