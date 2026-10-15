"""426覆盖率校验（看板 D-1）：README 宣称的接口数不得高于实测（不得注水）。

实测方法与 ``_count_api.py`` 一致：``app.openapi()['paths']``
（``include_router`` 是懒注册，直接数 ``app.routes`` 会得到假数字）。
本测试锁死"删路由不断言"的退化：实测低于宣称即红。新增路由后请用
``_count_api.py`` 重测并同步刷新 README 数字。
"""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "README.md").exists() and (parent / "_count_api.py").exists():
            return parent
    raise AssertionError("repo root not found")


def test_readme_api_counts_do_not_exceed_measured(app) -> None:
    paths = app.openapi().get("paths", {})
    measured_paths = len(paths)
    measured_ops = sum(len(methods) for methods in paths.values())
    assert measured_paths > 400, f"openapi 路径数异常偏低: {measured_paths}"
    text = (_repo_root() / "README.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+) 个接口路径\s*/\s*(\d+) 个接口操作", text)
    assert m is not None, "README 接口数声明行丢失"
    claimed_paths, claimed_ops = int(m.group(1)), int(m.group(2))
    assert measured_paths >= claimed_paths, (
        f"README 宣称 {claimed_paths} 路径，实测仅 {measured_paths}：声明注水或路由丢失")
    assert measured_ops >= claimed_ops, (
        f"README 宣称 {claimed_ops} 操作，实测仅 {measured_ops}：声明注水或路由丢失")
