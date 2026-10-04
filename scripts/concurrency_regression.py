"""并发回归：多线程同时打工作台 / 团队只读端点。

回归的是两个历史缺陷，它们都只在并发下出现：

1. 鉴权阶段开启的 SQLite/WAL 读事务后续升级为写 → ``database is locked``，
   曾表现为 ``/tree`` 与 ``/git/status`` 偶发 12×500。
2. 读路径产生的 revision / branch 更新未提交 → 每次读重写同一行，写风暴。

本脚本只打真实 HTTP 接口，不 mock。除了 HTTP 状态码，还检查响应体里是否
出现 ``database is locked``（有些 500 会被统一错误信封包住）。

用法::

    FY_CONCURRENCY_API=http://127.0.0.1:8030 \\
    FY_CONCURRENCY_REQUESTS=60 FY_CONCURRENCY_WORKERS=12 \\
    uv run python scripts/concurrency_regression.py

需要先登录拿到会话 cookie：设置 ``FY_CONCURRENCY_COOKIE`` 与
``FY_CONCURRENCY_CSRF``。退出码非 0 表示回归重现。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = os.environ.get("FY_CONCURRENCY_API", "http://127.0.0.1:8030").rstrip("/")
REQUESTS = int(os.environ.get("FY_CONCURRENCY_REQUESTS", "60"))
WORKERS = int(os.environ.get("FY_CONCURRENCY_WORKERS", "12"))
COOKIE = os.environ.get("FY_CONCURRENCY_COOKIE", "")
CSRF = os.environ.get("FY_CONCURRENCY_CSRF", "")
WORKSPACE = os.environ.get("FY_CONCURRENCY_WORKSPACE", "")
TIMEOUT = float(os.environ.get("FY_CONCURRENCY_TIMEOUT", "20"))

LOCK_MARKERS = ("database is locked", "database table is locked", "sqlite3.OperationalError")


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if COOKIE:
        h["Cookie"] = COOKIE
    if CSRF:
        h["X-CSRF-Token"] = CSRF
    return h


def _call(method: str, path: str, body: Any = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{API}{path}", data=data, method=method, headers=_headers())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except URLError as exc:
        return 0, f"URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - surface anything else verbatim
        return 0, f"{type(exc).__name__}: {exc}"


def discover_workspace() -> str:
    if WORKSPACE:
        return WORKSPACE
    status, text = _call("GET", "/api/workbench/workspaces")
    if status != 200:
        raise SystemExit(f"cannot list workspaces (HTTP {status}): {text[:300]}")
    items = json.loads(text).get("items", [])
    if not items:
        raise SystemExit(
            "no registered workspace; register one first or set FY_CONCURRENCY_WORKSPACE"
        )
    return items[0]["id"]


def build_plan(ws_id: str) -> list[tuple[str, str, Any]]:
    """The exact endpoints that regressed under concurrency."""
    plan: list[tuple[str, str, Any]] = [
        ("GET", "/api/workbench/workspaces", None),
        ("GET", f"/api/workbench/workspaces/{ws_id}/tree", None),
        ("GET", f"/api/workbench/workspaces/{ws_id}/snapshot", None),
        ("GET", f"/api/workbench/workspaces/{ws_id}/git/status", None),
        ("GET", f"/api/workbench/workspaces/{ws_id}/git/branches", None),
        ("GET", "/api/teams", None),
    ]
    # Round-robin so every worker mixes the read paths, which is what surfaced
    # the stale-snapshot upgrade.
    for i in range(REQUESTS):
        plan.append(plan[i % len(plan)])
    return plan


def main() -> int:
    ws_id = discover_workspace()
    plan = build_plan(ws_id)
    statuses: Counter[int] = Counter()
    lock_hits: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()
    by_path: dict[str, Counter] = {}

    started = time.time()

    def worker(task: tuple[str, str, Any]) -> None:
        method, path, body = task
        status, text = _call(method, path, body)
        with lock:
            statuses[status] += 1
            by_path.setdefault(path, Counter())[status] += 1
            if any(m in text.lower() for m in LOCK_MARKERS):
                lock_hits.append(f"{path} -> {text[:200]}")
            elif status == 0 or status >= 500:
                errors.append(f"{path} -> HTTP {status}: {text[:200]}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(worker, plan))

    elapsed = time.time() - started
    total = sum(statuses.values())
    server_errors = sum(n for s, n in statuses.items() if s >= 500 or s == 0)
    ok = sum(n for s, n in statuses.items() if 200 <= s < 300)

    report = {
        "api": API,
        "workspace_id": ws_id,
        "requests": total,
        "workers": WORKERS,
        "seconds": round(elapsed, 2),
        "rps": round(total / elapsed, 1) if elapsed else None,
        "status_counts": {str(k): v for k, v in sorted(statuses.items())},
        "by_path": {p: {str(k): v for k, v in sorted(c.items())} for p, c in by_path.items()},
        "ok_2xx": ok,
        "server_errors": server_errors,
        "database_lock_hits": lock_hits,
        "errors": errors[:20],
        "verdict": "PASS" if (server_errors == 0 and not lock_hits) else "FAIL",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if server_errors or lock_hits:
        print("\nCONCURRENCY REGRESSION REPRODUCED", file=sys.stderr)
        return 1
    print(f"\nOK: {total} requests, 0x5xx, 0 database-is-locked ({elapsed:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())