"""Find Yourself Desktop Runner (run_desktop.py).

Provides a cross-platform Python desktop entrypoint:
1. Configures isolated local runtime paths (data, logs, webview profile).
2. Spawns local FastAPI server bound strictly to loopback (127.0.0.1).
3. Verifies /health/live endpoint.
4. Launches an independent standalone application window (Edge App Mode or fallback).
5. Monitors window lifecycle and performs clean shutdown upon exit.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request


def find_free_port(preferred: int = 8088) -> int:
    """Check if preferred port is free; otherwise find an ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file with the stdlib only.

    Comments (starting with '#'), blank lines and entries without '=' are
    ignored; optional surrounding quotes are stripped. This mirrors the
    subset of dotenv syntax the backend's pydantic Settings relies on.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def require_desktop_secrets(env: dict[str, str], env_file: Path) -> None:
    """Resolve FY_SESSION_SECRET / FY_LOCAL_TOKEN from environment or .env.

    P1-14: no hardcoded fallback. The previous built-in defaults shipped
    guessable credentials and, worse, shadowed the user's own .env values
    (process env vars take precedence over env_file in pydantic Settings).
    Missing values abort startup with configuration instructions instead.
    """
    file_values = load_env_file(env_file)
    problems: list[str] = []
    session_secret = env.get("FY_SESSION_SECRET") or file_values.get("FY_SESSION_SECRET") or ""
    local_token = env.get("FY_LOCAL_TOKEN") or file_values.get("FY_LOCAL_TOKEN") or ""
    if not session_secret:
        problems.append(
            "FY_SESSION_SECRET is not configured.\n"
            "  Set it as an environment variable or add it to\n"
            f"  {env_file}:\n"
            "      FY_SESSION_SECRET=<at least 32 random characters>\n"
            '  Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    elif len(session_secret) < 32:
        problems.append(
            "FY_SESSION_SECRET is shorter than 32 characters; the backend\n"
            "  rejects it. Replace it with at least 32 random characters in\n"
            f"  the environment or {env_file}."
        )
    if not local_token:
        problems.append(
            "FY_LOCAL_TOKEN is not configured; the desktop login window\n"
            "  cannot authenticate without it.\n"
            "  Set it as an environment variable or add it to\n"
            f"  {env_file}:\n"
            "      FY_LOCAL_TOKEN=<any long random string>\n"
            '  Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if problems:
        print("[FindYourself Error] Missing required secret configuration:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("[FindYourself Error] Startup aborted; no insecure default is applied.", file=sys.stderr)
        raise SystemExit(2)


def find_edge_path() -> Path | None:
    """Find Microsoft Edge executable for Standalone App Mode."""
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Yourself Desktop Application")
    parser.add_argument("--port", type=int, default=8088, help="Port to bind backend server")
    parser.add_argument("--no-window", action="store_true", help="Run backend service only")
    parser.add_argument("--window-cmd", type=str, default="", help="Custom window executable or command (for testing/custom shell)")
    parser.add_argument("--app-root", type=str, default="", help="Application root directory")
    parser.add_argument("--data-dir", type=str, default="", help="Persistent SQLite database directory")
    args = parser.parse_args()

    app_root = Path(args.app_root or Path(__file__).resolve().parent)
    data_dir = Path(args.data_dir or (Path(os.environ.get("LOCALAPPDATA", str(app_root))) / "FindYourself" / "data"))
    run_dir = Path(os.environ.get("LOCALAPPDATA", str(app_root))) / "FindYourself" / "run"

    data_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "find-yourself.db"
    pid_file = run_dir / "findyourself.pid"

    port = find_free_port(args.port)
    print(f"[FindYourself Desktop] Initializing on 127.0.0.1:{port}...")
    print(f"[FindYourself Desktop] Database: {db_path}")

    project_root = Path(__file__).resolve().parents[2]

    # Set local environment variables
    env = os.environ.copy()
    env["FY_ENVIRONMENT"] = "local"
    env["FY_OFFLINE_MODE"] = "1"
    env["FY_LOCAL_ONLY"] = "1"
    # Secrets (P1-14): must come from the environment or the project .env.
    # They are not injected here — the backend's pydantic Settings loads the
    # same .env (uvicorn runs with cwd=project_root) and process env vars
    # keep their natural precedence over env_file values.
    require_desktop_secrets(env, project_root / ".env")
    env["FY_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["FY_PUBLIC_URL"] = f"http://127.0.0.1:{port}"

    # Locate static assets if built
    dist_dir = project_root / "web" / "dist"
    if dist_dir.exists():
        env["FY_STATIC_DIR"] = str(dist_dir)

    # Launch uvicorn process
    cmd = [
        sys.executable,
        "-m", "uvicorn",
        "find_yourself.api.app:create_app",
        "--factory",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning",
    ]

    server_proc = subprocess.Popen(cmd, env=env, cwd=str(project_root))
    pid_file.write_text(str(server_proc.pid), encoding="ascii")

    # Poll health
    health_url = f"http://127.0.0.1:{port}/health/live"
    healthy = False
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if server_proc.poll() is not None:
            print(f"[FindYourself Error] Server process exited with code {server_proc.returncode}")
            return 1
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as resp:
                if resp.status == 200:
                    healthy = True
                    break
        except Exception:
            time.sleep(0.3)

    if not healthy:
        print("[FindYourself Error] Server startup timed out.")
        server_proc.kill()
        return 1

    print(f"[FindYourself Desktop] Service ready at http://127.0.0.1:{port}")

    # Launch standalone application window
    window_proc = None
    if not args.no_window:
        if args.window_cmd:
            print(f"[FindYourself Desktop] Opening test window command: {args.window_cmd}...")
            window_proc = subprocess.Popen(args.window_cmd, shell=True)
        else:
            edge_path = find_edge_path()
            webview_dir = run_dir / "webview-profile"
            webview_dir.mkdir(parents=True, exist_ok=True)

            if edge_path:
                print(f"[FindYourself Desktop] Opening standalone application window via Edge App mode...")
                win_cmd = [
                    str(edge_path),
                    f"--app=http://127.0.0.1:{port}",
                    "--window-size=1280,840",
                    f"--user-data-dir={webview_dir}",
                    # Keep the app window the ONLY window: without these Edge
                    # shows the first-run "OneTab" page and/or lingers in
                    # background mode after the app window is closed, so the
                    # window-close -> service-shutdown lifecycle never fires.
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-mode",
                ]
                # Optional extra Edge flags (e.g. --remote-debugging-port so
                # acceptance automation can drive the *real* window). Off by default.
                extra = os.environ.get("FY_EDGE_EXTRA_ARGS", "").strip()
                if extra:
                    win_cmd.extend(extra.split())
                window_proc = subprocess.Popen(win_cmd)
            else:
                print("[FindYourself Desktop] Edge runtime not found, opening default browser as fallback...")
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{port}")

    print("[FindYourself Desktop] Running. Close window or press Ctrl+C to exit.")
    win_pid_file = run_dir / "window.pid"
    if window_proc:
        try:
            win_pid_file.write_text(str(window_proc.pid), encoding="ascii")
            print(f"[FindYourself Desktop] Window process PID: {window_proc.pid}")
        except Exception:
            pass

    try:
        if window_proc:
            # Wait for user to close the application window
            window_proc.wait()
            print("[FindYourself Desktop] Application window closed by user.")
        else:
            # Wait for server or keyboard interrupt
            server_proc.wait()
    except KeyboardInterrupt:
        print("\n[FindYourself Desktop] Stopping service...")
    finally:
        if win_pid_file.exists():
            win_pid_file.unlink(missing_ok=True)
        if server_proc.poll() is None:
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(server_proc.pid)],
                        capture_output=True,
                        check=False,
                    )
                except Exception:
                    pass
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                server_proc.kill()
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
        print("[FindYourself Desktop] Service stopped cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
