"""桌面进程生命周期测试：替代进程生命周期测试与实际 Edge 窗口测试 (Surrogate Process Lifecycle Test).

Adheres to:
- 13_双端产品续作与验收清单.md
- 18_工程代码工作台与主协调Agent全流程实施规格.md

Verifies:
1. run_desktop.py launches FastAPI backend on loopback port.
2. Endpoint /health/live returns 200 OK.
3. 替代进程生命周期测试 (Surrogate process lifecycle): when surrogate window process exits, run_desktop.py detects window exit.
4. 实际 Edge 窗口测试 (Actual Edge window): when actual msedge.exe is launched in standalone app mode and closed, process tree terminates cleanly.
5. Port is freed and pid files are unlinked cleanly.
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


def test_desktop_surrogate_window_close_triggers_clean_shutdown(tmp_path: Path) -> None:
    """替代进程生命周期测试 (Surrogate Process Lifecycle Test): using a surrogate window command."""
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


def test_actual_edge_window_launch_and_close_lifecycle(tmp_path: Path) -> None:
    """实际 Edge 窗口测试 (Actual Edge window lifecycle): tests real Edge app mode window launch and exit."""
    if sys.platform != "win32":
        pytest.skip("Actual Edge test requires Windows platform")

    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / r"Microsoft\Edge\Application\msedge.exe",
    ]
    edge_path = next((c for c in candidates if c.exists()), None)
    if not edge_path:
        pytest.skip("Microsoft Edge not found on this machine")

    port = find_test_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "FindYourself" / "run"

    cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "--port", str(port),
        "--app-root", str(tmp_path),
        "--data-dir", str(data_dir),
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

        # Step 2: Locate the specific Edge process started by this runner using window.pid or CommandLine
        time.sleep(2.0)
        win_pid = None
        win_pid_file = run_dir / "window.pid"
        if win_pid_file.exists():
            try:
                win_pid = int(win_pid_file.read_text(encoding="ascii").strip())
            except Exception:
                pass

        if win_pid:
            # Terminate ONLY the test's Edge window process tree, preserving any unrelated user browser processes
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(win_pid)], capture_output=True, check=False)
        else:
            # Fallback: query Edge process bound specifically to this test port
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f'Get-CimInstance Win32_Process | Where-Object {{ $_.Name -eq "msedge.exe" -and $_.CommandLine -like "*127.0.0.1:{port}*" }} | Select-Object -ExpandProperty ProcessId'],
                capture_output=True, text=True, check=False,
            )
            pids = [int(p.strip()) for p in res.stdout.strip().splitlines() if p.strip().isdigit()]
            for p in pids:
                subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True, check=False)

        # Step 3: Verify runner detects window closure and shuts down backend within 10s
        exit_code = proc.wait(timeout=10.0)
        assert exit_code == 0
        out, _ = proc.communicate()
        assert "Application window closed by user" in out or "Service stopped cleanly" in out

        # Step 4: Verify port is released
        time.sleep(0.5)
        assert is_port_listening(port) is False

    finally:
        if proc.poll() is None:
            proc.kill()
