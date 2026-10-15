"""GA-08 UI 宣传与实现一致性（并入 W1 基座门禁族，校验对象不同）。

页面/组件里写死的 `/api/...` 调用字面量必须在 openapi 有对应路由
（GA-01 只扫 web/src/api，这里扫 pages + components —— 宣传了就必须有端点）。
用法与退出码同 GA-01。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ga01_contract import API_LITERAL_RE, _code_only, _match, load_openapi  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    spec = load_openapi(root)
    templates = [p for p in spec.get("paths", {})]
    bad: list[str] = []
    for sub in ("pages", "components"):
        d = root / "web" / "src" / sub
        if not d.is_dir():
            continue
        for ts in sorted(d.rglob("*.tsx")) + sorted(d.rglob("*.ts")):
            if ts.name.endswith(".test.ts") or ts.name.endswith(".test.tsx"):
                continue
            text = _code_only(ts.read_text(encoding="utf-8"))
            bases = dict(re.findall(
                r"""const\s+(\w+)\s*=\s*['"](/api/[^'""]*)['"]""", text))
            for var, val in bases.items():
                text = text.replace("${" + var + "}", val)
            lits = set()
            for lit in API_LITERAL_RE.findall(text):
                lit = re.sub(r"\?\$\{[^}]*\}$", "", lit).split("?")[0]
                lits.add(lit.rstrip("/") or "/")
            calls = {x for x in lits
                     if not any(o != x and o.startswith(x + "/") for o in lits)}
            rel = ts.relative_to(root).as_posix()
            for lit in sorted(calls):
                variants = [lit]
                m = re.match(r"^(.*)\$\{[^}]*\}$", lit)
                if m:
                    variants.append(m.group(1).rstrip("/") or "/")
                normed = [re.sub(r"\$\{[^}]*\}", "{p}", v) for v in variants]
                if not any(_match(n, templates) is not None for n in normed):
                    bad.append(f"{rel}：{lit}")
    if bad:
        for b in bad:
            print(f"::error::GA-08 {b} 在 openapi 无对应路由（宣传了不存在的端点）")
        return 1
    print("GA-08 通过：页面/组件调用字面量全部命中 openapi。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
