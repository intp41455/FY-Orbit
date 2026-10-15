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
import sys
import tomllib
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/legal", tags=["legal"])

#: ``src/find_yourself/api/routes/legal.py`` → 上 4 级即仓库根（源码运行）。
#: 冻结包（PyInstaller onedir）下 ``__file__`` 指向 _MEI 临时目录，此时以
#: 可执行文件所在包目录（``.../find-yourself-backend/..``）与 cwd 为准。
#: 三处都找不到 NOTICE 即诚实 503（不编造文本）。
_SOURCE_ROOT = Path(__file__).resolve().parents[4]


def _repo_root() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [exe_dir, exe_dir.parent]
    candidates += [_SOURCE_ROOT, Path.cwd()]
    for cand in candidates:
        try:
            if (cand / "NOTICE").is_file():
                return cand
        except OSError:
            continue
    return None

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
    root = _repo_root()
    if root is None:
        return JSONResponse(
            {"error": {"code": "notice_missing",
                       "message": "NOTICE file is missing from the distribution"}},
            status_code=503,
        )
    notice_file = root / "NOTICE"
    backend, backend_src = _backend_deps(root)
    frontend, frontend_src = _frontend_deps(root)
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
