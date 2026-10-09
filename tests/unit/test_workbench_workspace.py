"""Unit tests: 18 §3 项目与文件 / 编辑器 / 实时修改 — WorkspaceService.

These exercise the real filesystem against a temporary authorized root, so the
behaviour under test is the actual behaviour, not a mock of it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from find_yourself.services.workspace import WorkspaceService


@pytest.fixture()
def svc(session, audit) -> WorkspaceService:
    return WorkspaceService(session, audit)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1", csrf_token="")


@pytest.fixture()
def ws(svc: WorkspaceService, owner: Actor, tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    return svc.register_workspace(
        owner, project_name="合成工程项目", authorized_root=str(root), data_domain="work"
    )


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------
def test_register_rejects_relative_and_missing_root(svc: WorkspaceService, owner: Actor, tmp_path: Path):
    with pytest.raises(ValidationFailed):
        svc.register_workspace(owner, project_name="x", authorized_root="relative/path")
    with pytest.raises(ValidationFailed):
        svc.register_workspace(owner, project_name="x", authorized_root=str(tmp_path / "nope"))


def test_register_persists_resolved_root(svc: WorkspaceService, owner: Actor, tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    w = svc.register_workspace(owner, project_name="p", authorized_root=str(root))
    assert Path(w.authorized_root).is_absolute()
    assert w.mode == "local" and w.data_domain == "work"


def test_other_owner_cannot_access(svc: WorkspaceService, owner: Actor, ws):
    stranger = Actor.owner("owner-2")
    with pytest.raises(PermissionDenied):
        svc.get_workspace(stranger, ws.id)


# ----------------------------------------------------------------------
# Path boundary (18 §6 文件边界)
# ----------------------------------------------------------------------
def test_rejects_absolute_drive_and_unc_paths(svc: WorkspaceService, owner: Actor, ws):
    for bad in ("C:/Windows/System32/drivers/etc/hosts", "\\\\server\\share\\x", "/etc/passwd"):
        with pytest.raises(ValidationFailed):
            svc.resolve_path(ws, bad)


def test_rejects_parent_traversal(svc: WorkspaceService, owner: Actor, ws):
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "../../outside.txt")


def test_rejects_junction_escaping_root(svc: WorkspaceService, owner: Actor, ws, tmp_path: Path):
    """18 §6: the boundary must cover directory junctions, not only '..'."""
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    link = Path(ws.authorized_root) / "escape"
    if os.name != "nt":
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted in this environment")
    else:
        # Junctions need no elevation, so this path is deterministic on Windows.
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if proc.returncode != 0:
            pytest.skip(f"junction creation unavailable: {proc.stderr.strip()}")

    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "escape/secret.txt")
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "escape/secret.txt", for_write=True)


def test_rejects_symlink_escaping_root(svc: WorkspaceService, owner: Actor, ws, tmp_path: Path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    link = Path(ws.authorized_root) / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")
    if not (link.is_symlink() or getattr(link, "is_junction", lambda: False)()):
        pytest.skip("this environment silently creates a plain directory instead of a link")
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "escape/secret.txt")


def test_blocks_credential_files(svc: WorkspaceService, owner: Actor, ws):
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, ".env")
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "keys/id_rsa")
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, "certs/server.pem")


def test_blocks_writes_into_git_internals(svc: WorkspaceService, owner: Actor, ws):
    git_dir = Path(ws.authorized_root) / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (git_dir / "post-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    # Readable is fine …
    assert svc.resolve_path(ws, ".git/hooks/post-commit").exists()
    # … but a workspace must never rewrite Git external config or hooks.
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, ".git/hooks/post-commit", for_write=True)
    with pytest.raises(PermissionDenied):
        svc.resolve_path(ws, ".git/config", for_write=True)


# ----------------------------------------------------------------------
# File tree / read / write
# ----------------------------------------------------------------------
def test_list_tree_reports_encoding_size_and_readonly(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "notes.txt").write_text("中文内容\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")

    tree = svc.list_tree(owner, ws.id)
    names = {e["name"] for e in tree["entries"]}
    assert "app.py" in names and "sub" in names
    # Credential files are never surfaced through the workbench API.
    assert ".env" not in names
    assert tree["blocked_credential_entries"] == 1

    app = next(e for e in tree["entries"] if e["name"] == "app.py")
    assert app["type"] == "file" and app["size_bytes"] > 0 and app["encoding"] == "utf-8"
    assert app["read_only"] is False


def test_tree_paging_is_lazy(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    for i in range(10):
        (root / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
    page1 = svc.list_tree(owner, ws.id, offset=0, limit=4)
    assert len(page1["entries"]) == 4 and page1["truncated"] is True and page1["total"] == 10
    page2 = svc.list_tree(owner, ws.id, offset=4, limit=4)
    assert page2["entries"][0]["name"] != page1["entries"][0]["name"]


def test_read_file_reports_revision_and_encoding(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    # Bytes are written verbatim so the round-trip assertion is CRLF-exact.
    (root / "a.py").write_bytes(b"x = 1\n")
    res = svc.read_file(owner, ws.id, "a.py")
    assert res["content"] == "x = 1\n"
    assert res["revision"] == 1
    assert res["sha256"] and res["encoding"] == "utf-8"


def test_binary_file_is_honestly_labelled(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    res = svc.read_file(owner, ws.id, "blob.bin")
    assert res["binary"] is True and res["editable"] is False


# ----------------------------------------------------------------------
# Optimistic concurrency (18 §5 FileRevision)
# ----------------------------------------------------------------------
def test_save_requires_expected_revision(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "m.py").write_text("v1\n", encoding="utf-8")
    svc.read_file(owner, ws.id, "m.py")
    with pytest.raises(ValidationFailed):
        svc.write_file(owner, ws.id, "m.py", "v2\n")


def test_stale_revision_conflicts_instead_of_overwriting(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "m.py").write_text("v1\n", encoding="utf-8")
    first = svc.read_file(owner, ws.id, "m.py")
    svc.write_file(owner, ws.id, "m.py", "v2\n", expected_revision=first["revision"])
    # The agent still holds revision 1; its save must conflict, not silently win.
    with pytest.raises(Conflict):
        svc.write_file(owner, ws.id, "m.py", "agent-v\n", expected_revision=1)
    assert (root / "m.py").read_text(encoding="utf-8") == "v2\n"


def test_external_edit_is_detected_and_bumps_revision(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "m.py").write_text("v1\n", encoding="utf-8")
    first = svc.read_file(owner, ws.id, "m.py")
    # Simulate an agent writing straight to disk, bypassing the API.
    (root / "m.py").write_text("externally-changed\n", encoding="utf-8")
    after = svc.read_file(owner, ws.id, "m.py")
    assert after["revision"] == first["revision"] + 1
    with pytest.raises(Conflict):
        svc.write_file(owner, ws.id, "m.py", "mine\n", expected_revision=first["revision"])
    events = svc.get_events(owner, ws.id)
    assert any(e["event_type"] == "file.external_change_detected" for e in events)


def test_save_records_diff_and_event(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "d.py").write_text("one\n", encoding="utf-8")
    svc.read_file(owner, ws.id, "d.py")
    res = svc.write_file(owner, ws.id, "d.py", "two\n", expected_revision=1)
    assert res["revision"] == 2
    assert res["diff"]["added"] >= 1 and res["diff"]["removed"] >= 1
    events = svc.get_events(owner, ws.id)
    saved = [e for e in events if e["event_type"] == "file.saved"]
    assert saved and saved[-1]["revision"] == 2
    assert saved[-1]["source_actor"] == "owner/owner-1"


# ----------------------------------------------------------------------
# Rename / delete / search
# ----------------------------------------------------------------------
def test_rename_moves_file(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "old.py").write_text("x\n", encoding="utf-8")
    svc.read_file(owner, ws.id, "old.py")
    res = svc.rename_file(owner, ws.id, "old.py", "new.py", expected_revision=1)
    assert res["path"] == "new.py"
    assert not (root / "old.py").exists() and (root / "new.py").exists()


def test_delete_is_recoverable(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "gone.py").write_bytes(b"x\n")
    svc.read_file(owner, ws.id, "gone.py")
    res = svc.delete_file(owner, ws.id, "gone.py", expected_revision=1)
    assert res["recoverable"] is True
    assert not (root / "gone.py").exists()
    # The content is still on disk inside the workspace trash area.
    assert (root / Path(res["trash_ref"])).exists()
    assert (root / Path(res["trash_ref"])).read_bytes() == b"x\n"


def test_search_by_name_and_content(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "handler.py").write_text("def handle():\n    return SECRET_MARKER\n", encoding="utf-8")
    by_name = svc.search(owner, ws.id, "handler")
    assert by_name["count"] == 1 and by_name["hits"][0]["match"] == "name"
    by_content = svc.search(owner, ws.id, "SECRET_MARKER", mode="content")
    assert by_content["count"] == 1
    assert "SECRET_MARKER" in by_content["hits"][0]["preview"]


# ----------------------------------------------------------------------
# Snapshot / reconnect replay
# ----------------------------------------------------------------------
def test_snapshot_and_cursor_replay(svc: WorkspaceService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    (root / "x.py").write_text("a\n", encoding="utf-8")
    svc.read_file(owner, ws.id, "x.py")
    svc.write_file(owner, ws.id, "x.py", "b\n", expected_revision=1)
    snap = svc.get_snapshot(owner, ws.id)
    assert snap["cursor"] >= 1
    # A client that reconnects from cursor 0 must receive the events it missed.
    events = svc.get_events(owner, ws.id, cursor=0)
    assert events and events[0]["seq"] > 0
    later = svc.get_events(owner, ws.id, cursor=snap["cursor"])
    assert later == []


def test_read_missing_file_raises_not_found(svc: WorkspaceService, owner: Actor, ws):
    with pytest.raises(NotFound):
        svc.read_file(owner, ws.id, "nope.py")
