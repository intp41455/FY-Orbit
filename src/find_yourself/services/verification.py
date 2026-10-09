"""Trusted Verification Runner (可信验证执行器).

Adheres to:
- 18_工程代码工作台与主协调Agent全流程实施规格.md
- 16_社区Harness候选接入与普通模型对照验收.md

Responsibilities:
1. Executes actual test commands against real workspace code artifacts in isolated processes.
2. Captures exact execution command, process exit code, stdout/stderr logs, and duration.
3. Computes cryptographic SHA-256 digests and byte sizes for all target artifacts.
4. Generates tamper-evident verification receipts binding the exact artifact version.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrustedVerificationRunner:
    """Independent Trusted Verification Runner executing tests against real workspace artifacts."""

    @classmethod
    def compute_artifact_digests(
        cls,
        workspace_dir: Path | str,
        target_files: list[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], str]:
        """Calculates SHA-256 digests and file metadata for actual artifacts on disk."""
        ws_path = Path(workspace_dir).resolve()
        digests: dict[str, dict[str, Any]] = {}

        if target_files:
            files_to_hash = [ws_path / f for f in target_files]
        else:
            files_to_hash = [
                p for p in ws_path.rglob("*")
                if p.is_file()
                and not any(
                    part.startswith(".") or part in {"__pycache__", "venv"}
                    for part in p.relative_to(ws_path).parts
                )
            ]

        hasher = hashlib.sha256()
        for f in sorted(files_to_hash, key=lambda x: str(x)):
            if f.exists() and f.is_file():
                rel_path = f.relative_to(ws_path).as_posix()
                content = f.read_bytes()
                file_sha = hashlib.sha256(content).hexdigest()
                size_bytes = len(content)
                digests[rel_path] = {
                    "sha256": file_sha,
                    "size_bytes": size_bytes,
                    "path": rel_path,
                }
                hasher.update(f"{rel_path}:{file_sha}".encode("utf-8"))

        composite_hash = hasher.hexdigest()
        return digests, composite_hash

    @classmethod
    def execute_test(
        cls,
        workspace_dir: Path | str,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Executes actual test command in workspace_dir, captures exit code, logs, and artifact digests."""
        ws_path = Path(workspace_dir).resolve()
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory {workspace_dir} does not exist")

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        # Normalize command
        if isinstance(command, str):
            cmd_list = [command]
            use_shell = True
        else:
            cmd_list = [str(c) for c in command]
            use_shell = False

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                command if use_shell else cmd_list,
                cwd=str(ws_path),
                env=run_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=use_shell,
                check=False,
            )
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            exit_code = proc.returncode
            stdout_log = proc.stdout
            stderr_log = proc.stderr
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            exit_code = 124  # timeout
            stdout_log = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            stderr_log = f"Execution timed out after {timeout_seconds} seconds"
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            exit_code = 1
            stdout_log = ""
            stderr_log = f"Failed to execute command: {exc}"

        # Compute current artifact digests on disk
        digests, composite_hash = cls.compute_artifact_digests(ws_path, target_files)

        verification_id = f"verif-{uuid4().hex[:12]}"
        passed = (exit_code == 0)

        receipt = {
            "verification_id": verification_id,
            "verified_at": utcnow().isoformat(),
            "command": cmd_list if not use_shell else [command],
            "workspace_dir": str(ws_path),
            "exit_code": exit_code,
            "passed": passed,
            "stdout": stdout_log,
            "stderr": stderr_log,
            "duration_ms": duration_ms,
            "artifact_digests": digests,
            "composite_artifact_hash": composite_hash,
            "status": "PASS" if passed else "FAIL",
        }
        return receipt
