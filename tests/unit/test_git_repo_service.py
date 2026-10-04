"""P1-03 GitRepoService 单元测试：init/stage/commit/remote/push/log + 安全边界."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from find_yourself.services.errors import Conflict, NotFound, ValidationFailed  # noqa: E402
from find_yourself.services.git_repo_service import GitRepoService  # noqa: E402


@pytest.fixture()
def svc(tmp_path: Path) -> GitRepoService:
    return GitRepoService(root=tmp_path / "workspaces")


def _bare_remote(tmp_path: Path, name: str = "remote.git") -> str:
    remote = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    return str(remote)


def test_init_commit_push_full_chain(svc: GitRepoService, tmp_path: Path):
    svc.init_workspace("demo")
    (svc.root / "demo" / "hello.txt").write_text("hello find-yourself\n", encoding="utf-8")

    staged = svc.stage("demo", ["hello.txt"])
    assert staged["count"] == 1

    committed = svc.commit("demo", "init: first commit", ["hello.txt"])
    assert len(committed["commit"]) == 40

    remote_url = _bare_remote(tmp_path)
    svc.add_remote("demo", "origin", remote_url)
    pushed = svc.push("demo", "origin", "main")
    assert pushed["pushed"] is True

    # bare 远端真实收到提交
    head = subprocess.run(["git", "--git-dir", remote_url, "rev-parse", "main"],
                          capture_output=True, text=True, check=True)
    assert head.stdout.strip() == committed["commit"]


def test_commit_history_returns_nodes_with_parents(svc: GitRepoService):
    svc.init_workspace("history")
    ws_dir = svc.root / "history"
    for i in range(3):
        (ws_dir / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        svc.stage("history", [f"f{i}.txt"])
        svc.commit("history", f"commit {i}", [f"f{i}.txt"])
    history = svc.commit_history("history", limit=10)
    assert history["count"] == 3
    shas = [c["sha"] for c in history["commits"]]
    assert len(set(shas)) == 3
    newest = history["commits"][0]
    assert newest["message"] == "commit 2"
    # 树图需要的 parents 字段
    assert newest["parents"] == [history["commits"][1]["sha"]]
    second = history["commits"][1]
    assert second["parents"] == [history["commits"][2]["sha"]]
    root = history["commits"][2]
    assert root["parents"] == []


def test_bulk_staging_forbidden(svc: GitRepoService):
    svc.init_workspace("bulk")
    (svc.root / "bulk" / "a.txt").write_text("x", encoding="utf-8")
    for bad in (["-A"], ["--all"], ["."], ["a.txt", "-u"], ["*"]):
        with pytest.raises(ValidationFailed):
            svc.stage("bulk", bad)
    with pytest.raises(ValidationFailed):
        svc.stage("bulk", [])


def test_directory_traversal_rejected(svc: GitRepoService):
    svc.init_workspace("safe")
    with pytest.raises(ValidationFailed):
        svc.stage("safe", ["../outside.txt"])
    with pytest.raises(ValidationFailed):
        svc.stage("safe", ["..\\outside.txt"])
    with pytest.raises(ValidationFailed):
        svc._workspace_dir("../escape")


def test_workspace_name_whitelist(svc: GitRepoService):
    for bad in ("a/b", "..", "-flag", "a b", ""):
        with pytest.raises(ValidationFailed):
            svc.init_workspace(bad)


def test_remote_name_and_url_validation(svc: GitRepoService, tmp_path: Path):
    svc.init_workspace("net")
    with pytest.raises(ValidationFailed):
        svc.add_remote("net", "-oProxyCommand=calc", "https://example.com/r.git")
    with pytest.raises(ValidationFailed):
        svc.add_remote("net", "origin", "javascript:alert(1)")
    with pytest.raises(ValidationFailed):
        svc.add_remote("net", "origin", "-u=https://evil")


def test_duplicate_init_conflict(svc: GitRepoService):
    svc.init_workspace("once")
    with pytest.raises(Conflict):
        svc.init_workspace("once")


def test_operations_require_repo(svc: GitRepoService):
    with pytest.raises(NotFound):
        svc.status("missing")
    svc.init_workspace("no-repo")  # 目录存在但未 init 时 init_workspace 幂等拒绝
    with pytest.raises(Conflict):
        svc.init_workspace("no-repo")


def test_commit_refuses_unchanged_paths(svc: GitRepoService):
    svc.init_workspace("unchanged")
    (svc.root / "unchanged" / "f.txt").write_text("1", encoding="utf-8")
    svc.stage("unchanged", ["f.txt"])
    svc.commit("unchanged", "first", ["f.txt"])
    # 无新改动再次提交同样文件 → 冲突
    with pytest.raises(Conflict):
        svc.commit("unchanged", "second", ["f.txt"])


def test_push_requires_commit(svc: GitRepoService, tmp_path: Path):
    svc.init_workspace("empty")
    svc.add_remote("empty", "origin", _bare_remote(tmp_path, "empty.git"))
    with pytest.raises(Conflict):
        svc.push("empty", "origin", "main")
