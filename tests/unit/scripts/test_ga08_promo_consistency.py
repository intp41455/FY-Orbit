"""GA-08 Self-Test：页面调用字面量必须命中 openapi；注释里的示例路径不算数。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ga08_promo_consistency.py"

sys.path.insert(0, str(ROOT / "scripts"))

SPEC = {"paths": {"/api/things": {"get": {"responses": {"200": {"description": "ok"}}}}}}


def _tree(tmp: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def test_gate_red_on_missing_route(tmp_path, monkeypatch) -> None:
    """页面调了 openapi 没有的路由 → 红。openapi 构建太重，这里 monkeypatch 掉。"""
    import scripts.ga08_promo_consistency as gate
    _tree(tmp_path, {
        "web/src/pages/Foo.tsx": "fetch('/api/nope');\n",
    })
    monkeypatch.setattr(gate, "load_openapi", lambda root: SPEC)
    assert gate.main(["--root", str(tmp_path)]) == 1


def test_gate_ignores_comment_examples(tmp_path, monkeypatch) -> None:
    import scripts.ga08_promo_consistency as gate
    _tree(tmp_path, {
        # 注释里的示例路径不得触发；真实调用命中 openapi。
        "web/src/pages/Foo.tsx": "// 例如 GET /api/kb/documents\nfetch('/api/things');\n",
    })
    monkeypatch.setattr(gate, "load_openapi", lambda root: SPEC)
    assert gate.main(["--root", str(tmp_path)]) == 0


def test_gate_passes_on_real_tree() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"] if False else [sys.executable, str(SCRIPT)],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout
