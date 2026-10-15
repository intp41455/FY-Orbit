"""GA-07 死代码门禁：新增未被任何路由/服务调用的模块 → exit 1。

判定「活着」的任一条件（全部命中即活）：
1. 被 src/、tests/、scripts/、desktop/、仓库根 *.py 任一文件静态 import；
2. 是 FastAPI 路由模块（定义 ``router =``，由 routes/__init__ 自动发现挂载）；
3. 有 ``if __name__ == "__main__"`` 入口（cli / 可执行脚本模块）；
4. 被 importlib 字符串引用（扫描全部 ``import_module("...")`` 字面量）；
5. 在 migrations/versions、alembic env、pyproject [project.scripts] 入口中被引用。

用法：python scripts/ga07_dead_code.py [--root DIR]
退出码：0 全活；1 列出死模块（::error:: 逐个点名）。
"""
from __future__ import annotations

import argparse
import ast
import re
import warnings
from pathlib import Path

IMPORT_MODULE_RE = re.compile(r"""import_module\(\s*['"]([^'"]+)['"]""")


def module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    return ".".join(rel.parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    src = root / "src"
    if not src.is_dir():
        print("::error::src/ 目录不存在，拒绝假绿")
        return 1

    py_files = [p for p in src.rglob("*.py") if "__pycache__" not in p.parts]
    if not py_files:
        print("::error::src/ 下扫到 0 个 .py 文件，拒绝假绿")
        return 1
    mods = {module_name(src, p): p for p in py_files}

    importers = (
        list(src.rglob("*.py"))
        + list((root / "tests").rglob("*.py") if (root / "tests").is_dir() else [])
        + list((root / "scripts").rglob("*.py") if (root / "scripts").is_dir() else [])
        + list((root / "desktop").rglob("*.py") if (root / "desktop").is_dir() else [])
        + list(root.glob("*.py"))
    )

    imported: set[str] = set()
    dyn_strings: set[str] = set()
    mains: set[str] = set()
    routers: set[str] = set()
    for path in importers:
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            with warnings.catch_warnings():
                # 被扫文件的非法转义是它们的告警，不是本门禁的输出。
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        try:
            modname = module_name(src, path) if path.is_relative_to(src) else None
        except ValueError:
            modname = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    base = node.module
                    if node.level and modname:
                        parts = modname.split(".")
                        base_pkg = ".".join(parts[: len(parts) - node.level]) \
                            if node.level <= len(parts) else ""
                        base = (base_pkg + "." + node.module).strip(".")
                    imported.add(base)
                    for a in node.names:
                        imported.add((base + "." + a.name).strip("."))
                elif modname and node.level:
                    parts = modname.split(".")
                    base = ".".join(parts[: len(parts) - node.level]) \
                        if node.level <= len(parts) else ""
                    for a in node.names:
                        imported.add((base + "." + a.name).strip("."))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    imported.add(a.name)
        for m in IMPORT_MODULE_RE.findall(text):
            dyn_strings.add(m)
        if '__name__' in text and '__main__' in text:
            if modname:
                mains.add(modname)
        if re.search(r"^router\s*[:=]", text, re.M):
            if modname:
                routers.add(modname)

    # pyproject scripts 入口
    try:
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        for m in re.findall(r"=\s*[\"']([\w.]+):", pyproject):
            dyn_strings.add(m.rsplit(":", 1)[0] if ":" in m else m)
    except OSError:
        pass

    dead: list[str] = []
    for name, path in sorted(mods.items()):
        if name.endswith(".__init__"):
            continue
        short = name.split(".")[-1]
        hit = (
            name in mains
            or name in routers
            or name in dyn_strings
            or any(
                i == name or i.startswith(name + ".") or i.endswith("." + short)
                for i in imported | dyn_strings
            )
        )
        if not hit:
            # migrations 脚本由 alembic 按 revision 寻址，不走 import。
            if ".migrations." in name or name.endswith(".migrations"):
                continue
            dead.append(name)

    if dead:
        for d in dead:
            print(f"::error::GA-07 死模块：{d}（无任何路由/服务/测试/入口引用）")
        return 1
    print(f"GA-07 通过：{len(mods)} 个模块全部有引用（scripts/tests/desktop/入口/路由自动发现均计入）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
