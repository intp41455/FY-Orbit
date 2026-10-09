"""P1-11 diff 可视化 — GitRepoService.diff 与子进程 ``git diff`` 输出一致性.

验收口径：对两次 commit 生成 diff，与 ``git diff`` 原始输出对照一致。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.git_repo_service import GitRepoService


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", check=True,
    )
    return proc.stdout


@pytest.fixture()
def two_commit_repo(tmp_path: Path) -> tuple[GitRepoService, str, Path, str, str]:
    """真实 git 仓库：init → commit1 → commit2（改/增/删），返回 (svc, ws, ws_dir, sha1, sha2)."""
    root = tmp_path / "workspaces"
    svc = GitRepoService(root)
    ws = "diff-consistency"
    svc.init_workspace(ws)
    ws_dir = root / ws

    (ws_dir / "notes.txt").write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    (ws_dir / "docs").mkdir()
    (ws_dir / "docs" / "guide.md").write_text("# 指南\n\n第一段。\n", encoding="utf-8")
    svc.stage(ws, ["notes.txt", "docs/guide.md"])
    sha1 = svc.commit(ws, "commit 1: 初稿", ["notes.txt", "docs/guide.md"])["commit"]

    (ws_dir / "notes.txt").write_text("alpha\nbravo-changed\ncharlie\ndelta\n", encoding="utf-8")
    (ws_dir / "docs" / "guide.md").write_text("# 指南\n\n第一段改写。\n第二段新增。\n", encoding="utf-8")
    (ws_dir / "extra.log").write_text("新增文件第一行\n", encoding="utf-8")
    svc.stage(ws, ["notes.txt", "docs/guide.md", "extra.log"])
    sha2 = svc.commit(ws, "commit 2: 修改/新增", ["notes.txt", "docs/guide.md", "extra.log"])["commit"]
    return svc, ws, ws_dir, sha1, sha2


def test_commit_pair_diff_matches_subprocess_git_diff(two_commit_repo):
    svc, ws, ws_dir, sha1, sha2 = two_commit_repo
    result = svc.diff(ws, sha1, sha2)
    expected = _git(ws_dir, "diff", "--no-color", "-U3", sha1, sha2, "--")
    assert result["text"] == expected
    assert result["from"] == sha1
    assert result["to"] == sha2
    assert result["truncated"] is False
    assert "@@ -1,3 +1,4 @@" in result["text"]
    assert "diff --git a/notes.txt b/notes.txt" in result["text"]


def test_commit_vs_worktree_diff_matches_subprocess_git_diff(two_commit_repo):
    svc, ws, ws_dir, sha1, _sha2 = two_commit_repo
    (ws_dir / "notes.txt").write_text("alpha\n工作区未提交改动\ncharlie\n", encoding="utf-8")
    result = svc.diff(ws, sha1, None)
    expected = _git(ws_dir, "diff", "--no-color", "-U3", sha1, "--")
    assert result["text"] == expected
    assert result["to"] == "worktree"


def test_worktree_sentinel_and_invalid_ref(two_commit_repo):
    svc, ws, _ws_dir, sha1, sha2 = two_commit_repo
    assert svc.diff(ws, sha1, "worktree")["to"] == "worktree"
    with pytest.raises(ValidationFailed):
        svc.diff(ws, "-evil")
    with pytest.raises(ValidationFailed):
        svc.diff(ws, sha1, "main..secret")
    with pytest.raises(ValidationFailed):
        svc.diff(ws, "deadbeef" * 5)


def test_diff_adds_and_deletions_present(two_commit_repo):
    svc, ws, _ws_dir, sha1, sha2 = two_commit_repo
    text = svc.diff(ws, sha1, sha2)["text"]
    assert "+delta" in text
    assert "-bravo" in text and "+bravo-changed" in text
    assert "+++ b/extra.log" in text
