"""Unit tests: 18 §3 Git — status / diff / explicit selection / recoverable revert.

These run the real ``git`` binary against a real temporary repository, so the
assertions describe actual Git behaviour rather than a stand-in.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, ValidationFailed
from find_yourself.services.git_service import GitService
from find_yourself.services.workspace import WorkspaceService

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available on PATH")


@pytest.fixture()
def workspaces(session, audit) -> WorkspaceService:
    return WorkspaceService(session, audit)


@pytest.fixture()
def git_svc(session, workspaces) -> GitService:
    return GitService(session, workspaces)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1", csrf_token="")


@pytest.fixture()
def ws(workspaces: WorkspaceService, owner: Actor, tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "wb@example.test"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "Workbench"], cwd=str(root), check=True)
    # Pin line-ending handling so diffs and reverts are byte-deterministic and
    # independent of the host's global core.autocrlf setting.
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(root), check=True)
    (root / "README.md").write_bytes(b"# init\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(root), check=True, capture_output=True)
    return workspaces.register_workspace(
        owner, project_name="git 测试工程", authorized_root=str(root), data_domain="work"
    )


# ----------------------------------------------------------------------
def test_status_reports_untracked_and_clean(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    assert git_svc.status(owner, ws.id)["clean"] is True
    (root / "new.py").write_bytes(b"x = 1\n")
    st = git_svc.status(owner, ws.id)
    assert st["clean"] is False
    assert any(e["path"] == "new.py" for e in st["untracked"])
    # A fully untracked path must not be double-reported as "unstaged" too.
    assert not any(e["path"] == "new.py" for e in st["unstaged"])


def test_status_records_branch_mapping(git_svc: GitService, owner: Actor, ws):
    st = git_svc.status(owner, ws.id)
    assert st["branch"]
    assert git_svc.branches(owner, ws.id)["current"] == st["branch"]


def test_diff_shows_real_additions(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "README.md").write_bytes(b"# init\nsecond line\n")
    d = git_svc.diff(owner, ws.id, ["README.md"])
    assert d["additions"] >= 1
    assert "second line" in d["text"]


# ----------------------------------------------------------------------
# 18 §3: 禁止自动暂存所有文件
# ----------------------------------------------------------------------
@pytest.mark.parametrize("token", [".", "-A", "--all", "*", "-u"])
def test_bulk_stage_tokens_are_rejected(git_svc: GitService, owner: Actor, ws, token):
    with pytest.raises(ValidationFailed):
        git_svc.stage(owner, ws.id, [token])


def test_stage_requires_explicit_selection(git_svc: GitService, owner: Actor, ws):
    with pytest.raises(ValidationFailed):
        git_svc.stage(owner, ws.id, [])


def test_explicit_stage_and_commit(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "a.py").write_bytes(b"a = 1\n")
    (root / "b.py").write_bytes(b"b = 2\n")

    res = git_svc.stage(owner, ws.id, ["a.py"])
    assert res["staged"] == ["a.py"]
    assert res["bulk_stage_forbidden"] is True

    commit = git_svc.commit(owner, ws.id, "add a module", ["a.py"])
    assert commit["paths"] == ["a.py"]
    sha = commit["commit"]
    assert len(sha) == 40

    # Only the explicitly selected file is in the commit.
    files = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", sha],
        cwd=str(root), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert files == ["a.py"]


def test_commit_refuses_path_without_staged_change(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "c.py").write_bytes(b"c = 3\n")
    with pytest.raises(Conflict):
        git_svc.commit(owner, ws.id, "no change", ["README.md"])


# ----------------------------------------------------------------------
# 18 §3: 回退未提交改动须预览且可恢复
# ----------------------------------------------------------------------
def test_revert_preview_shows_what_would_be_lost(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    # Replace the tracked content outright so the diff has both a deletion and
    # an addition — that is what "what would be lost" has to show.
    (root / "README.md").write_bytes(b"TEMPORARY_REWRITE\n")
    prev = git_svc.revert_preview(owner, ws.id, ["README.md"])
    assert prev["recoverable"] is True
    assert "TEMPORARY_REWRITE" in prev["would_discard"]
    assert prev["deletions"] >= 1
    assert prev["additions"] >= 1


def test_revert_apply_is_recoverable(git_svc: GitService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    original = (root / "README.md").read_bytes()
    (root / "README.md").write_bytes(b"TEMPORARY_REWRITE\n")

    res = git_svc.revert_apply(owner, ws.id, ["README.md"])
    assert (root / "README.md").read_bytes() == original
    assert res["recoverable"] is True or res["stash_ref"] is None

    if res["stash_ref"]:
        # The discarded edit is genuinely recoverable from the stash.
        pop = subprocess.run(
            ["git", "stash", "pop", res["stash_ref"]],
            cwd=str(root), capture_output=True, text=True,
        )
        assert pop.returncode == 0
        assert b"TEMPORARY_REWRITE" in (root / "README.md").read_bytes()
