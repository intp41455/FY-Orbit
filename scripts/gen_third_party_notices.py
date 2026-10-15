"""生成 NOTICE 里的第三方组件清单（NEW-2 收尾）。

单一真源：后端版本取 ``uv.lock``、前端版本取 ``web/package-lock.json``；
许可证标识取自各包发布元数据（后端 2026-10-15 由本机 venv 的
``importlib.metadata`` 逐一核对；前端 6 项均为上游声明的 MIT，
见各包 npm 页面 / 仓库 LICENSE）。

用法：
    python scripts/gen_third_party_notices.py --print   # 输出清单块
    python scripts/gen_third_party_notices.py --check   # 与 NOTICE 内嵌块比对，
                                                       # 不一致 exit 1（CI 漂移门禁）

注意：psycopg 为 LGPL-3.0-only，本项目仅以未修改的运行时库方式经 pip
依赖引用（动态链接），此处履行署名义务；不触发 LGPL 的源码分发条款。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "NOTICE"
BEGIN = "<!-- THIRD-PARTY-BEGIN: 由 scripts/gen_third_party_notices.py 生成，勿手改 -->"
END = "<!-- THIRD-PARTY-END -->"

#: (后端包名, 许可证标识）。标识来自各包的 License-Expression / License 字段。
_BACKEND_LICENSES: tuple[tuple[str, str], ...] = (
    ("fastapi", "MIT"),
    ("uvicorn", "BSD-3-Clause"),
    ("pydantic-settings", "MIT"),
    ("sqlalchemy", "MIT"),
    ("psycopg", "LGPL-3.0-only"),
    ("pgvector", "MIT"),
    ("authlib", "BSD-3-Clause"),
    ("itsdangerous", "BSD-3-Clause"),
    ("httpx", "BSD-3-Clause"),
    ("temporalio", "MIT"),
    ("langgraph", "MIT"),
    ("langchain-openai", "MIT"),
    ("boto3", "Apache-2.0"),
    ("pyyaml", "MIT"),
    ("alembic", "MIT"),
    ("prometheus-client", "Apache-2.0 AND BSD-2-Clause"),
    ("opentelemetry-sdk", "Apache-2.0"),
    ("opentelemetry-exporter-otlp-proto-http", "Apache-2.0"),
    ("cryptography", "Apache-2.0 OR BSD-3-Clause"),
    ("argon2-cffi", "MIT"),
    ("pypdf", "BSD-3-Clause"),
    ("python-docx", "MIT"),
    ("websockets", "BSD-3-Clause"),
    ("sqlite-vec", "MIT AND Apache-2.0"),
    ("apscheduler", "MIT"),
)

#: (前端包名, 许可证标识）。6 项上游均声明 MIT。
_FRONTEND_LICENSES: tuple[tuple[str, str], ...] = (
    ("@pixi/tilemap", "MIT"),
    ("pixi.js", "MIT"),
    ("react", "MIT"),
    ("react-dom", "MIT"),
    ("react-router-dom", "MIT"),
    ("three", "MIT"),
)


_DEP_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def _manifest_backend_names() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = (data.get("project") or {}).get("dependencies") or []
    names = set()
    for spec in deps:
        # PEP 508 取包名：先去 markers/extras，再取首个标识段（与 legal.py 同口径）。
        head = str(spec).split(";")[0].split("[")[0]
        m = _DEP_RE.match(head.strip())
        if m:
            names.add(m.group(1).lower())
    return sorted(names)


def _lock_backend_versions() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {p["name"].lower(): p["version"] for p in lock.get("package", [])}


def _manifest_frontend_names() -> list[str]:
    data = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    return sorted((data.get("dependencies") or {}).keys())


def _lock_frontend_versions() -> dict[str, str]:
    data = json.loads(
        (ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    pkgs = data.get("packages") or {}
    out: dict[str, str] = {}
    for key, val in pkgs.items():
        if key.startswith("node_modules/") and key.count("/") == 1:
            out[key[len("node_modules/"):]] = val.get("version", "?")
        elif key.startswith("node_modules/@") and key.count("/") == 2:
            out[key[len("node_modules/"):]] = val.get("version", "?")
    return out


def render() -> str:
    """渲染清单块（含行尾换行）。缺版本即诚实标 MISSING-LOCK，不编造。"""
    backend_versions = _lock_backend_versions()
    lines = [
        "第三方开源组件清单（直接依赖；版本锁定自 uv.lock / package-lock.json）：",
        "",
        "后端（Python）：",
    ]
    for name, lic in _BACKEND_LICENSES:
        lines.append(f"  - {name} {backend_versions.get(name, 'MISSING-LOCK')} · {lic}")
    frontend_versions = _lock_frontend_versions()
    lines += ["", "前端（npm）："]
    for name, lic in _FRONTEND_LICENSES:
        lines.append(
            f"  - {name} {frontend_versions.get(name, 'MISSING-LOCK')} · {lic}")
    lines += [
        "",
        "注：psycopg（LGPL-3.0-only）仅以未修改的运行时库方式引用，此处署名即履行义务。",
    ]
    return "\n".join(lines) + "\n"


def embedded() -> str | None:
    """取出 NOTICE 内嵌块（无块返回 None）。"""
    text = NOTICE.read_text(encoding="utf-8")
    start = text.find(BEGIN)
    stop = text.find(END)
    if start < 0 or stop < 0 or stop < start:
        return None
    return text[start + len(BEGIN):stop].strip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    block = render()
    if args.print:
        sys.stdout.write(block)
        return 0
    if args.check:
        current = embedded()
        if current is None:
            print("NOTICE 缺少第三方清单块（先用 --print 生成并嵌入）",
                  file=sys.stderr)
            return 1
        if current != block:
            print("NOTICE 第三方清单与锁文件漂移：请重新生成并嵌入",
                  file=sys.stderr)
            return 1
        # 清单必须覆盖清单文件声明的全部直接依赖（防漏项）。
        missing = [n for n in _manifest_backend_names()
                   if n not in {b for b, _ in _BACKEND_LICENSES}]
        missing += [n for n in _manifest_frontend_names()
                    if n not in {f for f, _ in _FRONTEND_LICENSES}]
        if missing:
            print(f"清单漏列直接依赖：{missing}", file=sys.stderr)
            return 1
        print(f"NOTICE 第三方清单一致（后端 {len(_BACKEND_LICENSES)} + "
              f"前端 {len(_FRONTEND_LICENSES)}）")
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
