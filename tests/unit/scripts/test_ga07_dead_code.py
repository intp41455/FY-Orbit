"""GA-07 Self-Test：证明死代码门禁真会红，且有判别力。

跑法与 test_layering_gate.py 同口径：子进程调真实脚本，断言退出码。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ga07_dead_code.py"


def _run(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd,
        capture_output=True, text=True, timeout=120,
    )


def _tree(files: dict[str, str], tmp_path: Path) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_gate_red_on_orphan(tmp_path) -> None:
    _tree({
        "src/pkg/__init__.py": "",
        "src/pkg/used.py": "VALUE = 1\n",
        "src/pkg/dead.py": "VALUE = 2\n",
        "src/pkg/main.py": "from pkg.used import VALUE\nprint(VALUE)\n",
    }, tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "::error::" in proc.stdout
    assert "dead" in proc.stdout


def test_gate_green_when_all_referenced(tmp_path) -> None:
    _tree({
        "src/pkg/__init__.py": "",
        "src/pkg/used.py": "VALUE = 1\n",
        "src/pkg/main.py": "from pkg.used import VALUE\nprint(VALUE)\n",
        "tests/test_smoke.py": "import pkg.main\n",
    }, tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_gate_green_for_router_and_main_entry(tmp_path) -> None:
    _tree({
        "src/pkg/__init__.py": "",
        "src/pkg/routes.py": "router = object()\n",
        "src/pkg/cli.py": 'if __name__ == "__main__":\n    print("hi")\n',
    }, tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_gate_refuses_fake_green_without_src(tmp_path) -> None:
    proc = _run(tmp_path)
    assert proc.returncode == 1


def test_gate_passes_on_real_tree() -> None:
    proc = _run(ROOT)
    assert proc.returncode == 0, proc.stdout
