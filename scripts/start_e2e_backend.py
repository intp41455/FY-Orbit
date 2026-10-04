"""Start an isolated acceptance backend for E2E / concurrency runs.

Binds strictly to loopback, uses its own SQLite database, prints the
loopback-only dev token on stdout so the caller can drive Playwright, and
shuts the server down cleanly on exit.

Usage::

    uv run --no-sync python scripts/start_e2e_backend.py --port 8030
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--db", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--ready-file", default="")
    args = ap.parse_args()

    db = args.db or str(ROOT / ".runtime" / f"e2e-backend-{args.port}.db")
    Path(db).parent.mkdir(parents=True, exist_ok=True)

    token = args.token or f"e2e-{secrets.token_hex(16)}"

    os.environ["FY_ENVIRONMENT"] = "test"
    os.environ["FY_DATABASE_URL"] = f"sqlite:///{db.replace(os.sep, '/')}"
    os.environ["FY_LOCAL_TOKEN"] = token
    os.environ["FY_PUBLIC_URL"] = f"http://127.0.0.1:{args.port}"
    os.environ["FY_SESSION_SECRET"] = (
        os.environ.get("FY_SESSION_SECRET")
        or "e2e-session-secret-that-is-long-enough-0123456789"
    )
    os.environ["FY_ARTIFACTS_PATH"] = str(Path(db).parent / "e2e-artifacts")

    sys.path.insert(0, str(ROOT / "src"))
    import uvicorn

    from find_yourself.api.app import create_app

    app = create_app()

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)

    def announce() -> None:
        for _ in range(120):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{args.port}/health/live", timeout=1
                ) as r:
                    if r.status == 200:
                        break
            except Exception:
                continue
        if args.ready_file:
            Path(args.ready_file).write_text(f"{args.port}\n{token}\n", encoding="utf-8")
        print(f"E2E_BACKEND_PORT={args.port}", flush=True)
        print(f"E2E_LOCAL_TOKEN={token}", flush=True)
        print(f"E2E_API_TARGET=http://127.0.0.1:{args.port}", flush=True)
        print("E2E_BACKEND_READY", flush=True)

    threading.Thread(target=announce, daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())