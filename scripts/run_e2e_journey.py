"""Local End-to-End Journey Runner (Step 1 Verification).

Starts FastAPI backend on loopback (127.0.0.1:8030) with an isolated SQLite DB,
drives the full user journey via Playwright, captures all 11 step screenshots
to evidence/e2e-screenshots/, and outputs execution logs.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def wait_for_health(url: str, timeout_sec: int = 20) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    web_dir = root_dir / "web"
    runtime_dir = root_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    db_path = runtime_dir / "e2e-journey.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    evidence_dir = root_dir / "evidence" / "e2e-screenshots"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    python_exe = sys.executable

    backend_log = open(runtime_dir / "backend_e2e.log", "w", encoding="utf-8")
    env = os.environ.copy()
    env["FY_ENVIRONMENT"] = "local"
    env["FY_SESSION_SECRET"] = "dev-session-secret-must-be-at-least-32-chars-long!"
    env["FY_LOCAL_TOKEN"] = "dev-token-secret"
    env["FY_DATABASE_URL"] = "sqlite:///.runtime/e2e-journey.db"
    env["FY_PUBLIC_URL"] = "http://127.0.0.1:8030"

    print("=================================================================")
    print("[E2E] Starting FastAPI backend on http://127.0.0.1:8030...")
    print("=================================================================")

    backend_proc = subprocess.Popen(
        [
            python_exe,
            "-m",
            "uvicorn",
            "find_yourself.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8030",
            "--log-level",
            "info",
        ],
        cwd=str(root_dir),
        env=env,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        health_ok = wait_for_health("http://127.0.0.1:8030/health/live", timeout_sec=25)
        if not health_ok:
            print("[E2E Error] Backend failed to start or /health/live did not return 200.")
            backend_log.flush()
            with open(runtime_dir / "backend_e2e.log", "r", encoding="utf-8", errors="replace") as f:
                print("[Backend Output]:\n", f.read())
            return 1

        print("[E2E] Building web frontend (npm run build)...")
        b_res = subprocess.run(
            ["npm.cmd" if sys.platform == "win32" else "npm", "run", "build"],
            cwd=str(web_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if b_res.returncode != 0:
            print("[E2E Error] npm run build failed:\n", b_res.stderr)
            return 1
        print("[E2E] Web frontend built successfully!")

        print("[E2E] Backend is healthy! Starting Playwright journey tests...")

        playwright_env = os.environ.copy()
        playwright_env["E2E_API_TARGET"] = "http://127.0.0.1:8030"
        playwright_env["E2E_LOCAL_TOKEN"] = "dev-token-secret"
        playwright_env["E2E_PREVIEW_PORT"] = "4173"

        pw_cmd = ["npx.cmd" if sys.platform == "win32" else "npx", "playwright", "test", "e2e/e2e_journey.spec.ts", "--project=desktop"]

        res = subprocess.run(
            pw_cmd,
            cwd=str(web_dir),
            env=playwright_env,
            text=True,
            check=False,
        )

        print("\n=================================================================")
        print(f"[E2E] Playwright exited with code: {res.returncode}")
        print("=================================================================")

        # List captured screenshots
        screenshots = sorted(list(evidence_dir.glob("*.png")))
        print(f"[E2E] Captured {len(screenshots)} evidence screenshots in {evidence_dir}:")
        for sc in screenshots:
            print(f"  - {sc.name} ({sc.stat().st_size} bytes)")

        return res.returncode

    finally:
        print("[E2E] Shutting down backend process...")
        try:
            backend_proc.terminate()
            backend_proc.wait(timeout=5)
        except Exception:
            backend_proc.kill()
        print("[E2E] Backend shutdown complete.")


if __name__ == "__main__":
    sys.exit(main())
