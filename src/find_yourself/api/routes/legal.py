"""Legal notice (NEW-2 NOTICE模块, FROZEN_CONTRACT §11 诚实披露).

``GET /api/legal/notice`` 公开下发（登录页即可查看，无需身份）：
* ``notice_text`` —— 仓库根 ``NOTICE`` 文件**逐字原文**（请求时读盘，
  不在代码里维护第二份会漂移的副本；文件缺失时诚实 503 而非编造文本）；
* ``third_party`` —— 后端/前端依赖名分别解析自 ``pyproject.toml`` 与
  ``web/package.json``（只取清单声明，不解析 lock，不宣称精确版本台账）。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/legal", tags=["legal"])

#: ``src/find_yourself/api/routes/legal.py`` → 上 4 级即仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[4]

_LICENSE_ID = "BSL-1.1"
_LICENSE_HOLDER = "intp41455"

_DEP_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _dep_name(spec: str) -> str:
    """PEP 508 依赖声明取包名（去版本约束/extras/markers）。"""
    head = spec.split(";")[0].split("[")[0]
    m = _DEP_RE.match(head.strip())
    return m.group(1).lower() if m else head.strip().lower()


def _backend_deps(root: Path) -> tuple[list[str], str]:
    manifest = root / "pyproject.toml"
    if not manifest.exists():
        return [], "pyproject.toml (missing)"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return [], "pyproject.toml (unreadable)"
    deps = (data.get("project") or {}).get("dependencies") or []
    names = sorted({_dep_name(str(d)) for d in deps if str(d).strip()})
    return names, "pyproject.toml"


def _frontend_deps(root: Path) -> tuple[list[str], str]:
    manifest = root / "web" / "package.json"
    if not manifest.exists():
        return [], "web/package.json (missing)"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "web/package.json (unreadable)"
    deps = data.get("dependencies") or {}
    return sorted(deps.keys()), "web/package.json"


@router.get("/notice")
async def legal_notice() -> JSONResponse:
    notice_file = _REPO_ROOT / "NOTICE"
    if not notice_file.exists():
        return JSONResponse(
            {"error": {"code": "notice_missing",
                       "message": "NOTICE file is missing from the distribution"}},
            status_code=503,
        )
    backend, backend_src = _backend_deps(_REPO_ROOT)
    frontend, frontend_src = _frontend_deps(_REPO_ROOT)
    return JSONResponse({
        "license": {
            "id": _LICENSE_ID,
            "holder": _LICENSE_HOLDER,
            "change_rule": "各版本首次发布后 4 年自动转为 Apache License 2.0",
            "license_file": "LICENSE",
            "notice_file": "NOTICE",
        },
        "notice_text": notice_file.read_text(encoding="utf-8"),
        "third_party": {
            "backend": backend,
            "backend_source": backend_src,
            "frontend": frontend,
            "frontend_source": frontend_src,
        },
    })
