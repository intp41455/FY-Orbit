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

    # Set local environment variables
    env = os.environ.copy()
    env["FY_ENVIRONMENT"] = "local"
    env["FY_OFFLINE_MODE"] = "1"
    env["FY_LOCAL_ONLY"] = "1"
    env["FY_SESSION_SECRET"] = env.get("FY_SESSION_SECRET", "desktop-session-secret-local-32chars-min-key!")
    env["FY_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["FY_PUBLIC_URL"] = f"http://127.0.0.1:{port}"

    # Locate static assets if built
    project_root = Path(__file__).resolve().parents[2]
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
                ]
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
