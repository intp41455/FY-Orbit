"""Count API paths, operations, and domain modules (D-1 verdict method).

本仓 FastAPI 的 ``include_router`` 是懒注册：``app.routes`` 只吐少量直挂
路由 + ``_IncludedRouter`` 占位，直接数 ``app.routes`` 会得到假数字
（旧脚本曾报出 paths=1）。**必须用 ``app.openapi()['paths']`` 统计**，
见看板 §3.32.7 D-1。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from find_yourself.api.main import app

paths: dict = app.openapi().get("paths", {})
ops = sum(len(methods) for methods in paths.values())

# Domain modules = first segment after /api/
domains: dict[str, int] = {}
for p in paths:
    parts = p.split("/")
    if len(parts) > 2 and parts[1] == "api":
        seg = parts[2]
        domains[seg] = domains.get(seg, 0) + 1

print(f"paths={len(paths)}")
print(f"operations={ops}")
print(f"domain_modules={len(domains)}")

print(f"\nDomains ({len(domains)}):")
for d in sorted(domains):
    print(f"  /api/{d} ({domains[d]} paths)")
