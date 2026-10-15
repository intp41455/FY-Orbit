"""GA-02 响应字段名统一门禁：列表端点形状冻结 + 禁止新增分叉。

目标形状：``{"items": [...], "total": N}``。存量 `"count"` 站点已登记在
BASELINE（逐步迁移，不搞大爆炸重命名）；**新增** `"count"` 返回或
`.count` 读取即 exit 1（::error:: 点名文件与行号）。

另：新增 GET 路由不得返回无类型裸 dict（必须 response_model；
14 个历史例外见 UNPREFIXED_ALLOWLIST：流/回调/跳转/指标/raw 文件）。

用法：python scripts/ga02_response_shape.py [--root DIR]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

#: 存量 `"count": len(` 站点所在文件（逐文件冻结；迁移一个删一项）。
#: 新文件出现该惯用法即红。2026-10-16 校准。
COUNT_BASELINE = {
    "src/find_yourself/api/routes/agent_dispatch.py",
    "src/find_yourself/api/routes/agent_teams.py",
    "src/find_yourself/api/routes/artifact_gate.py",
    "src/find_yourself/api/routes/assets.py",
    "src/find_yourself/api/routes/auth.py",
    "src/find_yourself/api/routes/cabin.py",
    "src/find_yourself/api/routes/canvas.py",
    "src/find_yourself/api/routes/collaboration.py",
    "src/find_yourself/api/routes/hitl.py",
    "src/find_yourself/api/routes/hub.py",
    "src/find_yourself/api/routes/knowledge.py",
    "src/find_yourself/api/routes/memory.py",
    "src/find_yourself/api/routes/profiles.py",
    "src/find_yourself/api/routes/review.py",
    "src/find_yourself/api/routes/runtime.py",
    "src/find_yourself/api/routes/session_state.py",
    "src/find_yourself/api/routes/stash.py",
    "src/find_yourself/api/routes/streaming.py",
    "src/find_yourself/api/routes/team_approval.py",
    "src/find_yourself/api/routes/tools.py",
    "src/find_yourself/api/routes/workbench.py",
}

COUNT_RETURN_RE = re.compile(r'''"count"\s*:\s*len\s*\(''')
COUNT_READ_RE = re.compile(r"""\.count\b""")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    problems: list[str] = []

    routes_dir = root / "src" / "find_yourself" / "api" / "routes"
    if not routes_dir.is_dir():
        print("::error::GA-02 routes 目录缺失，拒绝假绿")
        return 1
    for path in sorted(routes_dir.glob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if COUNT_RETURN_RE.search(line) and rel not in COUNT_BASELINE:
                problems.append(f"{rel}:{i} 新增 `count` 返回（列表统一用 total）")

    api_dir = root / "web" / "src" / "api"
    if api_dir.is_dir():
        for path in sorted(api_dir.glob("*.ts")):
            if path.name.endswith(".test.ts"):
                continue
            rel = path.relative_to(root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if COUNT_READ_RE.search(line) and "total" not in line:
                    problems.append(f"{rel}:{i} 新增 `.count` 读取（列表统一读 total）")

    if problems:
        for p in problems:
            print(f"::error::GA-02 {p}")
        return 1
    print("GA-02 通过：无新增 count 分叉（存量基线外）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
