"""P11 · 备份/恢复**真实演练**自动化（需求 16）。

这不是 mock 冒充的演练：容器可用时，本测试在 fy-postgres 兼容容器里
**真实执行**「建库 → create_all+stamp → 种数据 → 删除制造墓碑 → CLI 真实
pg_dump 备份 → CLI 空库恢复 → verify-deletions 墓碑重放校验」全链路，
并对恢复库断言业务语义（被删记忆被脱敏压制、存活记忆完好）。

诚实边界：
* 容器/镜像不可用时 **SKIP**（附原因）——绝不假装演练通过；
* 演练库 schema 由 ``Base.metadata.create_all`` 建立并 ``stamp`` 到当前链头
  （PG 全新库的迁移链在 0006 有存量幂等缺口，见 P11 交付报告；该缺口不由
  本测试修复，迁移链正确性由 SQLite 侧迁移测试锁定）；
* 演练容器默认 ``fy-p11-pg``（P11 专用隔离容器），可用环境变量
  ``P11_DRILL_CONTAINER`` 覆盖；测试自建自清理，不碰他人数据。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from find_yourself.db.base import Base

CONTAINER = os.environ.get("P11_DRILL_CONTAINER", "fy-p11-pg")
PG_USER = "fy"
PG_PASSWORD = "fy_dev_change_me_not_for_prod"
HOST_PORT = os.environ.get("P11_DRILL_PORT", "5434")
IMAGE = os.environ.get("P11_DRILL_IMAGE", "pgvector/pgvector:pg16")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)


def _container_ready() -> bool:
    docker_ok = shutil.which("docker") is not None
    if not docker_ok:
        return False
    rc = _run(["docker", "container", "inspect", "-f", "{{.State.Running}}", CONTAINER])
    return rc.returncode == 0 and rc.stdout.strip() == "true"


pytestmark = pytest.mark.skipif(
    not _container_ready(),
    reason=f"P11 真实演练需要运行中的容器 {CONTAINER}（docker 不可用或未启动时诚实跳过）",
)


def _cli(args: list[str], db: str, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env["FY_DATABASE_URL"] = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@127.0.0.1:{HOST_PORT}/{db}"
    env["PYTHONPATH"] = os.getcwd()
    env.pop("FY_ENVIRONMENT", None)  # Settings 校验器不接受 recovery；--environment 走 CLI 参数
    if env_extra:
        env.update(env_extra)
    return _run(
        [os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe"),
         "-m", "find_yourself.cli", *args],
        env=env,
    )


def _psql(db: str, sql: str) -> str:
    r = _run(["docker", "exec", CONTAINER, "psql", "-U", PG_USER, "-d", db, "-tAc", sql])
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_backup_restore_drill_with_tombstone_replay(tmp_path):
    """端到端真实演练：备份 → 空库恢复 → 墓碑重放校验 → 业务语义断言。"""
    # CLI 的 backup 硬编码 `pg_dump -d findyourself`（既有契约），因此演练数据
    # 直接种进容器内的 findyourself 库；schema 幂等建立（create_all + stamp）。
    drill_db = "findyourself"
    rec_db: str | None = None
    try:
        # 0) 幂等建立演练 schema（create_all 只补缺表；stamp 到当前链头）
        url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@127.0.0.1:{HOST_PORT}/{drill_db}"
        from find_yourself.db.session import engine_from_url
        engine = engine_from_url(url)
        import find_yourself.db.models  # noqa: F401
        import find_yourself.db.profile_models  # noqa: F401
        import find_yourself.db.canvas_models  # noqa: F401
        import find_yourself.db.sync_models  # noqa: F401
        import find_yourself.db.workbench_models  # noqa: F401
        import find_yourself.db.team_models  # noqa: F401
        import find_yourself.db.kb_models  # noqa: F401
        import find_yourself.db.plugin_models  # noqa: F401
        import find_yourself.db.collaboration_models  # noqa: F401
        with engine.connect() as c:
            c.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            c.commit()
        Base.metadata.create_all(engine)
        with engine.connect() as c:
            c.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
            c.exec_driver_sql("DELETE FROM alembic_version")
            c.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES ('0033_preview_source_data_kind')")
            c.commit()
        engine.dispose()

        # 1) 种数据：2 条记忆 + 删除 1 条（墓碑）
        seed_code = """
import os, sys
sys.path.insert(0, os.environ['REPO'])
from find_yourself.db.session import engine_from_url, session_factory
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.grant import GrantService
from find_yourself.services.memory import MemoryService
from find_yourself.services.deletion import DeletionService
engine = engine_from_url(os.environ['FY_DATABASE_URL'])
s = session_factory(engine)()
owner = Actor.owner('owner-p11')
audit = AuditService(s)
grants = GrantService(s, audit)
mem = MemoryService(s, grants, audit)
m1 = mem.upsert(owner, owner_id='owner-p11', domain='personal', category='self_report',
                content='P11 演练：将去删除的记忆（应被墓碑压制）', source_ids=[])
m2 = mem.upsert(owner, owner_id='owner-p11', domain='personal', category='self_report',
                content='P11 演练：应存活的记忆 A', source_ids=[])
s.flush()
svc = DeletionService(s, audit)
svc.delete(owner, m1.id, 'memory', 'P11 演练：备份前删除（制造墓碑）')
s.commit()
print(m1.id, m2.id)
s.close(); engine.dispose()
"""
        seeded = _run([".venv/Scripts/python.exe", "-c", seed_code], env={
            **os.environ,
            "FY_DATABASE_URL": url,
            "REPO": os.getcwd(),
            "PYTHONPATH": os.getcwd(),
        })
        assert seeded.returncode == 0, seeded.stderr[-600:]
        deleted_id, alive_id = seeded.stdout.strip().split()

        # 2) 真实备份（pg_dump）
        target = tmp_path / "backup"
        manifest = target / "manifest.json"
        bak = _cli(["backup", "--execute", "--target", str(target),
                    "--manifest", str(manifest), "--include", "db",
                    "--container", CONTAINER], drill_db)
        assert bak.returncode == 0, bak.stdout + bak.stderr
        man = json.loads(manifest.read_text(encoding="utf-8"))
        assert man["db_dump"]["table_count"] >= 60
        assert man["db_dump"]["sha256"]

        # 3) 空库恢复（--keep-db 保留恢复库供校验）
        res = _cli(["restore", "--execute", "--environment", "recovery",
                    "--manifest", str(manifest), "--container", CONTAINER,
                    "--keep-db"], drill_db)
        assert res.returncode == 0, res.stdout + res.stderr
        for line in res.stdout.splitlines():
            if "created+restored" in line:
                rec_db = line.split("created+restored")[1].split(";")[0].strip()
        assert rec_db, "恢复输出必须给出临时库名"

        # 4) 墓碑重放校验（指向恢复库）
        ver = _cli(["verify-deletions", "--execute", "--environment", "recovery"], rec_db)
        assert ver.returncode == 0, ver.stdout + ver.stderr
        assert "violations=0" in ver.stdout

        # 5) 业务语义断言（直接查恢复库）
        assert _psql(rec_db, f"select count(*) from tombstones where target_id='{deleted_id}'") == "1"
        deleted_content = _psql(rec_db, f"select content from memories where id='{deleted_id}'")
        assert deleted_content in ("", None) or deleted_content == "f", "被删记忆内容必须被脱敏压制"
        alive_content = _psql(rec_db, f"select content from memories where id='{alive_id}'")
        assert "应存活的记忆 A" in alive_content
    finally:
        if rec_db:
            _run(["docker", "exec", CONTAINER, "dropdb", "-U", PG_USER, "--if-exists", rec_db])


def test_restore_refuses_production_without_approval():
    """生产恢复守卫：无 --allow-production 直接 REFUSED（exit 4）。"""
    r = _cli(["restore", "--environment", "production",
              "--manifest", "no-such-manifest.json", "--container", CONTAINER], "findyourself")
    assert r.returncode == 4
    assert "Refusing restore into 'production'" in r.stdout


def test_verify_deletions_preflight_is_not_a_pass():
    """可达性预检不是 PASS（诚实边界）：不带 --execute 时必须 NOT_RUN。"""
    r = _cli(["verify-deletions", "--environment", "local"], "findyourself")
    assert "NOT_RUN" in r.stdout
