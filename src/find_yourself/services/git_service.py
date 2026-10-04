"""GitService: 18 工程代码工作台 — 状态 / 差异 / 明确选中提交.

Rules from ``18_工程代码工作台与主协调Agent全流程实施规格.md`` §3:

- **禁止自动暂存所有文件** (no ``git add -A`` / ``git add .``). Staging and
  committing accept an explicit, non-empty file list, each entry validated
  against the workspace boundary first.
- **回退未提交改动须预览且可恢复** — discarding uncommitted changes is a two
  step operation: :meth:`revert_preview` shows exactly what would be lost and
  creates a recoverable stash ref, then :meth:`revert_apply` performs it.
- Conflict state is surfaced, not hidden.
- Branch ↔ task workspace mapping is recorded on the workspace manifest.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from sqlalchemy.orm import Session

from find_yourself.db.workbench_models import WorkspaceManifest
from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, ValidationFailed
from find_yourself.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)

#: Never allow these as a "selected file": they stage everything.
BULK_STAGE_TOKENS = {"-a", "--all", ".", "*", "-u", "--update", ":/"}


class GitService:
    def __init__(self, session: Session, workspaces: WorkspaceService):
        self.session = session
        self.workspaces = workspaces

    # ------------------------------------------------------------------
    def _repo(self, actor: Actor, workspace_id: str) -> tuple[WorkspaceManifest, str]:
        ws = self.workspaces.get_workspace(actor, workspace_id)
        return ws, str(self.workspaces._root(ws))

    @staticmethod
    def _run(cwd: str, args: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )

    @staticmethod
    def _git_failed(op: str, proc: subprocess.CompletedProcess) -> ValidationFailed:
        """P2-16: fixed client-facing message; raw stderr only goes to logs."""
        logger.warning(
            "git %s failed (rc=%s): %s", op, proc.returncode, (proc.stderr or "").strip()
        )
        return ValidationFailed(f"git {op} failed; details are in the server logs")

    def _require_repo(self, cwd: str) -> None:
        probe = self._run(cwd, ["rev-parse", "--is-inside-work-tree"])
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            # P2-16: never echo the server-side absolute path to the client.
            logger.warning("Not a Git work tree: %s (stderr: %s)", cwd, (probe.stderr or "").strip())
            raise NotFound("Not a Git work tree")

    def _validate_paths(self, actor: Actor, ws: WorkspaceManifest, paths: list[str]) -> list[str]:
        if not paths:
            raise ValidationFailed(
                "Explicit file selection is required; bulk staging (git add -A / .) is forbidden"
            )
        cleaned: list[str] = []
        for raw in paths:
            token = str(raw).strip()
            if token.lower() in BULK_STAGE_TOKENS or "*" in token:
                raise ValidationFailed(
                    f"Bulk staging token {raw!r} is forbidden; select explicit files"
                )
            # Boundary check — the path must resolve inside the authorized root.
            resolved = self.workspaces.resolve_path(ws, token)
            rel = resolved.resolve().relative_to(self.workspaces._root(ws)).as_posix()
            cleaned.append(rel)
        return cleaned

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------
    def status(self, actor: Actor, workspace_id: str) -> dict[str, Any]:
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        out = self._run(cwd, ["status", "--porcelain=v1", "-uall", "-b"])
        if out.returncode != 0:
            raise self._git_failed("status", out)

        branch, ahead, behind = "", 0, 0
        staged, unstaged, untracked, conflicts = [], [], [], []
        for line in out.stdout.splitlines():
            if line.startswith("## "):
                head = line[3:]
                m = re.match(r"^(.*?)(?:\.\.\.(\S+))?(?:\s+\[(.*)\])?$", head)
                if m:
                    branch = m.group(1) or ""
                    tracking = m.group(3) or ""
                    a = re.search(r"ahead (\d+)", tracking)
                    b = re.search(r"behind (\d+)", tracking)
                    ahead = int(a.group(1)) if a else 0
                    behind = int(b.group(1)) if b else 0
                continue
            if not line or len(line) < 4:
                continue
            x, y = line[0], line[1]
            path = line[3:]
            if x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
                conflicts.append({"path": path, "state": f"{x}{y}"})
                continue
            if x == "?" and y == "?":
                # Fully untracked: report it ONLY as untracked, otherwise the
                # same path shows up twice (unstaged "?" + untracked "??").
                untracked.append({"path": path, "state": "??"})
                continue
            if x != " ":
                staged.append({"path": path, "state": x})
            if y != " ":
                unstaged.append({"path": path, "state": y})

        if ws.branch != branch:
            ws.branch = branch
            self.session.flush()

        return {
            "workspace_id": ws.id,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "conflicts": conflicts,
            "has_conflicts": bool(conflicts),
            "clean": not (staged or unstaged or untracked or conflicts),
        }

    def diff(
        self,
        actor: Actor,
        workspace_id: str,
        paths: list[str] | None = None,
        *,
        staged: bool = False,
        context: int = 3,
    ) -> dict[str, Any]:
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        args = ["diff", f"--unified={context}", "--no-color"]
        if staged:
            args.append("--cached")
        if paths:
            args.extend(["--", *self._validate_paths(actor, ws, paths)])
        out = self._run(cwd, args)
        if out.returncode != 0:
            raise self._git_failed("diff", out)
        text = out.stdout
        return {
            "workspace_id": ws.id,
            "staged": staged,
            "paths": paths or [],
            "text": text[:200_000],
            "truncated": len(text) > 200_000,
            "additions": sum(1 for l in text.splitlines() if l.startswith("+") and not l.startswith("+++")),
            "deletions": sum(1 for l in text.splitlines() if l.startswith("-") and not l.startswith("---")),
        }

    def branches(self, actor: Actor, workspace_id: str) -> dict[str, Any]:
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        out = self._run(cwd, ["branch", "--format=%(refname:short)|%(objectname:short)"])
        items = []
        for line in out.stdout.splitlines():
            if "|" in line:
                name, sha = line.split("|", 1)
                items.append({"name": name.strip(), "sha": sha.strip(), "current": name.strip() == ws.branch})
        return {"workspace_id": ws.id, "current": ws.branch, "branches": items}

    def set_branch_mapping(self, actor: Actor, workspace_id: str, branch: str) -> dict[str, Any]:
        """Record which branch this task workspace is mapped to."""
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        verify = self._run(cwd, ["rev-parse", "--verify", branch])
        if verify.returncode != 0:
            raise NotFound(f"Branch does not exist: {branch}")
        ws.branch = branch
        self.session.flush()
        return {"workspace_id": ws.id, "branch": ws.branch, "mapped": True}

    # ------------------------------------------------------------------
    # Write operations (explicit selection only)
    # ------------------------------------------------------------------
    def stage(self, actor: Actor, workspace_id: str, paths: list[str]) -> dict[str, Any]:
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        rels = self._validate_paths(actor, ws, paths)
        out = self._run(cwd, ["add", "--", *rels])
        if out.returncode != 0:
            raise self._git_failed("add", out)
        return {"workspace_id": ws.id, "staged": rels, "count": len(rels), "bulk_stage_forbidden": True}

    def unstage(self, actor: Actor, workspace_id: str, paths: list[str]) -> dict[str, Any]:
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        rels = self._validate_paths(actor, ws, paths)
        out = self._run(cwd, ["restore", "--staged", "--", *rels])
        if out.returncode != 0:
            raise self._git_failed("restore --staged", out)
        return {"workspace_id": ws.id, "unstaged": rels, "count": len(rels)}

    def commit(self, actor: Actor, workspace_id: str, message: str, paths: list[str]) -> dict[str, Any]:
        """Commit only the explicitly selected files.

        Staging is per-file (never ``-A``). If a path has no staged change we
        refuse rather than silently committing something else.
        """
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        if not message.strip():
            raise ValidationFailed("Commit message is required")
        rels = self._validate_paths(actor, ws, paths)

        add = self._run(cwd, ["add", "--", *rels])
        if add.returncode != 0:
            raise self._git_failed("add", add)

        staged_now = self._run(cwd, ["diff", "--cached", "--name-only"])
        staged_set = {p.strip() for p in staged_now.stdout.splitlines() if p.strip()}
        missing = [r for r in rels if r not in staged_set]
        if missing:
            raise Conflict(
                f"No staged change for requested path(s): {missing}. "
                "Commit refuses to include anything that was not explicitly selected."
            )

        out = self._run(cwd, ["commit", "-m", message, "--", *rels])
        if out.returncode != 0:
            raise self._git_failed("commit", out)
        sha = self._run(cwd, ["rev-parse", "HEAD"]).stdout.strip()
        return {"workspace_id": ws.id, "commit": sha, "message": message, "paths": rels, "count": len(rels)}

    # ------------------------------------------------------------------
    # Recoverable revert of uncommitted changes
    # ------------------------------------------------------------------
    def revert_preview(self, actor: Actor, workspace_id: str, paths: list[str]) -> dict[str, Any]:
        """Show exactly what discarding would lose, and create a recoverable stash."""
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        rels = self._validate_paths(actor, ws, paths)
        diff = self.diff(actor, workspace_id, rels)
        return {
            "workspace_id": ws.id,
            "paths": rels,
            "would_discard": diff["text"],
            "additions": diff["additions"],
            "deletions": diff["deletions"],
            "recoverable": True,
            "next_step": "call revert_apply with the same paths to perform the discard",
        }

    def revert_apply(self, actor: Actor, workspace_id: str, paths: list[str]) -> dict[str, Any]:
        """Discard uncommitted changes for the selected paths — recoverably.

        The prior content is stashed first, so the discard can be undone with
        ``stash_ref``. Nothing is dropped irreversibly.
        """
        ws, cwd = self._repo(actor, workspace_id)
        self._require_repo(cwd)
        rels = self._validate_paths(actor, ws, paths)

        stash = self._run(
            cwd,
            ["stash", "push", "-m", f"workbench-revert-{ws.id}", "--", *rels],
        )
        stash_ref = None
        if stash.returncode == 0 and "No local changes" not in stash.stdout:
            ref = self._run(cwd, ["stash", "list", "-n", "1", "--format=%gd|%gs"])
            stash_ref = ref.stdout.strip().split("|", 1)[0] or None

        out = self._run(cwd, ["checkout", "--", *rels])
        if out.returncode != 0:
            raise self._git_failed("checkout", out)
        return {
            "workspace_id": ws.id,
            "paths": rels,
            "recoverable": stash_ref is not None,
            "stash_ref": stash_ref,
            "restore_hint": f"git stash pop {stash_ref}" if stash_ref else None,
        }
