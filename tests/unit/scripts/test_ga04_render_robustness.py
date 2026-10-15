"""GA-04 Self-Test：渲染健壮性门禁 = tsconfig 约束 + tsc 全绿。

门禁本体是 `web/tsconfig.json` 的 `noUncheckedIndexedAccess`（CI 的
Frontend Typecheck job 跑 `tsc --noEmit`，违例即红）。
本文件锁死该约束存在（防有人偷偷关掉），判别力由 tsc 本身保证。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_no_unchecked_indexed_access_enabled() -> None:
    cfg = json.loads((ROOT / "web" / "tsconfig.json").read_text(encoding="utf-8"))
    assert cfg["compilerOptions"].get("noUncheckedIndexedAccess") is True
