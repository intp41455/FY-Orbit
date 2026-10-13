"""基座门禁的**反向验证**：证明 `run_base_contract_gate.py` 真的会红。

为什么需要这个文件
------------------
「CI 里接了一个门禁」很容易，「CI 里接了一个**会失败**的门禁」才是需求。
本次接门禁时当前基线恰好有 1 个可编辑页面未接入基座（FortunePage.tsx），
门禁会如实报红 —— 但这只能证明「今天它是红的」，证明不了**它以后还能红**：
一旦有人给脚本加上 `|| true`、把退出码换成警告、或者把扫描面写错成空目录，
它就会变成一个永远绿的装饰品，而 CI 不会给出任何信号。

所以这里不看真实仓库的绿红（那随接线进度变化，是业务事实），
而是**造三棵临时树**分别喂给门禁，断言它的退出码：

| 场景 | 构造 | 期望 |
|---|---|---|
| 契约被破坏 | 一个可编辑但未声明基座的页面 | **exit 1** + `::error::` |
| 契约完好 | 一个已 `useBase()` 且四项能力齐全的页面 | exit 0 |
| 扫描面缺失 | 连 `web/src/pages` 都没有 | **exit 1**（不得假绿） |

第三条最容易被忽略：目录改名 / checkout 不全时扫描到 0 个文件，
「0 违规」在旧实现里会被当成「通过」——门禁恰好在最需要它的时刻消失。

跑法：以子进程调用真实脚本（不是 import 后调函数），因为**退出码才是 CI 看到的
那一份契约**；在进程内调 `main()` 只能证明返回值，证明不了 `sys.exit` 与
`set -e` 下的实际表现。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "scripts" / "p0" / "run_base_contract_gate.py"

#: 已按契约接入基座、且四项能力齐全的页面（shorthand：不写 capabilities）。
DECLARED_PAGE = """
import { useBase } from '../hooks/useBase';
export function OkPage() {
  const base = useBase({ surface: 'workbench' });
  return <section>{base.surface}</section>;
}
"""

#: 可编辑（onChange）但**没有**任何基座声明 —— 契约被破坏的典型形态。
UNDECLARED_EDITABLE_PAGE = """
export function BrokenPage() {
  return <input onChange={() => {}} />;
}
"""


def _run_gate(scan_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    # 显式清掉可能从外层继承的扫描面覆盖，保证测的就是 --scan-root。
    env.pop("FY_BASE_SCAN_ROOT", None)
    env.pop("FY_BASE_EXEMPTIONS", None)
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scan-root", str(scan_root)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def _make_page(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def scanable_tree(tmp_path: Path) -> Path:
    """一棵**结构完整**的最小树：两个 DEFAULT_SCAN_DIRS 都在。"""
    for d in ("web/src/pages", "web/src/components"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_gate_script_exists() -> None:
    assert GATE_SCRIPT.is_file(), f"门禁脚本不存在：{GATE_SCRIPT}"


def test_gate_goes_red_when_contract_is_broken(scanable_tree: Path) -> None:
    """可编辑页面未接入基座 → 必须 exit 1 且打出可定位的 ::error::。

    这是「反假绿」的核心断言：若有人把门禁改成永远绿（吞退出码 / 只告警），
    本用例会立刻失败。
    """
    _make_page(scanable_tree, "web/src/pages/BrokenPage.tsx", UNDECLARED_EDITABLE_PAGE)
    _make_page(scanable_tree, "web/src/pages/OkPage.tsx", DECLARED_PAGE)

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "契约被破坏（可编辑页面未接入基座）时门禁必须 exit 1，"
        f"实际 returncode={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "BrokenPage.tsx" in proc.stdout, (
        f"失败输出必须点名具体文件，实际 stdout:\n{proc.stdout}"
    )
    assert "::error::" in proc.stdout, (
        f"失败必须用 ::error:: 输出以便 CI 标注定位，实际 stdout:\n{proc.stdout}"
    )


def test_gate_stays_green_when_contract_is_satisfied(scanable_tree: Path) -> None:
    """契约完好 → 必须 exit 0。

    没有这条，上一条可能被一个「永远 exit 1」的脚本误判为通过验证；
    两条合起来才证明门禁是**有判别力**的，而不是只会红或只会绿。
    """
    _make_page(scanable_tree, "web/src/pages/OkPage.tsx", DECLARED_PAGE)

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 0, (
        "契约完好时门禁必须 exit 0（否则就是只会红，CI 会被无意义地常驻爆红、"
        f"最终被人绕过）\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_gate_goes_red_when_scan_surface_is_missing(tmp_path: Path) -> None:
    """扫描目录不存在 → 必须 exit 1，**不得**因为「0 违规」而假绿。

    目录被改名、checkout 不全、稀疏检出等情况下扫描面会消失；
    此时报「通过」等于门禁在最需要它的时刻自动失效。
    """
    (tmp_path / "web" / "src" / "pages").mkdir(parents=True)
    # 故意不建 web/src/components —— 扫描面残缺。

    proc = _run_gate(tmp_path)

    assert proc.returncode == 1, (
        "扫描面缺失时门禁必须 exit 1（0 违规不等于契约满足），"
        f"实际 returncode={proc.returncode}\nstdout:\n{proc.stdout}"
    )


def test_gate_goes_red_on_empty_scan_surface(scanable_tree: Path) -> None:
    """扫描目录都在但一个 .tsx 都没有 → 同样 exit 1。

    与上一条区分：上一条是目录缺失，这一条是目录在、文件空。
    两者都指向同一个失败模式——「门禁没看到任何东西却报告通过」。
    """
    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "扫描到 0 个文件时门禁必须 exit 1，实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}"
    )


def test_gate_ci_step_exists_and_is_not_softened() -> None:
    """CI 里必须有这一步，且不得被 `|| true` / `continue-on-error` 软化。

    门禁脚本本身再硬，如果 CI 里没人调用它（这正是 P0-B1 的原始缺口），
    等于没有门禁。这条断言让「接入」本身变成可回归的。
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file(), f"CI workflow 不存在：{workflow}"
    text = workflow.read_text(encoding="utf-8")

    assert "run_base_contract_gate.py" in text, (
        "ci.yml 里没有任何地方调用 run_base_contract_gate.py —— "
        "门禁只在人手动跑时存在，CI 从不会因此拦下合并（P0-B1 的原始缺口）。"
    )
    for line in text.splitlines():
        if "run_base_contract_gate.py" not in line:
            continue
        assert "|| true" not in line, f"门禁调用被 `|| true` 软化：{line.strip()}"
        assert "continue-on-error" not in line, (
            f"门禁调用被 continue-on-error 软化：{line.strip()}"
        )
