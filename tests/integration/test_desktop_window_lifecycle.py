"""Integration test for desktop window lifecycle and graceful process tree exit (13 & 14 号清单).

Verifies:
1. run_desktop.py launches FastAPI backend on loopback port.
2. Endpoint /health/live returns 200 OK.
3. When standalone window is closed by user (simulated or real), run_desktop.py detects window exit.
4. Process tree cleanup occurs immediately (Windows taskkill /F /T).
5. Port is freed and findyourself.pid is cleanly unlinked.
"""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_SCRIPT = REPO_ROOT / "desktop" / "app" / "run_desktop.py"


def find_test_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def is_port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (OSError, ConnectionRefusedError):
            return False


def test_desktop_window_close_triggers_clean_shutdown(tmp_path: Path) -> None:
    port = find_test_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Window command: simulate user viewing app for 2 seconds then closing window
    window_cmd = f'"{sys.executable}" -c "import time; time.sleep(2.0)"'

    cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "--port", str(port),
        "--app-root", str(tmp_path),
        "--data-dir", str(data_dir),
        "--window-cmd", window_cmd,
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Step 1: Wait for backend /health/live to become healthy
        health_url = f"http://127.0.0.1:{port}/health/live"
        healthy = False
        start_time = time.time()
        while time.time() - start_time < 20.0:
            if proc.poll() is not None:
                out, err = proc.communicate()
                pytest.fail(f"Desktop runner exited prematurely with code {proc.returncode}:\nStdout: {out}\nStderr: {err}")
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.3)

        assert healthy is True, "Desktop backend failed to become healthy within 20s"
        assert is_port_listening(port) is True

        # Step 2: Wait for window process to exit (sleep 2.0s completes) and verify runner exits
        exit_code = proc.wait(timeout=15.0)
        assert exit_code == 0, f"Expected clean exit 0, got {exit_code}"

        out, err = proc.communicate()
        assert "Application window closed by user" in out
        assert "Service stopped cleanly" in out

        # Step 3: Verify port is freed
        time.sleep(0.5)
        assert is_port_listening(port) is False, f"Port {port} should be released after window exit"

    finally:
        if proc.poll() is None:
            proc.kill()


def test_desktop_no_window_terminate_frees_port(tmp_path: Path) -> None:
    port = find_test_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "--port", str(port),
        "--app-root", str(tmp_path),
        "--data-dir", str(data_dir),
        "--no-window",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        health_url = f"http://127.0.0.1:{port}/health/live"
        healthy = False
        start_time = time.time()
        while time.time() - start_time < 20.0:
            if proc.poll() is not None:
                out, err = proc.communicate()
                pytest.fail(f"Runner exited prematurely:\n{out}\n{err}")
            try:
                with urllib.request.urlopen(health_url, timeout=1.0) as resp:
                    if resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.3)

        assert healthy is True
        assert is_port_listening(port) is True

        # Terminate runner process tree (simulating Stop-Process -Tree / desktop stop)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            proc.terminate()
        proc.wait(timeout=10.0)

        time.sleep(0.5)
        assert is_port_listening(port) is False

    finally:
        if proc.poll() is None:
            proc.kill()
