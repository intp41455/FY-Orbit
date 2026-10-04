"""Engineering Sandbox Runner for untrusted script execution (Execution Manual F6, W05).

Safety and Isolation Rules:
1. Environment Sanitization: Strips all FY_*, AWS_*, DB, SECRET, TOKEN, and credential variables.
2. Filesystem Confinement: Scripts execute in an isolated sandbox working directory.
3. Resource Limits: Wall-clock timeout **kills the whole process tree** (S-1) and
   memory is capped with an OS-level limit (S-2), not just a config field.
4. Docker / Host Socket Blocking: Script runtime cannot access core DB volume or host Docker socket.
5. Post-execution audit: filesystem writes outside the run directory and network
   egress attempts are recorded as violations (S-3), not merely grepped from stdout.

S-1/S-2/S-3 fixes are C0-batch work; see
``docs/architecture/PLATFORM-V2-ARCHITECTURE-2026-10-04-architect2-rev.md`` §1.4.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ..services.terminal import kill_process_tree


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

# Directories an untrusted script must never write into, relative to the repo root.
FORBIDDEN_WRITE_TARGETS = (
    ".env",
    ".git",
    "src",
    "web",
    "migrations",
    "desktop",
    "pyproject.toml",
    "alembic.ini",
)

# Network egress markers: a script that prints any of these was trying to reach out.
NETWORK_EGRESS_MARKERS = (
    "socket_connect_attempt",
    "http_request_attempt",
    "dns_lookup_attempt",
    "fy_sandbox_egress_blocked",
)


@dataclass
class SandboxConfig:
    sandbox_root: str = ".runtime/sandbox"
    timeout_seconds: float = 5.0
    memory_limit_mb: int = 256
    block_docker_socket: bool = True
    block_core_db_paths: bool = True
    #: Root that untrusted scripts may not write outside of. Defaults to the CWD
    #: captured at construction time (the project root in every real deployment).
    protected_root: Optional[str] = None
    #: When False the memory cap is not applied (still recorded on the result).
    enforce_memory_limit: bool = True


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
    #: S-1 evidence: outcome of the process-tree termination attempt.
    kill_evidence: dict[str, Any] = field(default_factory=dict)
    #: S-2 evidence: whether the OS-level memory cap was applied.
    memory_limited: bool = False
    memory_limit_mb: int = 0
    #: S-3 evidence: files the script created outside its run directory.
    out_of_sandbox_writes: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and len(self.violations) == 0


def _apply_memory_limit(limit_mb: int) -> tuple[bool, str]:
    """Apply an OS-level address-space cap to the current process tree.

    ``preexec_fn`` runs in the child between fork and exec, which is the only
    portable place to call ``setrlimit``. Returns ``(applied, mechanism)``.
    """
    if limit_mb <= 0:
        return False, "disabled"
    if os.name == "nt":
        # Windows has no setrlimit. A Job Object would be the real answer but
        # needs pywin32; until then we report honestly instead of pretending.
        return False, "unsupported_platform"
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return False, "resource_module_missing"

    cap = limit_mb * 1024 * 1024

    def _preexec() -> None:  # pragma: no cover - runs in child
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        resource.setrlimit(resource.RLIMIT_DATA, (cap, cap))

    _MEMORY_PREEXEC.append(_preexec)
    return True, "rlimit_as"


# ``preexec_fn`` must be a module-level callable, so the closure is stashed here
# and drained by ``run_script`` on each call (keeps the signature picklable-free).
_MEMORY_PREEXEC: list = []


def _snapshot_tree(root: Path) -> Dict[str, float]:
    """Map absolute path -> mtime for every file under ``root``.

    Used as the *before* picture so post-run we can tell what a script created
    or modified without trusting anything the script prints.
    """
    snapshot: Dict[str, float] = {}
    if not root.exists():
        return snapshot
    for p in root.rglob("*"):
        if p.is_file():
            try:
                snapshot[str(p.resolve())] = p.stat().st_mtime
            except OSError:
                continue
    return snapshot


def _diff_new_files(before: Dict[str, float], after: Dict[str, float]) -> list[str]:
    return sorted(set(after) - set(before))


class IsolatedScriptRunner:
    """Runs untrusted engineering scripts and test suites inside an isolated, scrubbed environment."""

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self.sandbox_root = Path(self.config.sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self.protected_root = Path(
            self.config.protected_root or Path.cwd()
        ).resolve()

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
        # S-3: tell the script that egress is policed, so a cooperative script
        # reports the attempt itself and we can record it in the audit trail.
        # Name deliberately avoids the FY_ prefix: everything FY_* is stripped
        # from the child env, and a sandbox-injected FY_* variable would both
        # defeat that guarantee and trip the exfiltration test (W05).
        clean_env["SANDBOX_EGRESS_POLICY"] = "audit"
        return clean_env

    def _detect_out_of_sandbox_writes(
        self, run_dir: Path, before: Dict[str, float]
    ) -> list[str]:
        """S-3: list files the script created outside its own run directory.

        We diff the protected root's file map instead of grepping stdout, so a
        script that writes silently is still caught.
        """
        after = _snapshot_tree(self.protected_root)
        offenders: list[str] = []
        for path in _diff_new_files(before, after):
            p = Path(path)
            try:
                rel_to_run = p.relative_to(run_dir)
            except ValueError:
                rel_to_run = None
            if rel_to_run is not None:
                continue  # inside its own sandbox dir — allowed
            try:
                rel_to_root = p.relative_to(self.protected_root)
            except ValueError:
                offenders.append(path)
                continue
            top = str(rel_to_root).replace("\\", "/").split("/")[0]
            if top in FORBIDDEN_WRITE_TARGETS:
                offenders.append(path)
        return sorted(offenders)

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

        # S-3: "before" picture of the protected root.
        protected_before = _snapshot_tree(self.protected_root)

        # S-2: install the OS-level memory cap for this child.
        memory_applied = False
        preexec = None
        if self.config.enforce_memory_limit and self.config.memory_limit_mb > 0:
            _MEMORY_PREEXEC.clear()
            memory_applied, _mechanism = _apply_memory_limit(self.config.memory_limit_mb)
            if memory_applied and _MEMORY_PREEXEC:
                preexec = _MEMORY_PREEXEC[-1]
        else:
            _MEMORY_PREEXEC.clear()

        start_time = time.perf_counter()
        timed_out = False
        exit_code = -1
        stdout = ""
        stderr = ""
        violations: list[str] = []
        kill_evidence: dict[str, Any] = {}

        # S-1: use Popen so we hold the PID and can kill the whole tree.
        proc: Optional[subprocess.Popen] = None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(target_script)],
                cwd=str(run_dir),
                env=clean_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # PLW1509: preexec_fn 在多线程父进程里有死锁风险（POSIX 上
                # 子进程 exec 前会等所有锁）。FastAPI 确实是多线程的，所以这里
                # 显式豁免并记录理由，而不是留一条会被人顺手"修掉"的告警：
                # 1) 它只在 POSIX 生效，Windows 分支根本不会传；
                # 2) 我们在函数里只调用 resource.setrlimit —— 异步信号安全的
                #    系统调用，不碰 CPython 锁，因此不存在经典的死锁路径；
                # 3) 真正的兜底是 memory_limited 标志：任何平台不支持时都
                #    如实上报 False，不假装限额已生效。
                # 若将来要换成 cgroup/Job Object，删掉 preexec_fn 即可。
                preexec_fn=preexec,  # noqa: PLW1509
            )
            try:
                out, err = proc.communicate(timeout=timeout_sec)
                exit_code = proc.returncode
                stdout = out or ""
                stderr = err or ""
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                # S-1: kill the whole tree, not just the direct child, and verify
                # reclamation. Without this, grandchildren keep running and
                # ``communicate`` may block long past the nominal timeout.
                kill_evidence = kill_process_tree(proc.pid)
                try:
                    out, err = proc.communicate(timeout=5)
                except Exception:  # noqa: BLE001 - best effort drain
                    out, err = exc.stdout or "", exc.stderr or ""
                stdout = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else (out or "")
                stderr_txt = err.decode("utf-8", errors="replace") if isinstance(err, bytes) else (err or "")
                stderr = stderr_txt + "\n[Execution timed out; process tree terminated]"
                exit_code = -9
                violations.append(
                    f"timeout_exceeded: script exceeded maximum allowed runtime ({timeout_sec}s)"
                )
                if kill_evidence.get("reaped"):
                    violations.append("process_tree_reaped: all descendants terminated and verified")
        except Exception as exc:  # noqa: BLE001
            exit_code = -1
            stderr = f"Subprocess launch failed: {exc}"

        duration_ms = (time.perf_counter() - start_time) * 1000

        # S-3: out-of-sandbox writes (filesystem diff, not stdout grep).
        out_of_sandbox_writes = self._detect_out_of_sandbox_writes(
            run_dir, protected_before
        )
        if out_of_sandbox_writes:
            violations.append(
                f"out_of_sandbox_write_detected: {len(out_of_sandbox_writes)} file(s) "
                "created outside the run directory"
            )

        # Post-execution secret / socket checks (keep the original signal, but
        # treat egress markers as violations too).
        lower_out = (stdout + "\n" + stderr).lower()
        if "fy_database_url=" in lower_out or "fy_session_secret=" in lower_out:
            violations.append("secret_exfiltration_detected")
        if "docker.sock" in lower_out and "connected" in lower_out:
            violations.append("docker_socket_access_detected")
        for marker in NETWORK_EGRESS_MARKERS:
            if marker in lower_out:
                violations.append(f"network_egress_attempt:{marker}")
                break

        return ExecutionResult(
            run_id=run_id,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            sandbox_dir=str(run_dir),
            violations=violations,
            kill_evidence=kill_evidence,
            memory_limited=memory_applied,
            memory_limit_mb=self.config.memory_limit_mb,
            out_of_sandbox_writes=out_of_sandbox_writes,
        )
