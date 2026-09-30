"""Tests for Phase F6: Professional Engineering Task Delivery (W01–W08).

Verifies:
- W01: Baseline engineering vertical slice (specification -> sandbox implementation -> tests -> review)
- W03: Release proposal gates (unapproved merge/release is rejected)
- W04: Permit invalidation on commit/package drift
- W05: Authentic isolated execution of malicious scripts (credential scrubbing, socket blocking, CPU timeout)
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
    config = SandboxConfig(
        sandbox_root=str(tmp_path / "sandbox"),
        timeout_seconds=2.0,
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


def test_w05_isolated_sandbox_blocks_docker_socket_and_core_db_access(isolated_runner: IsolatedScriptRunner):
    """W05: Malicious script attempts to open host docker sockets or locate core db files."""
    malicious_script = """
import os, sys

dangerous_targets = [
    "/var/run/docker.sock",
    r"\\\\.\\pipe\\docker_engine",
    ".runtime/find-yourself.db",
    "../../.runtime/find-yourself.db",
    "../../.env",
]

accessed = []
for target in dangerous_targets:
    if os.path.exists(target):
        try:
            with open(target, "rb") as f:
                f.read(10)
            accessed.append(target)
        except Exception:
            pass

if accessed:
    print(f"BREACH: accessed {accessed}")
    sys.exit(1)
else:
    print("ISOLATED: dangerous paths inaccessible")
    sys.exit(0)
"""
    result = isolated_runner.run_script(malicious_script, script_name="probe_socket.py")
    assert result.exit_code == 0
    assert "ISOLATED" in result.stdout
    assert "BREACH" not in result.stdout


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
