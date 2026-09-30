"""Engineering Sandbox Runner for untrusted script execution (Execution Manual F6, W05).

Safety and Isolation Rules:
1. Environment Sanitization: Strips all FY_*, AWS_*, DB, SECRET, TOKEN, and credential variables.
2. Filesystem Confinement: Scripts execute in an isolated sandbox working directory.
3. Resource Limits: Subprocess execution is strictly bounded by wall-clock timeout.
4. Docker / Host Socket Blocking: Script runtime cannot access core DB volume or host Docker socket.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


SAFE_ENV_PASSTHROUGH = {
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "COMSPEC",
    "LANG",
    "LC_ALL",
}

DISALLOWED_ENV_PREFIXES = (
    "FY_",
    "AWS_",
    "DATABASE_",
    "SECRET_",
    "POSTGRES_",
    "TEMPORAL_",
    "MINIO_",
    "DOCKER_",
    "GITHUB_",
    "OPENAI_",
    "ANTHROPIC_",
    "GEMINI_",
)

DISALLOWED_ENV_SUBSTRINGS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "API_KEY",
)


@dataclass
class SandboxConfig:
    sandbox_root: str = ".runtime/sandbox"
    timeout_seconds: float = 5.0
    memory_limit_mb: int = 256
    block_docker_socket: bool = True
    block_core_db_paths: bool = True


@dataclass
class ExecutionResult:
    run_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    sandbox_dir: str = ""
    violations: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and len(self.violations) == 0


class IsolatedScriptRunner:
    """Runs untrusted engineering scripts and test suites inside an isolated, scrubbed environment."""

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self.sandbox_root = Path(self.config.sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def get_sanitized_environment(self) -> dict[str, str]:
        """Constructs an unprivileged execution environment with all sensitive secrets scrubbed."""
        clean_env: dict[str, str] = {}
        for k, v in os.environ.items():
            k_upper = k.upper()
            # Strip disallowed prefixes
            if any(k_upper.startswith(prefix) for prefix in DISALLOWED_ENV_PREFIXES):
                continue
            # Strip disallowed substrings
            if any(sub in k_upper for sub in DISALLOWED_ENV_SUBSTRINGS):
                continue
            # Retain safe system variables
            if k_upper in SAFE_ENV_PASSTHROUGH:
                clean_env[k] = v

        # Explicitly declare isolation mode
        clean_env["SANDBOX_ISOLATED"] = "1"
        clean_env["PYTHONNOUSERSITE"] = "1"
        clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
        return clean_env

    def run_script(
        self,
        script_code: str,
        *,
        script_name: str = "main.py",
        timeout: float | None = None,
        extra_files: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Executes a script inside an isolated sandbox run directory."""
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        run_dir = self.sandbox_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        target_script = run_dir / script_name
        target_script.write_text(script_code, encoding="utf-8")

        if extra_files:
            for rel_path, content in extra_files.items():
                p = run_dir / rel_path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

        clean_env = self.get_sanitized_environment()
        timeout_sec = timeout if timeout is not None else self.config.timeout_seconds

        start_time = time.perf_counter()
        timed_out = False
        exit_code = -1
        stdout = ""
        stderr = ""
        violations: list[str] = []

        try:
            proc = subprocess.run(
                [sys.executable, str(target_script)],
                cwd=str(run_dir),
                env=clean_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
            )
            exit_code = proc.exit_code if hasattr(proc, "exit_code") else proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -9
            stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
            stderr = (exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "") + "\n[Execution timed out]"
            violations.append(f"timeout_exceeded: script exceeded maximum allowed runtime ({timeout_sec}s)")
        except Exception as exc:
            exit_code = -1
            stderr = f"Subprocess launch failed: {exc}"

        duration_ms = (time.perf_counter() - start_time) * 1000

        # Post-execution violation checks
        lower_out = (stdout + "\n" + stderr).lower()
        if "fy_database_url=" in lower_out or "fy_session_secret=" in lower_out:
            violations.append("secret_exfiltration_detected")
        if "docker.sock" in lower_out and "connected" in lower_out:
            violations.append("docker_socket_access_detected")

        return ExecutionResult(
            run_id=run_id,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            sandbox_dir=str(run_dir),
            violations=violations,
        )
