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


#: 验证命令白名单（argv[0] 的 basename，小写去扩展名后比对）。
#: 只允许测试运行器：命令只能在调用方已绑定的工作区内执行，
#: 与终端功能同等信任级别；白名单防的是"借验证接口跑任意系统二进制"。
ALLOWED_VERIFICATION_COMMANDS = frozenset({
    "python", "python3", "pytest", "py.test",
    "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
    "go", "cargo", "dotnet", "mvn", "gradle", "uv", "uvx",
    "ruby", "php",
})

#: 单次验证最多哈希的文件数（防 rglob 全盘遍历 DoS）。
MAX_HASHED_FILES = 5000
#: 单个文件哈希上限（超限跳过并标记，不读入内存）。
MAX_HASH_FILE_BYTES = 50 * 1024 * 1024
#: 验证命令超时上/下界（秒）。
VERIFICATION_TIMEOUT_MIN_SECONDS = 1.0
VERIFICATION_TIMEOUT_MAX_SECONDS = 600.0


class VerificationBoundaryError(ValueError):
    """调用方传入的工作区/命令/文件越界（调用方应转译为 4xx）。"""


def _exe_basename(argv0: str) -> str:
    name = Path(str(argv0)).name.lower()
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def validate_verification_command(command: list[str] | str | None) -> list[str]:
    """校验验证命令并规范化为 argv 列表。

    - 禁止 ``str`` 形式（会走 ``shell=True``），只接受 argv 列表；
    - ``argv[0]`` 必须在 :data:`ALLOWED_VERIFICATION_COMMANDS` 内；
    - 拒绝 NUL 字节与空命令。
    """
    if command is None:
        raise VerificationBoundaryError("command is required")
    if isinstance(command, str):
        raise VerificationBoundaryError(
            "string command is rejected: pass argv list instead (shell execution is disabled)"
        )
    cmd_list = [str(c) for c in command]
    if not cmd_list or not cmd_list[0].strip():
        raise VerificationBoundaryError("command is empty")
    if "\x00" in cmd_list[0]:
        raise VerificationBoundaryError("command contains a NUL byte")
    if _exe_basename(cmd_list[0]) not in ALLOWED_VERIFICATION_COMMANDS:
        raise VerificationBoundaryError(
            f"command {cmd_list[0]!r} is not an allowed test runner "
            f"(allowed: {sorted(ALLOWED_VERIFICATION_COMMANDS)})"
        )
    if len(cmd_list) > 64:
        raise VerificationBoundaryError("command has too many arguments (max 64)")
    return cmd_list


def clamp_verification_timeout(timeout_seconds: float | None) -> float:
    """把超时钳制到允许区间。"""
    try:
        t = float(timeout_seconds if timeout_seconds is not None else 30.0)
    except (TypeError, ValueError):
        raise VerificationBoundaryError("timeout_seconds must be a number")
    return min(max(t, VERIFICATION_TIMEOUT_MIN_SECONDS), VERIFICATION_TIMEOUT_MAX_SECONDS)


def validate_target_files(root: Path, target_files: list[str] | None) -> list[str] | None:
    """校验 target_files 全部是 root 内的相对路径（防绝对路径丢弃左侧与 .. 穿越）。

    返回规范化后的相对路径列表；``None`` 表示"哈希整个工作区"。
    """
    if target_files is None:
        return None
    cleaned: list[str] = []
    for f in target_files:
        raw = str(f or "").strip().replace("\\", "/")
        if not raw or raw in (".", "/"):
            raise VerificationBoundaryError(f"target file {f!r} is empty")
        if "\x00" in raw:
            raise VerificationBoundaryError(f"target file {f!r} contains a NUL byte")
        p = Path(raw)
        if p.is_absolute() or raw.startswith("~"):
            raise VerificationBoundaryError(
                f"target file {f!r} must be a workspace-relative path"
            )
        resolved = (root / p).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise VerificationBoundaryError(
                f"target file {f!r} escapes the workspace root"
            )
        cleaned.append(resolved.relative_to(root).as_posix())
    return cleaned


class TrustedVerificationRunner:
    """Independent Trusted Verification Runner executing tests against real workspace artifacts."""

    @classmethod
    def compute_artifact_digests(
        cls,
        workspace_dir: Path | str,
        target_files: list[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], str, list[dict[str, str]]]:
        """Calculates SHA-256 digests and file metadata for actual artifacts on disk.

        返回 ``(digests, composite_hash, skipped)``：``skipped`` 记录因
        数量/体积上限或读取失败而未哈希的文件（调用方应如实透出，不静默丢）。
        """
        ws_path = Path(workspace_dir).resolve()
        if not ws_path.is_dir():
            raise VerificationBoundaryError(
                f"Workspace directory {workspace_dir} does not exist or is not a directory"
            )
        digests: dict[str, dict[str, Any]] = {}
        skipped: list[dict[str, str]] = []

        if target_files:
            # 调用方必须先经 validate_target_files；这里再做一次 containment 兜底。
            files_to_hash = []
            for f in target_files:
                cand = (ws_path / str(f)).resolve()
                try:
                    cand.relative_to(ws_path)
                except ValueError:
                    raise VerificationBoundaryError(
                        f"target file {f!r} escapes the workspace root"
                    )
                files_to_hash.append(cand)
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
        hashed = 0
        for f in sorted(files_to_hash, key=lambda x: str(x)):
            if hashed >= MAX_HASHED_FILES:
                skipped.append({"path": f.relative_to(ws_path).as_posix(), "reason": "file_count_cap"})
                continue
            if f.exists() and f.is_file():
                rel_path = f.relative_to(ws_path).as_posix()
                try:
                    size_bytes = f.stat().st_size
                except OSError:
                    skipped.append({"path": rel_path, "reason": "stat_failed"})
                    continue
                if size_bytes > MAX_HASH_FILE_BYTES:
                    skipped.append({"path": rel_path, "reason": "file_too_large"})
                    continue
                try:
                    content = f.read_bytes()
                except OSError:
                    skipped.append({"path": rel_path, "reason": "read_failed"})
                    continue
                file_sha = hashlib.sha256(content).hexdigest()
                size_bytes = len(content)
                digests[rel_path] = {
                    "sha256": file_sha,
                    "size_bytes": size_bytes,
                    "path": rel_path,
                }
                hasher.update(f"{rel_path}:{file_sha}".encode("utf-8"))
                hashed += 1

        composite_hash = hasher.hexdigest()
        return digests, composite_hash, skipped

    @classmethod
    def execute_test(
        cls,
        workspace_dir: Path | str,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Executes actual test command in workspace_dir, captures exit code, logs, and artifact digests.

        安全边界（P0 修复）：``workspace_dir`` 必须是调用方已解析并鉴权过的
        工作区 root（路由层用 ``workspace_id`` 查表推导，禁止客户端直传）；
        ``command`` 必须过 :func:`validate_verification_command`（argv 列表 +
        测试运行器白名单，永不 ``shell=True``）。
        """
        ws_path = Path(workspace_dir).resolve()
        if not ws_path.is_dir():
            raise VerificationBoundaryError(
                f"Workspace directory {workspace_dir} does not exist or is not a directory"
            )

        cmd_list = validate_verification_command(command)
        timeout_seconds = clamp_verification_timeout(timeout_seconds)
        target_files = validate_target_files(ws_path, target_files)

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd_list,
                cwd=str(ws_path),
                env=run_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
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
        digests, composite_hash, skipped = cls.compute_artifact_digests(ws_path, target_files)

        verification_id = f"verif-{uuid4().hex[:12]}"
        passed = (exit_code == 0)

        receipt = {
            "verification_id": verification_id,
            "verified_at": utcnow().isoformat(),
            "command": cmd_list,
            "workspace_dir": str(ws_path),
            "exit_code": exit_code,
            "passed": passed,
            "stdout": stdout_log,
            "stderr": stderr_log,
            "duration_ms": duration_ms,
            "artifact_digests": digests,
            "composite_artifact_hash": composite_hash,
            "skipped_files": skipped,
            "status": "PASS" if passed else "FAIL",
        }
        return receipt
