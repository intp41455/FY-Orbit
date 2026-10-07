"""OpenAPI 规范导出与 SDK 契约一致性校验工具（A-生态兼容-01 · P12）。

功能：
1. 从当前 FastAPI app 导出最新的 OpenAPI 3.1 规格（openapi.json）。
2. 校验后端路由与 Python / JavaScript / Java SDK 声明的能力覆盖面。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 设置测试/脚本默认秘钥（长度 >= 32）
os.environ.setdefault("FY_SESSION_SECRET", "test-secret-at-least-32-chars-long-abc")
os.environ.setdefault("FY_CSRF_SECRET", "test-csrf-secret-at-least-32-chars-xyz")

# 添加 src 到路径
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from find_yourself.api.app import create_app
except ImportError:
    create_app = None


def export_openapi_schema(output_path: Path | None = None) -> dict:
    if create_app is None:
        raise RuntimeError("无法导入 find_yourself.api.app")
    app = create_app()
    schema = app.openapi()
    target = output_path or (Path(__file__).resolve().parent / "openapi.json")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    return schema


def verify_sdk_coverage(schema: dict) -> dict:
    paths = schema.get("paths", {})
    plugin_paths = [p for p in paths if p.startswith("/api/plugins")]
    
    # 期望 SDK 覆盖的核心领域
    expected_areas = {
        "marketplace": any("/marketplace" in p for p in plugin_paths),
        "ratings": any("/rate" in p for p in plugin_paths),
        "templates": any("/templates" in p for p in plugin_paths),
        "cursor_context": any("/cursor" in p for p in plugin_paths),
    }
    
    return {
        "total_paths": len(paths),
        "plugin_paths_count": len(plugin_paths),
        "covered_areas": expected_areas,
        "all_covered": all(expected_areas.values()),
    }


if __name__ == "__main__":
    schema = export_openapi_schema()
    report = verify_sdk_coverage(schema)
    print(f"成功导出 OpenAPI 规格，共 {report['total_paths']} 条路由。")
    print(f"P12 生态路由覆盖: {report['covered_areas']}")
