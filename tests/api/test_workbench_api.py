"""API tests: 18 工程代码工作台 — HTTP surface of the workbench.

Exercises the real FastAPI app over a real TestClient: workspace registration,
file read/write with revision conflicts, path-escape rejection, terminal
lifecycle, Git status, preview isolation flags and orchestrator lease takeover.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from helpers import login_owner


@pytest.fixture()
def headers(client: TestClient) -> dict[str, str]:
    return login_owner(client)


@pytest.fixture()
def ws_id(client: TestClient, headers: dict[str, str], tmp_path) -> str:
    root = tmp_path / "api-project"
    root.mkdir()
    (root / "app.py").write_bytes(b"value = 1\n")
    r = client.post(
        "/api/workbench/workspaces",
        json={"project_name": "API 工作台工程", "authorized_root": str(root)},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ----------------------------------------------------------------------
def test_register_and_list(client: TestClient, headers: dict[str, str], ws_id: str):
    r = client.get("/api/workbench/workspaces", headers=headers)
    assert r.status_code == 200
    assert r.json()["count"] >= 1
    assert any(w["id"] == ws_id for w in r.json()["items"])


def test_register_requires_absolute_root(client: TestClient, headers: dict[str, str]):
    r = client.post(
        "/api/workbench/workspaces",
        json={"project_name": "bad", "authorized_root": "relative/dir"},
        headers=headers,
    )
    assert r.status_code == 422


def test_write_requires_csrf(client: TestClient, headers: dict[str, str], ws_id: str):
    r = client.post(
        f"/api/workbench/workspaces/{ws_id}/file?path=app.py",
        json={"content": "x = 2\n"},
    )
    # No CSRF header and no session cookie → unauthenticated.
    assert r.status_code in (401, 403)


# ----------------------------------------------------------------------
def test_read_write_and_revision_conflict(client: TestClient, headers: dict[str, str], ws_id: str):
    r = client.get(f"/api/workbench/workspaces/{ws_id}/file?path=app.py", headers=headers)
    assert r.status_code == 200
    first = r.json()
    assert first["content"] == "value = 1\n"
    assert first["revision"] == 1

    r2 = client.post(
        f"/api/workbench/workspaces/{ws_id}/file?path=app.py",
        json={"content": "value = 2\n", "expected_revision": first["revision"]},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["revision"] == 2

    # A stale expected_revision must conflict rather than overwrite.
    r3 = client.post(
        f"/api/workbench/workspaces/{ws_id}/file?path=app.py",
        json={"content": "value = 3\n", "expected_revision": 1},
        headers=headers,
    )
    assert r3.status_code == 409


def test_path_escape_is_rejected(client: TestClient, headers: dict[str, str], ws_id: str):
    for bad in ("..%2F..%2Fetc%2Fpasswd", "..\\..\\outside.txt"):
        r = client.get(f"/api/workbench/workspaces/{ws_id}/file?path={bad}", headers=headers)
        assert r.status_code in (403, 422), (bad, r.status_code, r.text)


def test_tree_and_events(client: TestClient, headers: dict[str, str], ws_id: str):
    r = client.get(f"/api/workbench/workspaces/{ws_id}/tree", headers=headers)
    assert r.status_code == 200
    assert any(e["name"] == "app.py" for e in r.json()["entries"])

    ev = client.get(f"/api/workbench/workspaces/{ws_id}/events", headers=headers)
    assert ev.status_code == 200 and ev.json()["count"] >= 0

    snap = client.get(f"/api/workbench/workspaces/{ws_id}/snapshot", headers=headers)
    assert snap.status_code == 200 and "cursor" in snap.json()


# ----------------------------------------------------------------------
def test_terminal_lifecycle_over_http(client: TestClient, headers: dict[str, str], ws_id: str):
    r = client.post(
        f"/api/workbench/workspaces/{ws_id}/terminals",
        json={"cols": 100, "rows": 30, "timeout_seconds": 120},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sess = r.json()
    sid = sess["id"]
    try:
        assert sess["state"] == "running"
        # Honest labelling: never claim a resizable interactive terminal when
        # the session is only pipe-backed.
        assert sess["presentation"] in ("interactive_pty", "command_log")
        assert sess["interactive"] == (sess["presentation"] == "interactive_pty")

        w = client.post(
            f"/api/workbench/terminals/{sid}/write",
            json={"data": "echo API_TERMINAL_MARKER\r\n"},
            headers=headers,
        )
        assert w.status_code == 200

        marker_seen = False
        for _ in range(30):
            rd = client.get(f"/api/workbench/terminals/{sid}/read?wait_seconds=0.3", headers=headers)
            if "API_TERMINAL_MARKER" in rd.json().get("output", ""):
                marker_seen = True
                break
            time.sleep(0.2)
        assert marker_seen, "terminal did not echo the command marker"
    finally:
        st = client.post(
            f"/api/workbench/terminals/{sid}/stop", json={"reason": "user_stop"}, headers=headers
        )
        assert st.status_code == 200
        assert st.json()["process_tree_killed"] is True


# ----------------------------------------------------------------------
def test_git_status_and_bulk_stage_rejection(
    client: TestClient, headers: dict[str, str], ws_id: str, tmp_path
):
    import subprocess

    root = tmp_path / "api-project"
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "wb"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(root), check=True)

    r = client.get(f"/api/workbench/workspaces/{ws_id}/git/status", headers=headers)
    assert r.status_code == 200, r.text
    assert "branch" in r.json()

    bad = client.post(
        f"/api/workbench/workspaces/{ws_id}/git/stage", json={"paths": ["."]}, headers=headers
    )
    assert bad.status_code == 422


# ----------------------------------------------------------------------
def test_orchestrator_lease_takeover_over_http(client: TestClient, headers: dict[str, str]):
    root_task_id = "root-api-1"
    r = client.post(
        "/api/workbench/orchestrator/lease",
        json={"root_task_id": root_task_id, "orchestrator_id": "Codex",
              "capabilities": ["plan", "dispatch", "review"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["fencing_token"] == 1

    dup = client.post(
        "/api/workbench/orchestrator/lease",
        json={"root_task_id": root_task_id, "orchestrator_id": "Hermes",
              "capabilities": ["plan", "dispatch", "review"]},
        headers=headers,
    )
    assert dup.status_code == 409

    chk = client.post(
        "/api/workbench/orchestrator/dispatch-check",
        json={"root_task_id": root_task_id, "orchestrator_id": "Codex", "fencing_token": 1},
        headers=headers,
    )
    assert chk.status_code == 200 and chk.json()["valid"] is True

    stale = client.post(
        "/api/workbench/orchestrator/dispatch-check",
        json={"root_task_id": root_task_id, "orchestrator_id": "Codex", "fencing_token": 0},
        headers=headers,
    )
    assert stale.status_code == 409

    # A candidate that only answers version/help must not be accepted.
    weak = client.post(
        "/api/workbench/orchestrator/takeover",
        json={"root_task_id": root_task_id, "new_orchestrator_id": "StaticDocGen",
              "new_capabilities": ["version"]},
        headers=headers,
    )
    assert weak.status_code == 422

    take = client.post(
        "/api/workbench/orchestrator/takeover",
        json={"root_task_id": root_task_id, "new_orchestrator_id": "Hermes",
              "new_capabilities": ["plan", "dispatch", "review"]},
        headers=headers,
    )
    assert take.status_code == 200, take.text
    assert take.json()["fencing_token"] == 2
    assert take.json()["paused"] is True

    # Dispatch stays blocked until the owner has seen the summary and resumed.
    paused = client.post(
        "/api/workbench/orchestrator/dispatch-check",
        json={"root_task_id": root_task_id, "orchestrator_id": "Hermes", "fencing_token": 2},
        headers=headers,
    )
    assert paused.status_code == 409

    resume = client.post(
        "/api/workbench/orchestrator/takeover/resume",
        json={"root_task_id": root_task_id, "orchestrator_id": "Hermes"},
        headers=headers,
    )
    assert resume.status_code == 200 and resume.json()["resumed"] is True

    ok = client.post(
        "/api/workbench/orchestrator/dispatch-check",
        json={"root_task_id": root_task_id, "orchestrator_id": "Hermes", "fencing_token": 2},
        headers=headers,
    )
    assert ok.status_code == 200

    old = client.post(
        "/api/workbench/orchestrator/dispatch-check",
        json={"root_task_id": root_task_id, "orchestrator_id": "Codex", "fencing_token": 1},
        headers=headers,
    )
    assert old.status_code == 403
