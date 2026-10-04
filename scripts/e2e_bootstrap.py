"""Bootstrap the E2E/concurrency environment in one shot.

Logs in once (the CSRF token is bound to the session it was issued with, so the
cookie and the token MUST come from the same response), registers a git-backed
workspace, and writes ``.runtime/e2e-auth.env`` for the concurrency script.

Usage::

    uv run --no-sync python scripts/e2e_bootstrap.py --token "$E2E_LOCAL_TOKEN"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict, str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, json.loads(raw) if raw else {}, r.headers.get("set-cookie", "")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), ""
        except json.JSONDecodeError:
            return e.code, {"raw": raw}, ""


def get(url: str, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:300]}


def make_workspace(name: str) -> str:
    ws = ROOT / ".runtime" / "concurrency-ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "app.py").write_text("value = 1\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "e2e@example.invalid"],
        ["git", "config", "user.name", "e2e"],
        ["git", "add", "app.py"],
        ["git", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=ws, capture_output=True)
    # Windows-safe absolute form.
    return str(ws.resolve()).replace("\\", "/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8030")
    args = ap.parse_args()
    api = args.api.rstrip("/")

    status, payload, set_cookie = post(f"{api}/auth/local/dev-token", {"token": args.token})
    if status != 200:
        print(f"login failed HTTP {status}: {payload}", file=__import__("sys").stderr)
        return 1
    cookie = ""
    for part in set_cookie.split(","):
        seg = part.strip()
        if seg.startswith("fy_session="):
            cookie = "fy_session=" + seg[len("fy_session="):].split(";")[0]
            break
    if not cookie:
        print("no fy_session cookie in the login response", file=__import__("sys").stderr)
        return 1
    csrf = payload["csrf_token"]

    root = make_workspace("concurrency-ws")
    h = {"Cookie": cookie, "X-CSRF-Token": csrf, "Content-Type": "application/json"}
    status, ws, _ = post(
        f"{api}/api/workbench/workspaces",
        {"project_name": "concurrency-ws", "authorized_root": root},
        headers=h,
    )
    if status != 201:
        print(f"workspace registration failed HTTP {status}: {ws}",
              file=__import__("sys").stderr)
        return 1
    ws_id = ws["id"]

    st, listed = get(f"{api}/api/workbench/workspaces", {"Cookie": cookie})
    print(json.dumps({
        "workspace_id": ws_id,
        "authorized_root": root,
        "workspaces": [w["id"] for w in listed.get("items", [])],
        "list_status": st,
    }, ensure_ascii=False, indent=2))

    env = ROOT / ".runtime" / "e2e-auth.env"
    env.write_text(
        f"COOKIE={cookie}\nCSRF={csrf}\nWSID={ws_id}\nAPI={api}\n", encoding="utf-8"
    )
    print(f"\nwrote {env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())