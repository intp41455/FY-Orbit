"""Unit tests: 18 §3 预览 — real process, health probe, port mapping, isolation.

The preview really starts an HTTP server on loopback, is really probed over a
socket, and on stop the process tree is really terminated and the port really
released.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

import pytest

from find_yourself.services.actor import Actor
from find_yourself.services.preview import PreviewService, port_open
from find_yourself.services.workspace import WorkspaceService


@pytest.fixture()
def workspaces(session, audit) -> WorkspaceService:
    return WorkspaceService(session, audit)


@pytest.fixture()
def previews(session, workspaces) -> PreviewService:
    return PreviewService(session, workspaces)


@pytest.fixture()
def owner() -> Actor:
    return Actor.owner("owner-1", csrf_token="")


@pytest.fixture()
def ws(workspaces: WorkspaceService, owner: Actor, tmp_path: Path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_bytes(b"<html><body>PREVIEW_OK</body></html>")
    return workspaces.register_workspace(
        owner, project_name="预览测试工程", authorized_root=str(root), data_domain="work"
    )


def _serve_cmd(port: int, directory: Path) -> list[str]:
    return [
        sys.executable, "-m", "http.server", str(port),
        "--bind", "127.0.0.1", "--directory", str(directory),
    ]


def _wait_healthy(previews: PreviewService, owner: Actor, session_id: str, tries: int = 40):
    for _ in range(tries):
        st = previews.status(owner, session_id)
        if st["health"] == "healthy":
            return st
        time.sleep(0.25)
    return previews.status(owner, session_id)


# ----------------------------------------------------------------------
def test_preview_starts_real_server_and_probes_health(
    previews: PreviewService, owner: Actor, ws
):
    root = Path(ws.authorized_root)
    port = 18321
    sess = previews.start(
        owner, ws.id, command=_serve_cmd(port, root), target_port=port, kind="http"
    )
    try:
        assert sess["target_port"] == port
        st = _wait_healthy(previews, owner, sess["id"])
        assert st["health"] == "healthy", st
        assert st["http_status"] == 200
        assert st["process_alive"] is True
        assert port_open(port) is True
    finally:
        previews.stop(owner, sess["id"])


def test_preview_is_explicitly_untrusted_and_loopback_only(
    previews: PreviewService, owner: Actor, ws
):
    root = Path(ws.authorized_root)
    port = 18322
    sess = previews.start(
        owner, ws.id, command=_serve_cmd(port, root), target_port=port, kind="api"
    )
    try:
        st = _wait_healthy(previews, owner, sess["id"])
        # 18 §6: preview content is untrusted and must not inherit core identity.
        assert st["untrusted"] is True
        assert st["inherits_core_session"] is False
        assert st["host"] == "127.0.0.1"
        assert st["url"].startswith("http://127.0.0.1:")
        assert "no-core-cookie" in st["sandbox_policy"]
        assert st["kind"] == "api"
        assert st["access_lease"]
    finally:
        previews.stop(owner, sess["id"])


def test_stop_kills_tree_and_releases_port(previews: PreviewService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    port = 18323
    sess = previews.start(
        owner, ws.id, command=_serve_cmd(port, root), target_port=port
    )
    _wait_healthy(previews, owner, sess["id"])
    assert port_open(port) is True

    res = previews.stop(owner, sess["id"])
    assert res["state"] == "stopped"
    assert res["process_tree_killed"] is True
    assert res["port_released"] is True
    assert port_open(port) is False


def test_start_rejects_port_already_in_use(previews: PreviewService, owner: Actor, ws):
    from find_yourself.services.errors import Conflict
    root = Path(ws.authorized_root)
    port = 18324
    sess = previews.start(owner, ws.id, command=_serve_cmd(port, root), target_port=port)
    try:
        _wait_healthy(previews, owner, sess["id"])
        with pytest.raises(Conflict):
            previews.start(owner, ws.id, command=_serve_cmd(port, root), target_port=port)
    finally:
        previews.stop(owner, sess["id"])


def test_list_sessions_marks_every_entry_untrusted(previews: PreviewService, owner: Actor, ws):
    root = Path(ws.authorized_root)
    port = 18325
    sess = previews.start(owner, ws.id, command=_serve_cmd(port, root), target_port=port)
    try:
        items = previews.list_sessions(owner, ws.id)
        assert len(items) == 1
        assert items[0]["untrusted"] is True
        assert items[0]["inherits_core_session"] is False
    finally:
        previews.stop(owner, sess["id"])
