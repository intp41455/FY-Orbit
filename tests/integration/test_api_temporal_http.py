"""REAL HTTP <-> Temporal integration over a live uvicorn + worker.

This is NOT a TestClient test. It spawns:
  * a real Worker process  (``python -m find_yourself.workflows.worker --adapter auto``)
  * a real uvicorn process  (``uvicorn find_yourself.api.main:app`` on a unique loopback port)

and drives them over real HTTP with the loopback local token.

Gating (never hangs ordinary CI):
  * requires ``FY_RUN_HTTP_TEMPORAL=1``;
  * requires ``FY_DATABASE_URL`` to be a postgresql DSN reachable on 127.0.0.1:5432;
  * requires Temporal reachable on 127.0.0.1:7233.
Otherwise the whole module skips.

No paid model, no MinIO. Budget/state assertions read a fresh Postgres connection.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
PG_URL = os.environ.get("FY_DATABASE_URL", "")
TEMPORAL_ADDR = os.environ.get("FY_TEMPORAL_ADDRESS", "127.0.0.1:7233")
RUN_GATE = os.environ.get("FY_RUN_HTTP_TEMPORAL") == "1"
# Isolate from the always-on production fy-worker container on the shared queue.
QUEUE = os.environ.get("FY_TEMPORAL_QUEUE") or f"find-yourself-test-{uuid.uuid4().hex[:8]}"


def _reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _free_port(lo: int = 8030, hi: int = 8060) -> int:
    for p in range(lo, hi):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    raise RuntimeError("no free loopback port")


@pytest.fixture(scope="module")
def live_stack():
    if not RUN_GATE:
        pytest.skip("FY_RUN_HTTP_TEMPORAL!=1; real HTTP-Temporal integration skipped")
    if not PG_URL.startswith("postgresql"):
        pytest.skip("FY_DATABASE_URL is not a postgresql DSN")
    h, _, port = TEMPORAL_ADDR.partition(":")
    if not _reachable(h, int(port)):
        pytest.skip("Temporal frontend not reachable")
    if not _reachable("127.0.0.1", 5432):
        pytest.skip("Postgres 127.0.0.1:5432 not reachable")

    port = _free_port()
    secret = "http-temporal-it-" + uuid.uuid4().hex[:24]
    local_token = "it-local-" + uuid.uuid4().hex

    env = dict(os.environ)
    env.update({
        "FY_ENVIRONMENT": "local",
        "FY_DATABASE_URL": PG_URL,
        "FY_TEMPORAL_ADDRESS": TEMPORAL_ADDR,
        "FY_TEMPORAL_NAMESPACE": os.environ.get("FY_TEMPORAL_NAMESPACE", "default"),
        "FY_TEMPORAL_QUEUE": QUEUE,
        "FY_SESSION_SECRET": secret,
        "FY_LOCAL_TOKEN": local_token,
        "FY_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "FY_ARTIFACTS_PATH": str(ROOT / ".runtime" / "artifacts"),
        "UV_CACHE_DIR": str(ROOT / ".runtime" / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(ROOT / ".runtime" / "python"),
    })

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "find_yourself.api.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    # Wait for the app to be ready with Temporal connected.
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if api.poll() is not None:
            out = api.stdout.read() if api.stdout else ""
            raise RuntimeError(f"uvicorn exited early rc={api.returncode}\n{out}")
        try:
            r = requests.get(f"{base}/health/ready", timeout=3)
            if r.status_code == 200 and r.json().get("temporal") == "ready":
                ready = True
                break
        except requests.RequestException:
            time.sleep(0.5)
    if not ready:
        api.kill()
        raise RuntimeError("uvicorn never became ready with temporal=ready")

    yield {
        "base": base, "port": port, "env": env,
        "local_token": local_token, "api": api,
    }

    api.kill()
    try:
        api.wait(timeout=10)
    except subprocess.TimeoutExpired:
        api.kill()
    # Wait for the port to be released.
    for _ in range(20):
        if not _reachable("127.0.0.1", port, timeout=0.5):
            break
        time.sleep(0.5)


def _start_worker(env: dict, planner_mode: str) -> subprocess.Popen:
    we = dict(env)
    we["FY_PLANNER_MODE"] = planner_mode
    return subprocess.Popen(
        [sys.executable, "-m", "find_yourself.workflows.worker",
         "--adapter", "auto", "--queue", QUEUE],
        cwd=str(ROOT), env=we,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _wait_worker_ready(w: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if w.poll() is not None:
            out = w.stdout.read() if w.stdout else ""
            raise RuntimeError(f"worker exited early rc={w.returncode}\n{out}")
        line = w.stdout.readline() if w.stdout else ""
        if '"polling"' in line:
            return
        if not line:
            time.sleep(0.2)
    raise RuntimeError("worker never reached polling")


def _client(base: str, local_token: str):
    s = requests.Session()
    r = s.post(f"{base}/auth/local/dev-token", json={"token": local_token}, timeout=10)
    r.raise_for_status()
    s.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})
    return s


def _pg_query(sql: str, *args):
    import psycopg
    c = psycopg.connect(PG_URL.replace("postgresql+psycopg", "postgresql"), connect_timeout=5)
    try:
        cur = c.cursor()
        cur.execute(sql, args)
        return cur.fetchall()
    finally:
        c.close()


def test_happy_path_then_cancel(live_stack):
    base = live_stack["base"]
    env = live_stack["env"]
    s = _client(base, live_stack["local_token"])

    # ---- Phase 1: happy path (worker default planner -> finish) ----
    w1 = _start_worker(env, "finish")
    try:
        _wait_worker_ready(w1)
        key = "it-http-" + uuid.uuid4().hex[:8]
        t = s.post(f"{base}/api/tasks", json={
            "goal": "http temporal happy", "domain": "personal", "mode": "listen",
            "strategy": "auto", "idempotency_key": key}, timeout=10).json()
        assert t["workflow"]["started"] is True
        task_id = t["id"]

        # Idempotent replay must NOT restart the workflow.
        t2 = s.post(f"{base}/api/tasks", json={
            "goal": "http temporal happy", "domain": "personal", "mode": "listen",
            "strategy": "auto", "idempotency_key": key}, timeout=10).json()
        assert t2["id"] == task_id
        assert t2["workflow"]["started"] is False

        status = None
        for _ in range(60):
            status = s.get(f"{base}/api/tasks/{task_id}", timeout=10).json()["status"]
            if status in ("completed", "failed", "cancelled"):
                break
            time.sleep(1)
        assert status == "completed"

        rows = _pg_query("select status from tasks where id=%s", task_id)
        assert rows and rows[0][0] == "completed"
        att = _pg_query("select status from task_attempts where task_id=%s order by attempt_no", task_id)
        assert att and att[0][0] == "succeeded"
    finally:
        w1.kill()
        try:
            w1.wait(timeout=10)
        except subprocess.TimeoutExpired:
            w1.kill()

    # ---- Phase 2: wait_input task, cancel over HTTP ----
    w2 = _start_worker(env, "wait_input")
    try:
        _wait_worker_ready(w2)
        key2 = "it-http-cx-" + uuid.uuid4().hex[:8]
        t = s.post(f"{base}/api/tasks", json={
            "goal": "http temporal cancel", "domain": "personal", "mode": "listen",
            "strategy": "auto", "idempotency_key": key2}, timeout=10).json()
        task_id2 = t["id"]
        assert t["workflow"]["started"] is True

        reached = False
        for _ in range(40):
            g = s.get(f"{base}/api/tasks/{task_id2}", timeout=10).json()
            if g["stage"] == "awaiting_input":
                reached = True
                break
            time.sleep(1)
        assert reached, "task never reached awaiting_input"

        c = s.post(f"{base}/api/tasks/{task_id2}/cancel", timeout=10).json()
        assert c["workflow"]["sent"] is True

        status = None
        for _ in range(40):
            status = s.get(f"{base}/api/tasks/{task_id2}", timeout=10).json()["status"]
            if status in ("completed", "failed", "cancelled"):
                break
            time.sleep(1)
        assert status == "cancelled"

        rows = _pg_query("select status from tasks where id=%s", task_id2)
        assert rows and rows[0][0] == "cancelled"
        # The workflow's task_cancel activity updates the attempt asynchronously;
        # poll the DB (source of truth) until it settles.
        att = None
        for _ in range(30):
            att = _pg_query("select status from task_attempts where task_id=%s order by attempt_no", task_id2)
            if att and att[0][0] in ("cancelled", "failed", "succeeded"):
                break
            time.sleep(1)
        assert att and att[0][0] == "cancelled"
        res = _pg_query(
            "select count(*) from budget_reservations where task_id=%s and state in ('reserved','unknown')",
            task_id2)
        assert res[0][0] == 0, "no active reservation expected after cancel"
    finally:
        w2.kill()
        try:
            w2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            w2.kill()
