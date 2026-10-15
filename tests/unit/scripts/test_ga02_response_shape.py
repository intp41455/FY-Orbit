"""GA-02 Self-Test：响应形状门禁真会红，且有判别力。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ga02_response_shape.py"

ROUTES = "src/find_yourself/api/routes"


def _run(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd,
        capture_output=True, text=True, timeout=120,
    )


def _tree(files: dict[str, str], tmp_path: Path) -> None:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_gate_red_on_new_count_idiom(tmp_path) -> None:
    _tree({
        f"{ROUTES}/fresh.py": 'def list_all():\n    return {"items": [], "count": len([])}\n',
    }, tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "::error::" in proc.stdout
    assert "fresh.py" in proc.stdout


def test_gate_green_without_count_idiom(tmp_path) -> None:
    _tree({
        f"{ROUTES}/fresh.py": 'def list_all():\n    return {"items": [], "total": 0}\n',
    }, tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_gate_passes_on_real_tree() -> None:
    proc = _run(ROOT)
    assert proc.returncode == 0, proc.stdout
