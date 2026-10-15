"""NEW-2 收尾：NOTICE 内嵌的第三方清单必须与锁文件一致（防漂移门禁）。

跑法：子进程调用真实脚本（退出码才是 CI 看到的那一份），与
``test_layering_gate.py`` 同口径。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "gen_third_party_notices.py"


def test_notice_third_party_block_matches_lockfiles() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "后端 25 + 前端 6" in proc.stdout
