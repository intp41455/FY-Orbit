"""Tests for Phase F6: Professional Engineering Task Delivery (W01–W08).

Verifies:
- W01: Baseline engineering vertical slice (specification -> sandbox implementation -> tests -> review)
- W03: Release proposal gates (unapproved merge/release is rejected)
- W04: Permit invalidation on commit/package drift
- W05: Authentic isolated execution of malicious scripts (credential scrubbing,
  process-tree kill, write audit, write-path confinement). NOTE: the sandbox is
  *not* a security boundary — it does not block the host Docker socket or the
  core DB volume. The old test claiming "socket blocking" was removed as fake
  green (ADR-011); what remains asserts only behaviour that can actually fail.
- W06/W07: Release proposal approval state machine and rollback
- W08: Programmatic migration downgrade/upgrade cycle
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from find_yourself.db.models import Proposal
from find_yourself.db.types import utcnow
from find_yourself.runtime.sandbox import IsolatedScriptRunner, SandboxConfig
from find_yourself.services.actor import Actor
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import Conflict, PermissionDenied
from find_yourself.services.proposal import ProposalService


@pytest.fixture
def isolated_runner(tmp_path: Path) -> IsolatedScriptRunner:
    # protected_root MUST be pinned to tmp_path. If left to its default it
    # resolves to Path.cwd() — i.e. the repository root — and any test that
    # exercises the write-audit path would deposit files into the real src/.
    protected = tmp_path / "protected"
    protected.mkdir()
    config = SandboxConfig(
        sandbox_root=str(tmp_path / "sandbox"),
        timeout_seconds=2.0,
        protected_root=str(protected),
    )
    return IsolatedScriptRunner(config)


def test_w05_isolated_sandbox_scrubs_all_credentials_and_secrets(isolated_runner: IsolatedScriptRunner):
    """W05: Malicious script attempts to dump environment variables to steal core database URL and secrets."""
    malicious_script = """
import os, sys

sensitive_prefixes = ("FY_", "AWS_", "DATABASE_", "SECRET_", "POSTGRES_", "TEMPORAL_", "MINIO_")
sensitive_subs = ("SECRET", "TOKEN", "PASSWORD", "KEY")

leaked = []
for k, v in os.environ.items():
    k_up = k.upper()
    if any(k_up.startswith(p) for p in sensitive_prefixes) or any(s in k_up for s in sensitive_subs):
        leaked.append(f"{k}={v}")

if leaked:
    print(f"LEAK_FOUND: {'; '.join(leaked)}")
    sys.exit(1)
else:
    print("NO_SECRETS_FOUND: isolated environment is clean")
    sys.exit(0)
"""
    result = isolated_runner.run_script(malicious_script, script_name="exfil_env.py")
    assert result.exit_code == 0
    assert "NO_SECRETS_FOUND" in result.stdout
    assert "LEAK_FOUND" not in result.stdout
    assert len(result.violations) == 0


def test_w05_isolated_sandbox_does_not_claim_to_block_host_paths(isolated_runner: IsolatedScriptRunner):
    """W05（已按 ADR-011 重写，原版是假绿）。

    原版断言「危险路径不可访问」，但它把 sandbox_root 指到 tmp_path，再让脚本去
    `os.path.exists('../../.env')` —— 该相对路径在临时目录下**物理上不可能存在**，
    所以测试永远绿，证明不了任何隔离能力。这正是 ADR-011 禁止的
    「用临时目录里天然不存在该路径制造绿灯」。

    重写后断言**真实行为**：本沙箱的隔离等级是 `process`，且**不声称**能阻断
    宿主路径。声明与实现一致才是可验收的；声称能阻断而实际不能，才是危险的那个。
    本用例会在「有人把假声明写回 docstring」时变红。
    """
    info = isolated_runner.config.describe_isolation()
    assert info["isolation_level"] == "process"
    assert info["security_boundary"] is False
    assert "docker_socket_block" in info["not_enforced"]
    assert "filesystem_jail" in info["not_enforced"]


def test_w05_sandbox_detects_writes_into_protected_root(isolated_runner: IsolatedScriptRunner):
    """W05 的真实边界：越界写入必须被**审计抓到**（S-3 移除即变红）。

    这是能真正失败的那一半——把文件写到受保护根，审计必须记录。
    """
    (isolated_runner.protected_root / "src").mkdir(parents=True, exist_ok=True)
    target = isolated_runner.protected_root / "src" / "planted_by_script.py"
    result = isolated_runner.run_script(
        f"from pathlib import Path; Path(r'{target.as_posix()}').write_text('x=1', encoding='utf-8')"
    )
    assert result.success is False
    assert any("out_of_sandbox_write_detected" in v for v in result.violations)
    assert any("planted_by_script.py" in p for p in result.out_of_sandbox_writes)


def test_w05_sandbox_rejects_path_traversal_write_targets(isolated_runner: IsolatedScriptRunner):
    """W05 的写侧边界（S0-1a/1b）：调用方传的逃逸路径必须被拒。

    这条在**真实生产配置下也会失败**（不改 sandbox_root 也能成立），
    因此满足 ADR-011「该测试能在真实生产配置下失败」的要求。
    """
    from find_yourself.runtime.sandbox import SandboxBoundaryViolation

    with pytest.raises(SandboxBoundaryViolation):
        isolated_runner.run_script("x=1", script_name="../../../evil.py")
    with pytest.raises(SandboxBoundaryViolation):
        isolated_runner.run_script("x=1", extra_files={"../../../evil.py": "x=1"})


def test_w05_isolated_sandbox_enforces_execution_timeout(isolated_runner: IsolatedScriptRunner):
    """W05: Malicious runaway/infinite loop script is killed strictly on timeout."""
    runaway_script = """
import time
while True:
    time.sleep(0.05)
"""
    result = isolated_runner.run_script(runaway_script, script_name="dos_loop.py", timeout=0.6)
    assert result.timed_out is True
    assert result.exit_code == -9
    assert any("timeout_exceeded" in v for v in result.violations)
    # Execution should be killed within ~1.5s
    assert result.duration_ms < 2500


def test_w05_isolated_sandbox_runs_benign_engineering_task(isolated_runner: IsolatedScriptRunner):
    """W05: Valid engineering verification runs cleanly in sandbox and returns structured artifact."""
    benign_script = """
import json, sys

result = {
    "status": "passed",
    "tests_run": 5,
    "failures": 0,
    "coverage_percent": 98.5
}
print(json.dumps(result))
sys.exit(0)
"""
    result = isolated_runner.run_script(benign_script, script_name="run_tests.py")
    assert result.success is True
    assert '"status": "passed"' in result.stdout


def test_w03_release_proposal_requires_owner_approval(session: Session):
    """W03: Release proposal cannot be decided or executed by unprivileged actors."""
    audit = AuditService(session)
    service = ProposalService(session, audit)
    owner = Actor.owner("owner-1")
    agent = Actor.service("agent-1", kind="agent")

    p = service.create(
        owner,
        operation="task.release",
        payload={"commit": "abc1234", "environment": "production", "digest": "sha256:112233"},
        reason="Deploy release v1.0.0",
        rollback="revert to previous sha",
    )
    assert p.status == "pending"

    # Service agent cannot decide release proposals (W03 gate)
    with pytest.raises(PermissionDenied):
        service.decide(agent, p.id, p.digest, approve=True)

    # Proposal remains pending
    session.refresh(p)
    assert p.status == "pending"


def test_w04_release_proposal_digest_and_drift_detection(session: Session):
    """W04: Changed code/package/commit invalidates prior approval permit."""
    audit = AuditService(session)
    service = ProposalService(session, audit)
    owner = Actor.owner("owner-1")

    p = service.create(
        owner,
        operation="task.release",
        payload={"commit": "abc1234", "environment": "production"},
        reason="Deploy release",
        rollback="revert",
    )

    # If client attempts to approve with mismatched digest (drift)
    tampered_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(Conflict) as exc:
        service.decide(owner, p.id, tampered_digest, approve=True)
    assert exc.value.code == "digest_mismatch"


def test_w06_w07_release_proposal_approval_and_rejection_mechanics(session: Session):
    """W06/W07: Release proposal transitions to approved_pending_execution or rejected."""
    audit = AuditService(session)
    service = ProposalService(session, audit)
    owner = Actor.owner("owner-1")

    # Case A: Approved release proposal creates outbox operation
    p1 = service.create(
        owner,
        operation="task.release",
        payload={"commit": "sha-v1", "environment": "production"},
        reason="Release v1",
        rollback="revert sha-v0",
    )
    decided1 = service.decide(owner, p1.id, p1.digest, approve=True)
    assert decided1.status == "approved_pending_execution"
    assert decided1.execution_id is not None

    # Case B: Rejected release proposal transitions to rejected without execution
    p2 = service.create(
        owner,
        operation="task.release",
        payload={"commit": "sha-bad", "environment": "production"},
        reason="Bad release",
        rollback="none",
    )
    decided2 = service.decide(owner, p2.id, p2.digest, approve=False)
    assert decided2.status == "rejected"
    assert decided2.execution_id is None


def test_w08_alembic_migration_downgrade_and_upgrade_cycle():
    """W08: Exercises Alembic migration downgrade to base and upgrade to head programmatically."""
    from alembic import command
    from alembic.config import Config

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{temp_db_path}")

        # 1. Upgrade to head
        command.upgrade(alembic_cfg, "head")

        # 2. Downgrade to base
        command.downgrade(alembic_cfg, "base")

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")

    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except OSError:
                pass


def test_fixture_never_points_protected_root_at_the_repo(isolated_runner: IsolatedScriptRunner):
    """护栏：`protected_root` 绝不能是仓库根。

    这是我自己踩过的坑：fixture 若不给 `protected_root`，它默认解析为
    `Path.cwd()`（仓库根），于是任何走「越界写入审计」路径的测试都会把文件
    真的写进仓库 `src/`。表现为——单跑通过、全量跑失败，且在工作区留下脏文件。
    本用例确保 fixture 永远把它钉在临时目录下。
    """
    repo_root = Path(__file__).resolve().parents[2].resolve()
    protected = isolated_runner.protected_root.resolve()
    assert protected != repo_root, (
        "protected_root 指向了仓库根 —— 测试会污染真实 src/，请把它钉到 tmp_path"
    )
    # 仓库根不得是 protected 的祖先（否则写入仍会落进仓库）
    assert repo_root not in protected.parents, (
        f"protected_root({protected}) 位于仓库内({repo_root})，测试会污染工作区"
    )
