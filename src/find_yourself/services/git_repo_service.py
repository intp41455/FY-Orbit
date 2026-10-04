"""GitRepoService — P1-03 工作区 git init / 远端绑定 / push / 提交历史.

与 18 号工单的 :mod:`find_yourself.services.git_service`（工程代码工作台）互不
重叠：本模块面向"用户工作区"概念，负责把一个普通目录变成 git 仓库并推送到
远端（本地 bare 或网络远端）。

安全边界：
- 所有操作只允许发生在产品工作区根目录（默认 ``.runtime/workspaces``，可用
  ``FY_WORKSPACE_ROOT`` 覆盖）及其子目录内；workspace 名称与相对路径均做
  白名单校验，拒绝目录穿越、批量暂存 token 与参数注入。
- 全部 git 调用走 ``subprocess.run(list)``，**永不使用 shell**；分支名、
  remote 名均按字符白名单校验，URL 拒绝以 ``-`` 开头。
- 每条 git 命令执行后写入结构化日志（logger ``find_yourself.git_repo``），
  字段含时间戳、workspace、命令、退出码、耗时与 stderr 摘要。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from find_yourself.services.errors import Conflict, NotFound, ValidationFailed

logger = logging.getLogger("find_yourself.git_repo")

#: workspace / remote 名称白名单：字母数字开头，仅含安全字符，杜绝 ``..`` 与参数注入
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: 分支名白名单（git check-ref-format 的保守子集）
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

#: 禁止作为"选中文件"的批量暂存 token（git add -A / . 等一律拒绝）
BULK_STAGE_TOKENS = {"-a", "-A", "--all", ".", "*", "-u", "-U", "--update", ":/"}

#: 单条 git 命令超时（秒）
GIT_TIMEOUT = 60.0

DEFAULT_WORKSPACE_ROOT = ".runtime/workspaces"


def default_workspace_root() -> Path:
    """产品工作区根目录：环境变量 ``FY_WORKSPACE_ROOT`` 优先。"""
    return Path(os.environ.get("FY_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT))


class GitRepoService:
    """面向用户工作区的 git 服务（init / stage / commit / remote / push / log）."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else default_workspace_root()
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 路径与参数安全
    # ------------------------------------------------------------------
    def _workspace_dir(self, workspace: str) -> Path:
        if not NAME_RE.match(workspace or ""):
            raise ValidationFailed(f"Invalid workspace name: {workspace!r}")
        ws_dir = (self.root / workspace).resolve()
        if not ws_dir.is_relative_to(self.root):
            raise ValidationFailed("Workspace path escapes the workspace root")
        return ws_dir

    def _require_repo(self, workspace: str) -> Path:
        ws_dir = self._workspace_dir(workspace)
        if not (ws_dir / ".git").exists():
            raise NotFound(f"Workspace is not a git repository: {workspace}")
        return ws_dir

    def _validate_rel_paths(self, ws_dir: Path, paths: list[str]) -> list[str]:
        """显式文件清单校验：拒绝批量 token、通配符、目录穿越。"""
        if not paths:
            raise ValidationFailed(
                "Explicit file selection is required; bulk staging (git add -A / .) is forbidden"
            )
        cleaned: list[str] = []
        for raw in paths:
            token = str(raw).strip()
            if not token:
                raise ValidationFailed("Empty path in file selection")
            if token.lower() in BULK_STAGE_TOKENS or "*" in token or ".." in token:
                raise ValidationFailed(
                    f"Forbidden path token {raw!r}; select explicit files inside the workspace"
                )
            resolved = (ws_dir / token).resolve()
            if not resolved.is_relative_to(ws_dir):
                raise ValidationFailed(f"Path escapes the workspace boundary: {raw!r}")
            cleaned.append(resolved.relative_to(ws_dir).as_posix())
        return cleaned

    @staticmethod
    def _validate_remote_name(remote: str) -> str:
        if not NAME_RE.match(remote or ""):
            raise ValidationFailed(f"Invalid remote name: {remote!r}")
        return remote

    @staticmethod
    def _validate_url(url: str) -> str:
        url = str(url or "").strip()
        if not url or url.startswith("-") or " " in url:
            raise ValidationFailed("Invalid remote url")
        lowered = url.lower()
        if "://" in lowered:
            scheme = lowered.split("://", 1)[0]
            if scheme not in {"file", "https", "http", "ssh", "git"}:
                raise ValidationFailed(f"Unsupported remote url scheme: {scheme}")
        elif ":" in url.split("/", 1)[0]:
            # 无 // 的伪协议（如 javascript:...）一律拒绝；仅放行 Windows 盘符（C:）
            prefix = url.split(":", 1)[0]
            if not re.fullmatch(r"[A-Za-z]", prefix):
                raise ValidationFailed(f"Unsupported remote url scheme: {prefix}")
        return url

    @staticmethod
    def _validate_branch(branch: str) -> str:
        if not BRANCH_RE.match(branch or "") or ".." in branch or branch.endswith("/") \
                or branch.endswith(".lock"):
            raise ValidationFailed(f"Invalid branch name: {branch!r}")
        return branch

    # ------------------------------------------------------------------
    # git 命令执行 + 结构化日志
    # ------------------------------------------------------------------
    def _run(self, workspace: str, cwd: Path, args: list[str]) -> subprocess.CompletedProcess:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(cwd), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired:
            self._log_command(workspace, args, -1, (time.monotonic() - started) * 1000.0, "timeout")
            raise ValidationFailed(f"git command timed out: git {' '.join(args[:3])}")
        self._log_command(workspace, args, proc.returncode, (time.monotonic() - started) * 1000.0,
                          proc.stderr.strip()[:500])
        return proc

    @staticmethod
    def _log_command(workspace: str, args: list[str], returncode: int,
                     duration_ms: float, stderr: str) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "workspace": workspace,
            "command": "git " + " ".join(args),
            "returncode": returncode,
            "duration_ms": round(duration_ms, 2),
            "stderr": stderr,
        }
        logger.info("git command %s", json.dumps(record, ensure_ascii=False))

    @staticmethod
    def _require_ok(workspace: str, proc: subprocess.CompletedProcess, what: str) -> None:
        if proc.returncode != 0:
            raise ValidationFailed(f"git {what} failed: {proc.stderr.strip()}")

    # ------------------------------------------------------------------
    # 初始化 / 远端绑定 / push
    # ------------------------------------------------------------------
    def init_workspace(self, workspace: str, default_branch: str = "main") -> dict[str, Any]:
        """把 workspace 目录变成 git 仓库（幂等拒绝：已初始化则冲突）."""
        branch = self._validate_branch(default_branch)
        ws_dir = self._workspace_dir(workspace)
        ws_dir.mkdir(parents=True, exist_ok=True)
        if (ws_dir / ".git").exists():
            raise Conflict(f"Workspace already initialized: {workspace}")
        proc = self._run(workspace, ws_dir, ["init", "-b", branch])
        self._require_ok(workspace, proc, "init")
        # 仓库级身份，避免宿主机缺少全局 git 配置导致 commit 失败
        self._run(workspace, ws_dir, ["config", "user.name", "Find Yourself"])
        self._run(workspace, ws_dir, ["config", "user.email", "noreply@find-yourself.local"])
        return {"workspace": workspace, "path": str(ws_dir), "default_branch": branch,
                "initialized": True}

    def add_remote(self, workspace: str, remote: str, url: str) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        remote = self._validate_remote_name(remote)
        url = self._validate_url(url)
        existing = self._run(workspace, ws_dir, ["remote", "get-url", remote])
        if existing.returncode == 0:
            raise Conflict(f"Remote already exists: {remote}")
        proc = self._run(workspace, ws_dir, ["remote", "add", remote, url])
        self._require_ok(workspace, proc, "remote add")
        return {"workspace": workspace, "remote": remote, "url": url, "bound": True}

    def remove_remote(self, workspace: str, remote: str) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        remote = self._validate_remote_name(remote)
        proc = self._run(workspace, ws_dir, ["remote", "remove", remote])
        self._require_ok(workspace, proc, "remote remove")
        return {"workspace": workspace, "remote": remote, "removed": True}

    def list_remotes(self, workspace: str) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        proc = self._run(workspace, ws_dir, ["remote", "-v"])
        self._require_ok(workspace, proc, "remote -v")
        remotes: dict[str, dict[str, str]] = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                entry = remotes.setdefault(parts[0], {"name": parts[0], "fetch": "", "push": ""})
                if parts[2] == "(fetch)":
                    entry["fetch"] = parts[1]
                elif parts[2] == "(push)":
                    entry["push"] = parts[1]
        return {"workspace": workspace, "remotes": list(remotes.values())}

    def push(self, workspace: str, remote: str, branch: str,
             set_upstream: bool = False) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        remote = self._validate_remote_name(remote)
        branch = self._validate_branch(branch)
        head = self._run(workspace, ws_dir, ["rev-parse", "--verify", "HEAD"])
        if head.returncode != 0:
            raise Conflict("Nothing to push: repository has no commits yet")
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.extend([remote, branch])
        proc = self._run(workspace, ws_dir, args)
        self._require_ok(workspace, proc, "push")
        return {"workspace": workspace, "remote": remote, "branch": branch,
                "pushed": True, "output": proc.stdout.strip()[:2000]}

    # ------------------------------------------------------------------
    # 暂存 / 提交 / 状态
    # ------------------------------------------------------------------
    def stage(self, workspace: str, paths: list[str]) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        rels = self._validate_rel_paths(ws_dir, paths)
        proc = self._run(workspace, ws_dir, ["add", "--", *rels])
        self._require_ok(workspace, proc, "add")
        return {"workspace": workspace, "staged": rels, "count": len(rels)}

    def commit(self, workspace: str, message: str, paths: list[str]) -> dict[str, Any]:
        """只提交显式选中的文件；无改动或空 message 拒绝."""
        ws_dir = self._require_repo(workspace)
        if not str(message or "").strip():
            raise ValidationFailed("Commit message is required")
        rels = self._validate_rel_paths(ws_dir, paths)
        staged = self._run(workspace, ws_dir, ["add", "--", *rels])
        self._require_ok(workspace, staged, "add")
        pending = self._run(workspace, ws_dir, ["diff", "--cached", "--name-only"])
        pending_set = {line.strip() for line in pending.stdout.splitlines() if line.strip()}
        missing = [rel for rel in rels if rel not in pending_set]
        if missing:
            raise Conflict(f"No staged change for requested path(s): {missing}")
        proc = self._run(workspace, ws_dir, ["commit", "-m", str(message), "--", *rels])
        self._require_ok(workspace, proc, "commit")
        sha = self._run(workspace, ws_dir, ["rev-parse", "HEAD"]).stdout.strip()
        return {"workspace": workspace, "commit": sha, "message": message,
                "paths": rels, "count": len(rels)}

    def status(self, workspace: str) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        proc = self._run(workspace, ws_dir, ["status", "--porcelain=v1", "-uall", "-b"])
        self._require_ok(workspace, proc, "status")
        branch = ""
        staged: list[dict[str, str]] = []
        unstaged: list[dict[str, str]] = []
        untracked: list[dict[str, str]] = []
        for line in proc.stdout.splitlines():
            if line.startswith("## "):
                branch = line[3:].split("...", 1)[0].split("[", 1)[0].strip()
                continue
            if len(line) < 4:
                continue
            x, y, path = line[0], line[1], line[3:]
            if x == "?" and y == "?":
                untracked.append({"path": path, "state": "??"})
                continue
            if x not in (" ", "?"):
                staged.append({"path": path, "state": x})
            if y not in (" ", "?"):
                unstaged.append({"path": path, "state": y})
        return {"workspace": workspace, "branch": branch, "staged": staged,
                "unstaged": unstaged, "untracked": untracked,
                "clean": not (staged or unstaged or untracked)}

    # ------------------------------------------------------------------
    # 提交历史（供 P1-12 git 提交树图消费）
    # ------------------------------------------------------------------
    def commit_history(self, workspace: str, limit: int = 50,
                       branch: str | None = None) -> dict[str, Any]:
        ws_dir = self._require_repo(workspace)
        limit = max(1, min(int(limit or 50), 500))
        args = ["log", f"-n{limit}",
                "--pretty=format:%H%x1f%P%x1f%an%x1f%aI%x1f%s%x1e"]
        if branch:
            args.append(self._validate_branch(branch))
        proc = self._run(workspace, ws_dir, args)
        self._require_ok(workspace, proc, "log")
        commits: list[dict[str, Any]] = []
        for record in proc.stdout.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            fields = record.split("\x1f")
            if len(fields) < 5:
                continue
            sha, parents, author, date, message = fields[0], fields[1], fields[2], fields[3], fields[4]
            commits.append({
                "sha": sha,
                "parents": [p for p in parents.split() if p],
                "author": author,
                "date": date,
                "message": message,
            })
        return {"workspace": workspace, "count": len(commits), "commits": commits}

    # ------------------------------------------------------------------
    # diff（供 P1-11 diff 可视化消费）
    # ------------------------------------------------------------------
    #: `to` 的哨兵值：与工作区（未提交改动）比较
    WORKTREE_REF = "worktree"
    #: ref 参数白名单：sha / 分支名形态，拒绝以 ``-`` 开头的参数注入
    REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

    #: diff 原文上限（字符），超出截断并置 truncated 标记
    MAX_DIFF_TEXT = 2_000_000

    def diff(self, workspace: str, from_ref: str, to_ref: str | None = None,
             context: int = 3) -> dict[str, Any]:
        """两个 commit（或 commit vs 工作区）的 unified diff 原文.

        ``to_ref`` 为 ``None`` 或哨兵值 ``worktree`` 时表示与工作区比较
        （``git diff <from>``）；否则 ``git diff <from> <to>``。参数一律走
        ``subprocess.run(list)``，ref 按白名单校验且命令尾部追加 ``--``
        终结选项，杜绝参数注入。
        """
        ws_dir = self._require_repo(workspace)
        from_ref = self._validate_ref(from_ref)
        context = max(0, min(int(context or 3), 50))
        worktree = to_ref is None or str(to_ref).strip().lower() == self.WORKTREE_REF
        to_ref = None if worktree else self._validate_ref(to_ref or "")
        args = ["diff", "--no-color", f"-U{context}"]
        if worktree:
            args.append(from_ref)
        else:
            args.extend([from_ref, to_ref])
        args.append("--")
        proc = self._run(workspace, ws_dir, args)
        self._require_ok(workspace, proc, "diff")
        text = proc.stdout
        truncated = False
        if len(text) > self.MAX_DIFF_TEXT:
            text = text[: self.MAX_DIFF_TEXT]
            truncated = True
        return {
            "workspace": workspace,
            "from": from_ref,
            "to": self.WORKTREE_REF if worktree else to_ref,
            "context": context,
            "text": text,
            "truncated": truncated,
        }

    def _validate_ref(self, ref: str) -> str:
        ref = str(ref or "").strip()
        if not self.REF_RE.match(ref) or ".." in ref or ref.endswith(".lock"):
            raise ValidationFailed(f"Invalid git ref: {ref!r}")
        return ref
