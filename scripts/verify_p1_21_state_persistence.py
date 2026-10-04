"""P1-21 acceptance verification: session state survives a real backend restart.

Flow (real process evidence, no simulation):

1. Boot backend A on 127.0.0.1:8103 with a temp SQLite DB (fresh).
2. Seed state:
   - a 30-turn conversation (60 ORM Message rows) written to the DB,
   - register 2 tools via ``POST /api/tools/register``,
   - run 1 DSL document via ``POST /api/dsl-canvas/runs``.
3. ``POST /api/session-state/snapshot`` -> archive response.
4. Kill backend A (process exit) and boot backend B on the same DB/port —
   in-memory state (DSL run store, window cache) is empty again.
5. ``GET /api/session-state/restore`` -> replay snapshot into the live
   runtime; archive response.
6. Field-by-field diff of ``snapshot.state`` (pre-restart) vs
   ``live_state`` (post-restart re-export): must be EMPTY. Also verify the
   DSL run is reachable again on ``/api/dsl-canvas/runs/{run_id}`` and the
   restored window is readable via ``/api/session-state/windows/{key}``.

Evidence is archived under ``evidence/p1-21-state/``.

Usage::

    uv run --no-sync python scripts/verify_p1_21_state_persistence.py \
        --port 8103 --evidence evidence/p1-21-state
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TOKEN = "p1-21-verify-token-0123456789abcdef"
SESSION_KEY = "conv-p1-21-30turns"
CONV_ID = "conv-p1-21-30turns"
OWNER_ID = "owner-p1-21"

DSL_DOC = {
    "version": "1",
    "nodes": [
        {"id": "in", "type": "input", "params": {"kind": "literal", "value": [
            {"topic": "对话恢复"},
            {"topic": "状态持久化"},
            {"topic": "重启验证"},
            {"topic": "无关条目"},
        ]}},
        {"id": "keep", "type": "transform", "verb": "filter",
         "params": {"field": "topic", "op": "contains", "value": "恢复"}},
        {"id": "out", "type": "output", "params": {"format": "text"}},
    ],
    "edges": [
        {"from": "in", "to": "keep"},
        {"from": "keep", "to": "out"},
    ],
}

DIFF_FIELDS = [
    "memory_window.summary",
    "memory_window.summarized_message_ids",
    "memory_window.recent_messages",
    "memory_window.token_estimate",
    "memory_window.original_token_estimate",
    "memory_window.compressed",
    "memory_window.turns_total",
    "memory_window.turns_summarized",
    "memory_window.turns_recent",
    "tool_registry.tools",
    "dsl_runs.runs",
]


def login(port: int) -> str:
    """Bootstrap a loopback owner session; returns 'fy_session=...; ...' cookie."""
    body = json.dumps({"token": TOKEN}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/auth/local/dev-token",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    csrf = ""
    cookie = ""
    with urllib.request.urlopen(req, timeout=15) as resp:
        csrf = json.loads(resp.read().decode("utf-8")).get("csrf_token", "")
        for raw in resp.headers.get_all("Set-Cookie") or []:
            if raw.startswith("fy_session="):
                cookie = raw.split(";")[0]
    if not cookie or not csrf:
        raise RuntimeError("dev-token login failed")
    return cookie, csrf


STATE = {"port": 0, "cookie": "", "csrf": ""}


def http_json(method: str, port: int, path: str, body: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Cookie", STATE["cookie"])
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.add_header("x-csrf-token", STATE["csrf"])
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ready(port: int, proc: subprocess.Popen, timeout_s: float = 40.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"backend exited early rc={proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("backend did not become ready in time")


def boot(port: int, db: Path, workdir: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["FY_ENVIRONMENT"] = "test"
    env["FY_DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env["FY_LOCAL_TOKEN"] = TOKEN
    env["FY_PUBLIC_URL"] = f"http://127.0.0.1:{port}"
    env["FY_SESSION_SECRET"] = "p1-21-session-secret-that-is-long-enough-0123456789"
    env["FY_TOOL_REGISTRY_DIR"] = str(workdir / "tool_registry")
    env["FY_DSL_ARCHIVE_DIR"] = str(workdir / "dsl_archive")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "start_e2e_backend.py"),
         "--port", str(port), "--db", str(db), "--token", TOKEN],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    wait_ready(port, proc)
    return proc


def seed_conversation(db: Path) -> int:
    """Write a 30-turn conversation (user+assistant per turn) into the DB."""
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from find_yourself.db.models import Conversation, Message

    engine = create_engine(f"sqlite:///{db.as_posix()}")
    base = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(Conversation(
            id=CONV_ID, owner_id=OWNER_ID, title="P1-21 持久化验证会话",
            domain="work", mode="research", created_at=base, updated_at=base,
        ))
        for i in range(1, 31):
            session.add(Message(
                id=f"msg-p121-u{i:02d}", conversation_id=CONV_ID, role="user",
                content=(
                    f"第{i}轮：我们要讨论需求点 {i}，请分析现状并给出建议。"
                    f"另外请记住：项目代号 HYACINTH，预算上限 {i * 3} 万，"
                    f"任何超出预算的采购都需要走审批流程，并同步给负责人确认。"
                ),
                client_message_id=f"p121-u{i:02d}", created_at=base,
            ))
            session.add(Message(
                id=f"msg-p121-a{i:02d}", conversation_id=CONV_ID, role="assistant",
                content=(
                    f"第{i}轮回复：已记录需求点 {i} 的分析结论——优先级中等，"
                    f"建议下一迭代跟进；预算 {i * 3} 万与代号 HYACINTH 已纳入长期记忆。"
                ),
                client_message_id=f"p121-a{i:02d}",
                created_at=datetime(2026, 10, 3, 9, 0, i, tzinfo=timezone.utc),
            ))
        session.commit()
    engine.dispose()
    return 60


def diff_state(before: dict, after: dict) -> list[str]:
    """Field-by-field diff of snapshot.state vs live_state."""
    diffs: list[str] = []
    for field in DIFF_FIELDS:
        node_before: Any = before
        node_after: Any = after
        for part in field.split("."):
            node_before = node_before[part]
            node_after = node_after[part]
        if node_before != node_after:
            diffs.append(f"{field}: before={json.dumps(node_before, ensure_ascii=False)[:200]!r} "
                         f"after={json.dumps(node_after, ensure_ascii=False)[:200]!r}")
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8103)
    ap.add_argument("--evidence", default="evidence/p1-21-state")
    ap.add_argument("--workdir", default=".runtime/p1-21")
    args = ap.parse_args()

    evidence = ROOT / args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    workdir = ROOT / args.workdir
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    db = workdir / "p1-21-state.db"

    log: list[str] = []

    def note(msg: str) -> None:
        print(msg, flush=True)
        log.append(msg)

    proc_a = proc_b = None
    try:
        # -- Phase A: boot, seed state, snapshot -------------------------------
        note("[A] booting backend A ...")
        proc_a = boot(args.port, db, workdir)
        STATE["port"] = args.port
        STATE["cookie"], STATE["csrf"] = login(args.port)
        messages = seed_conversation(db)
        note(f"[A] seeded conversation {CONV_ID}: {messages} messages (30 turns)")

        for name, desc in (
            ("p1_21_state_probe", "P1-21 verification probe tool (echo builtin)"),
            ("p1_21_budget_add", "P1-21 verification budget tool (add builtin)"),
        ):
            body = {
                "name": name,
                "description": desc,
                "parameters": {"type": "object", "properties": {}, "required": []}
                if name.endswith("probe") else {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
                "entry": {"type": "builtin", "executor": "echo" if name.endswith("probe") else "add"},
            }
            http_json("POST", args.port, "/api/tools/register", body)
        note("[A] registered 2 tools via POST /api/tools/register")

        run = http_json("POST", args.port, "/api/dsl-canvas/runs", DSL_DOC)
        note(f"[A] ran DSL: run_id={run['run_id']} status={run['status']} output={run['output']!r}")

        snap = http_json("POST", args.port, "/api/session-state/snapshot", {
            "session_key": SESSION_KEY,
            "conversation_id": CONV_ID,
        })
        mw = snap["snapshot"]["state"]["memory_window"]
        note(f"[A] snapshot ok: schema_version={snap['snapshot']['schema_version']}, "
             f"compressed={mw['compressed']}, turns_summarized={mw['turns_summarized']}, "
             f"turns_recent={mw['turns_recent']}, "
             f"tools={len(snap['snapshot']['state']['tool_registry']['tools'])}, "
             f"dsl_runs={len(snap['snapshot']['state']['dsl_runs']['runs'])}")
        (evidence / "snapshot-before.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

        # -- Restart: kill A, boot B on the same DB ----------------------------
        note("[R] terminating backend A (process exit) ...")
        proc_a.terminate()
        proc_a.wait(timeout=15)
        proc_a = None
        note("[R] booting backend B (fresh process, same DB) ...")
        proc_b = boot(args.port, db, workdir)
        STATE["cookie"], STATE["csrf"] = login(args.port)

        # -- Phase B: restore + verify -----------------------------------------
        restored = http_json("GET", args.port,
                             f"/api/session-state/restore?session_key={SESSION_KEY}")
        (evidence / "restore-after-restart.json").write_text(
            json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf-8")
        note(f"[B] restore ok: reloaded={restored['reloaded']}")

        diffs = diff_state(snap["snapshot"]["state"], restored["live_state"])
        tools_after = http_json("GET", args.port, "/api/tools/discover")
        run_after = http_json("GET", args.port, f"/api/dsl-canvas/runs/{run['run_id']}")
        window_after = http_json("GET", args.port, f"/api/session-state/windows/{SESSION_KEY}")
        (evidence / "tools-after-restart.json").write_text(
            json.dumps(tools_after, ensure_ascii=False, indent=2), encoding="utf-8")
        (evidence / "dsl-run-after-restart.json").write_text(
            json.dumps(run_after, ensure_ascii=False, indent=2), encoding="utf-8")
        (evidence / "memory-window-after-restart.json").write_text(
            json.dumps(window_after, ensure_ascii=False, indent=2), encoding="utf-8")

        live_run_ok = run_after.get("run_id") == run["run_id"] and \
            run_after.get("logs") == run.get("logs")
        live_tools_ok = {t["name"] for t in tools_after.get("tools", [])} >= {
            "p1_21_state_probe", "p1_21_budget_add"}

        result = {
            "ticket": "P1-21 状态持久化验证",
            "acceptance": "重启后端状态恢复，前后快照 diff 为空",
            "session_key": SESSION_KEY,
            "backend_restarted": True,
            "port": args.port,
            "diff_empty": not diffs,
            "fields_checked": DIFF_FIELDS,
            "diffs": diffs,
            "live_dsl_run_restored": live_run_ok,
            "live_tools_restored": live_tools_ok,
            "evidence_files": [
                "snapshot-before.json", "restore-after-restart.json",
                "tools-after-restart.json", "dsl-run-after-restart.json",
                "memory-window-after-restart.json", "diff-result.json",
                "verify-log.txt",
            ],
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        (evidence / "diff-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        ok = result["diff_empty"] and live_run_ok and live_tools_ok
        note(f"[V] diff_empty={result['diff_empty']} diffs={diffs}")
        note(f"[V] live_dsl_run_restored={live_run_ok} live_tools_restored={live_tools_ok}")
        note(f"[V] P1-21 {'PASS' if ok else 'FAIL'} — evidence: {evidence}")
        return 0 if ok else 1
    finally:
        for proc in (proc_a, proc_b):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        (evidence / "verify-log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
