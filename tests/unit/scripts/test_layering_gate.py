"""分层门禁的**反向验证**：证明 `run_layering_gate.py` 真的会红，而且**有判别力**。

为什么需要这个文件
------------------
「CI 里接了一个门禁」很容易，「CI 里接了一个**会失败**的门禁」才是需求。
更隐蔽的失败模式是**反向的**：一个只会红的脚本（`sys.exit(1)` 写死、或把
`services → runtime` 这种合法方向也当成环）同样会让 CI 一直红，直到被人用
`|| true` 绕过 —— 那时它和永远绿的装饰品一样毫无价值。

所以这里不看真实仓库的绿红（那是随切片推进变化的业务事实），
而是**在临时目录里造若干棵最小树**分别喂给门禁，断言真实子进程的退出码：

| 场景 | 构造 | 期望 |
|---|---|---|
| 门禁脚本存在 | —— | 文件在 |
| L1 反向 import L2（R5） | `db/models.py` → `..services.errors` | **exit 1** + `::error::` + 点名文件 |
| L2 反向 import L3（R6） | `services/x.py` → `...api.routes` | **exit 1** |
| 真的成环（V1） | `runtime/a.py → services/b.py` 且 `services/b.py → runtime/a.py` | **exit 1** 且输出能看到环 |
| **不成环** | 只有单向 `services/b.py → runtime/a.py` | **exit 0**（证明有判别力） |
| **单向 `runtime → services`（D3R）** | `runtime/a.py → services/b.py`（**无反向边**） | **exit 1**（D3 禁止方向，与成环无关） |
| **D3R 不误伤合法方向** | 只有单向 `services/b.py → runtime/a.py` | **exit 0** 且 `D3R` 合计 0 |
| W1 默认 warn | `api/routes/r.py → ...runtime.sse` | exit 0（且输出列出 W1 现状） |
| W1 可切 hard | 同上 + `--w1 hard` | **exit 1** |
| 扫描面缺失 | 不建 `src/find_yourself/runtime` | **exit 1**（不得假绿） |
| 扫到 0 个文件 | 目录都在但无 `.py` | **exit 1** |
| CI 确实调用且未软化 | —— | 断言通过 |

其中「**不成环 → exit 0**」是全部用例里最重要的一条：ADR-0001 D3 明确
`services → runtime` 是**合法方向**，若门禁把它误判成环，就会逼着人去改
100+ 处合法业务代码（而本切片不允许动业务代码）。

跑法：以子进程调用真实脚本（不是 import 后调函数），因为**退出码才是 CI 看到的
那一份契约**；在进程内调 `main()` 只能证明返回值，证明不了 `sys.exit` 与
`set -e` 下的实际表现。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "scripts" / "p0" / "run_layering_gate.py"

#: 门禁赖以判定分层的最小扫描面（对应 three-layer-model.md §9.2 的目录锚点）。
REQUIRED_DIRS = (
    "src/find_yourself/db",
    "src/find_yourself/services",
    "src/find_yourself/runtime",
    "src/find_yourself/api/routes",
)


def _run_gate(scan_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["FY_ENVIRONMENT"] = "test"
    env["FY_OFFLINE_MODE"] = "true"
    # Windows GBK 控制台：子进程 stdout 与本进程解码都强制 UTF-8，
    # 否则门禁输出的「⇄」会在编码层打崩（与脚本内 reconfigure 双保险）。
    env["PYTHONIOENCODING"] = "utf-8"
    # 显式清掉可能从外层继承的覆盖，保证测的就是 --scan-root 与本仓 baseline 无关。
    env.pop("FY_LAYERING_SCAN_ROOT", None)
    env.pop("FY_LAYERING_BASELINE", None)
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--scan-root", str(scan_root), *extra],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def scanable_tree(tmp_path: Path) -> Path:
    """一棵**结构完整**的最小树：§9.2 的层锚点目录都在。"""
    for d in REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_gate_script_exists() -> None:
    assert GATE_SCRIPT.is_file(), f"门禁脚本不存在：{GATE_SCRIPT}"


def test_gate_stays_green_when_tree_is_clean(scanable_tree: Path) -> None:
    """无违规 → 必须 exit 0。

    没有这条，一个「永远 exit 1」的脚本也能通过其余全部红 assertions；
    它和只会红的门禁一样没有价值（CI 会被人加 `|| true` 绕过）。
    """
    _write(scanable_tree, "src/find_yourself/config.py", "TIMEOUT = 30\n")
    _write(scanable_tree, "src/find_yourself/db/models.py", "class Row:  # L1 自守\n    pass\n")
    _write(scanable_tree, "src/find_yourself/runtime/kernel.py", "def run() -> None:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/api/routes/tasks.py",
           "from ...services.tasks import run_task  # R1: L3 -> L2 合法\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 0, (
        "无违规时门禁必须 exit 0（否则就是只会红，CI 会被无意义地常驻爆红、"
        f"最终被人绕过）\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_gate_goes_red_when_l1_imports_l2(scanable_tree: Path) -> None:
    """L1 反向 import L2（R5）→ 必须 exit 1 且 `::error::` 点名文件。"""
    _write(scanable_tree, "src/find_yourself/db/models.py",
           "from ..services.errors import DomainError  # R5 违规\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "L1 反向 import L2 必须 exit 1，实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, f"必须用 ::error:: 输出，实际 stdout:\n{proc.stdout}"
    assert "db/models.py" in proc.stdout, (
        f"失败输出必须点名具体文件，实际 stdout:\n{proc.stdout}"
    )
    assert "R5" in proc.stdout, f"必须标明触发的规则编号，实际 stdout:\n{proc.stdout}"


def test_gate_goes_red_when_l2_imports_l3(scanable_tree: Path) -> None:
    """L2 反向 import L3（R6，`services/x.py` → `...api.routes`）→ 必须 exit 1。"""
    _write(scanable_tree, "src/find_yourself/api/routes/tasks.py",
           "def run() -> None:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/x.py",
           "from ..api.routes.tasks import run  # R6 违规：L2 -> L3\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "L2 反向 import L3 必须 exit 1，实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, f"必须用 ::error:: 输出，实际 stdout:\n{proc.stdout}"
    assert "services/x.py" in proc.stdout, (
        f"失败输出必须点名具体文件，实际 stdout:\n{proc.stdout}"
    )
    assert "R6" in proc.stdout, f"必须标明触发的规则编号，实际 stdout:\n{proc.stdout}"


def test_gate_detects_real_cycle(scanable_tree: Path) -> None:
    """真成环：`runtime/a.py → services/b.py` 且 `services/b.py → runtime/a.py`。

    这是 V1 的最小复现。**双向边都存在才叫环**，门禁必须真的走图找到环，
    而不是看到 `runtime`/`services` 互相 import 就报红。
    """
    _write(scanable_tree, "src/find_yourself/runtime/a.py",
           "from ..services.b import B  # runtime -> services\n\n\nclass A:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/b.py",
           "from ..runtime.a import A  # services -> runtime\n\n\nclass B:\n    pass\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "runtime ⇄ services 互相依赖形成真环时必须 exit 1，实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, f"环必须用 ::error:: 输出，实际 stdout:\n{proc.stdout}"
    assert "services/b.py" in proc.stdout and "runtime/a.py" in proc.stdout, (
        "环输出必须点名成环的两个模块，实际 stdout:\n{proc.stdout}"
    )
    assert "V1" in proc.stdout, f"必须标明是 V1 同层环，实际 stdout:\n{proc.stdout}"


def test_gate_stays_green_when_dependency_is_one_way(scanable_tree: Path) -> None:
    """**不成环**：只有单向 `services/b.py → runtime/a.py` → 必须 exit 0。

    ADR-0001 D3：`runtime` 是 L2.0 引擎底座、`services` 是 L2.1 领域服务，
    `services → runtime` 是**合法方向**。若门禁把单向依赖误判成环，
    就会逼着人改大量合法业务代码 —— 本切片明令不许动业务代码，
    所以这条断言同时是「门禁有判别力」的证明。
    """
    _write(scanable_tree, "src/find_yourself/runtime/a.py",
           "def helper() -> None:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/b.py",
           "from ..runtime.a import helper  # 合法：services -> runtime\n\n\nclass B:\n    pass\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 0, (
        "单向 services -> runtime 是 ADR-0001 D3 的合法方向，不得判成环（假红）"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_gate_goes_red_when_runtime_imports_services_one_way(scanable_tree: Path) -> None:
    """**单向** `runtime/a.py → services/b.py`（无反向边）→ 必须 exit 1。

    这是 D3R 与 V1 的分水岭，也是本用例存在的唯一理由：
    ADR-0001 D3 规定 `runtime`（L2.0）是引擎底座、`services`（L2.1）是其上
    领域服务，**禁止 `runtime → services`** —— 无论反向边是否存在。

    旧门禁抓不到它：`collect_reverse_deps()` 用的是**粗粒度三层**判定，
    `runtime` 与 `services` 同为 L2，于是 `dst_layer == src_layer` 直接 continue；
    `collect_d3_cross_pairs()` 只找「A→B 且 B→A」的**双向**交叉对。
    于是单向反向依赖落进双向盲区：存量看不见，新增也拦不住。
    """
    _write(scanable_tree, "src/find_yourself/runtime/a.py",
           "from ..services.b import B  # D3R 违规：L2.0 -> L2.1\n\n\nclass A:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/b.py",
           "class B:\n    pass\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "单向 runtime -> services 是 ADR-0001 D3 明文禁止的方向，必须 exit 1"
        f"（与是否成环无关），实际 returncode={proc.returncode}"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, f"必须用 ::error:: 输出，实际 stdout:\n{proc.stdout}"
    assert "D3R" in proc.stdout, f"必须标明触发的规则编号 D3R，实际 stdout:\n{proc.stdout}"
    assert "runtime/a.py" in proc.stdout, (
        f"失败输出必须点名源文件，实际 stdout:\n{proc.stdout}"
    )


def test_d3r_does_not_fire_on_legal_services_to_runtime(scanable_tree: Path) -> None:
    """反向守卫：新规则不得误伤 `services → runtime`（D3 合法方向）。

    与 `test_gate_stays_green_when_dependency_is_one_way` 互补：那条锁的是
    「不得判成环（V1）」，这条锁的是「不得判成 D3R」——两者是不同的规则，
    必须分别断言，否则其中一条被误实现时另一条仍会绿。
    """
    _write(scanable_tree, "src/find_yourself/runtime/a.py",
           "def helper() -> None:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/b.py",
           "from ..runtime.a import helper  # D3 合法方向：services -> runtime\n\n\n"
           "class B:\n    pass\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 0, (
        "D3R 不得把合法的 services -> runtime 判成违规（方向反了就会逼着人改 "
        f"上百处正确代码）\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    d3r_line = [ln for ln in proc.stdout.splitlines() if "D3R" in ln]
    assert d3r_line, f"输出里必须有 D3R 计数行（否则无法证明规则确实在跑），实际 stdout:\n{proc.stdout}"
    assert "合计 0" in d3r_line[0], (
        f"合法方向下 D3R 合计必须是 0，实际 stdout:\n{proc.stdout}"
    )


def test_d3r_existing_debt_is_absorbed_by_baseline(scanable_tree: Path) -> None:
    """D3R 走 baseline 机制：存量登记为已知债务 → exit 0；未登记 → exit 1。

    D3R 定为 hard 级（会阻塞 CI），但仓库现存 22 处 `runtime → services`
    方向违规。若没有 baseline 吸收，门禁一上线就常驻爆红，最终必然被人用
    `|| true` 绕过 —— 那和「没有门禁」等价。这条锁住「hard 规则 + baseline
    白名单」这个组合本身。
    """
    _write(scanable_tree, "src/find_yourself/runtime/a.py",
           "from ..services.b import B  # D3R 违规\n\n\nclass A:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/services/b.py", "class B:\n    pass\n")

    baseline = scanable_tree / "layering_baseline.json"
    baseline.write_text(json.dumps({
        "version": "1.0.0",
        "records": [{
            "id": "D3R|src/find_yourself/runtime/a.py|find_yourself.services.b",
            "kind": "sublayer_reverse_dep",
            "path": "src/find_yourself/runtime/a.py",
            "rule": "D3R",
            "reason": "已知债务：待下沉到 L1 契约或改依赖注入",
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    known = _run_gate(scanable_tree, "--baseline", str(baseline))
    assert known.returncode == 0, (
        "D3R 存量登记进 baseline 后必须 exit 0（否则门禁一上线就爆红、必然被绕过）"
        f"\nstdout:\n{known.stdout}\nstderr:\n{known.stderr}"
    )
    assert "已知债务" in known.stdout, (
        f"baseline 内的 D3R 必须报告为「已知债务」，实际 stdout:\n{known.stdout}"
    )

    # id 写错一位（target 尾部多一个字符）→ 必须退化成「新增违规」并 exit 1。
    baseline.write_text(json.dumps({
        "version": "1.0.0",
        "records": [{
            "id": "D3R|src/find_yourself/runtime/a.py|find_yourself.services.bx",
            "kind": "sublayer_reverse_dep",
            "path": "src/find_yourself/runtime/a.py",
            "rule": "D3R",
            "reason": "故意写错的 id",
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    mismatched = _run_gate(scanable_tree, "--baseline", str(baseline))
    assert mismatched.returncode == 1, (
        "baseline id 与实际产出不一致时必须判为新增违规并 exit 1（不得模糊匹配）"
        f"\nstdout:\n{mismatched.stdout}"
    )


def test_from_dot_import_sibling_module_is_not_a_cycle(scanable_tree: Path) -> None:
    """``from . import sibling`` 不得被当成「回到包__init__」的假环。

    真实缺陷（本仓 ``services/automation/registry.py``）：

        from . import permissions as perm      # permissions 是同级子模块

    若把 ``X`` 丢掉只解析成包名，就会凭空造出 ``registry → automation/__init__``
    这条自指边；``automation/__init__.py`` 又 import 了 registry，于是两个文件
    互指 —— 一个**根本不存在的环**，却会把基线撑大并逼人去改正确代码。
    """
    _write(scanable_tree, "src/find_yourself/services/automation/__init__.py",
           "from .registry import get_backend  # 包 __init__ -> registry\n\n\n"
           "__all__ = ['get_backend']\n\n\ndef get_backend():\n    return None\n")
    _write(scanable_tree, "src/find_yourself/services/automation/permissions.py",
           "ALLOW = {'read'}\n")
    _write(scanable_tree, "src/find_yourself/services/automation/registry.py",
           "from . import permissions as perm  # 同级子模块，不是包属性\n\n\n"
           "def get_backend():\n    return perm.ALLOW\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 0, (
        "from . import <同级子模块> 是普通依赖，不得判成环（假红）"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_from_dot_import_package_attribute_is_a_real_dependency(scanable_tree: Path) -> None:
    """``from . import X`` 且 X **不是**子模块时，依赖确实落在包 ``__init__`` 上。

    本仓 ``knowledge/sources/baidu_pan.py`` 就是这一类：
    ``secret_store = HubSecretStore()`` 写在 ``sources/__init__.py`` 里，
    它不是子模块，``from . import secret_store`` 真的依赖包初始化 ——
    配合 ``sources/__init__.py`` 顶部的 ``from .baidu_pan import ...``
    就是货真价实的环，门禁**必须**报出来。上一个测试不是这一条的豁免理由。
    """
    _write(scanable_tree, "src/find_yourself/services/knowledge/sources/__init__.py",
           "from .baidu_pan import BaiduPanSource  # 包 -> 子模块\n\n\n"
           "secret_store = object()\n\n__all__ = ['BaiduPanSource', 'secret_store']\n")
    _write(scanable_tree, "src/find_yourself/services/knowledge/sources/baidu_pan.py",
           "class BaiduPanSource:\n    def token(self):\n"
           "        from . import secret_store  # secret_store 是包属性，不是子模块\n"
           "        return secret_store\n")

    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "依赖包 __init__ 属性的惰性 import 与包对子模块的 import 构成真环，必须 exit 1"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "V1" in proc.stdout and "baidu_pan.py" in proc.stdout, (
        f"必须报出 V1 同层环并点名 baidu_pan.py，实际 stdout:\n{proc.stdout}"
    )


def test_w1_defaults_to_warn_and_can_be_escalated_to_hard(scanable_tree: Path) -> None:
    """W1（路由越层直连 runtime）默认 warn + 计数，`--w1 hard` 时升级为硬错。

    §4.3 说 W1 的严重度取决于子层口径，所以它必须是**可切换**的，
    不能写死成一种结论。
    """
    _write(scanable_tree, "src/find_yourself/runtime/sse.py", "def publish() -> None:\n    pass\n")
    _write(scanable_tree, "src/find_yourself/api/routes/runtime.py",
           "from ...runtime.sse import publish  # W1 越层直连\n")

    warned = _run_gate(scanable_tree)
    assert warned.returncode == 0, (
        "W1 默认应为警告级（warn + 计数），不得直接拦下合并"
        f"\nstdout:\n{warned.stdout}\nstderr:\n{warned.stderr}"
    )
    assert "W1" in warned.stdout, f"输出里必须列出 W1 现状，实际 stdout:\n{warned.stdout}"

    hardened = _run_gate(scanable_tree, "--w1", "hard")
    assert hardened.returncode == 1, (
        "--w1 hard 时 W1 必须变成硬错误，实际 returncode="
        f"{hardened.returncode}\nstdout:\n{hardened.stdout}\nstderr:\n{hardened.stderr}"
    )


def test_gate_goes_red_when_scan_surface_is_missing(tmp_path: Path) -> None:
    """扫描目录不存在 → 必须 exit 1，**不得**因为「0 违规」而假绿。

    目录被改名、checkout 不全、稀疏检出等情况下扫描面会消失；
    此时报「通过」等于门禁在最需要它的时刻自动失效。
    """
    for d in REQUIRED_DIRS[:-1]:
        (tmp_path / d).mkdir(parents=True)
    # 故意不建 src/find_yourself/api/routes —— 扫描面残缺。

    proc = _run_gate(tmp_path)

    assert proc.returncode == 1, (
        "扫描面缺失时门禁必须 exit 1（0 违规不等于契约满足），实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "::error::" in proc.stdout, (
        f"扫描面缺失必须用 ::error:: 输出，实际 stdout:\n{proc.stdout}"
    )


def test_gate_goes_red_on_empty_scan_surface(scanable_tree: Path) -> None:
    """扫描目录都在但一个 .py 都没有 → 同样 exit 1。

    与上一条区分：上一条是目录缺失，这一条是目录在、文件空。
    两者都指向同一个失败模式——「门禁没看到任何东西却报告通过」。
    """
    proc = _run_gate(scanable_tree)

    assert proc.returncode == 1, (
        "扫描到 0 个文件时门禁必须 exit 1，实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_gate_reports_baseline_debt_and_blocks_new_violations(scanable_tree: Path) -> None:
    """baseline 内已知项 → 报告为「已知债务」且 exit 0；baseline 外新增 → exit 1。

    这是 baseline 白名单机制的核心契约：门禁不能一上线就全红（会被 `|| true`
    绕过、形同虚设），但也绝不能「默认放过一切」。
    """
    baseline_path = "src/find_yourself/db/models.py"
    baseline = scanable_tree / "layering_baseline.json"
    baseline.write_text(json.dumps({
        "version": "1.0.0",
        "records": [{
            "id": "R5|src/find_yourself/db/models.py|find_yourself.services.errors",
            "kind": "reverse_dep",
            "path": baseline_path,
            "rule": "R5",
            "reason": "已知债务 V2：DomainError 系错误类型本应下沉 L1（three-layer-model §7）",
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ① 只有 baseline 内的那一条 → 报告为「已知债务」且 exit 0。
    _write(scanable_tree, baseline_path, "from ..services.errors import DomainError  # 已知债务\n")
    known_only = _run_gate(scanable_tree, "--baseline", str(baseline))

    assert known_only.returncode == 0, (
        "baseline 覆盖全部违规时应 exit 0（门禁不能一上线就全红、否则会被 `|| true` "
        f"绕过），实际 returncode={known_only.returncode}\nstdout:\n{known_only.stdout}"
    )
    assert "已知债务" in known_only.stdout, (
        f"baseline 内项必须报告为「已知债务」，实际 stdout:\n{known_only.stdout}"
    )
    assert "db/models.py" in known_only.stdout, (
        f"已知债务必须点名文件，实际 stdout:\n{known_only.stdout}"
    )

    # ② 再加一条 baseline 外的新增违规 → 必须 exit 1 且点名该文件。
    _write(scanable_tree, "src/find_yourself/db/other.py",
           "from ..services.errors import DomainError  # 新增违规\n")
    with_new = _run_gate(scanable_tree, "--baseline", str(baseline))

    assert with_new.returncode == 1, (
        "baseline 外的新增违规必须 exit 1（白名单不得默认放过一切），实际 returncode="
        f"{with_new.returncode}\nstdout:\n{with_new.stdout}"
    )
    assert "::error::" in with_new.stdout, f"新增违规必须 ::error::，实际 stdout:\n{with_new.stdout}"
    assert "db/other.py" in with_new.stdout, (
        f"新增违规必须点名文件，实际 stdout:\n{with_new.stdout}"
    )


def test_baseline_that_outlived_its_violation_is_reported_as_prunable(scanable_tree: Path) -> None:
    """baseline 条目已不再复现 → 必须提示可裁剪（baseline 只能变短不能变长）。

    默默留着会让 baseline 逐渐变成一张永远不被清理的免死金牌。
    """
    _write(scanable_tree, "src/find_yourself/db/models.py", "VALUE = 1\n")

    baseline = scanable_tree / "layering_baseline.json"
    baseline.write_text(json.dumps({
        "version": "1.0.0",
        "records": [{
            "id": "R5|src/find_yourself/db/gone.py|find_yourself.services.errors",
            "kind": "reverse_dep",
            "path": "src/find_yourself/db/gone.py",
            "rule": "R5",
            "reason": "早已收敛的历史债务",
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    proc = _run_gate(scanable_tree, "--baseline", str(baseline))

    assert proc.returncode == 0, (
        "baseline 里多记一条不该让门禁变红（否则无法渐进收敛），实际 returncode="
        f"{proc.returncode}\nstdout:\n{proc.stdout}"
    )
    assert "可裁剪" in proc.stdout, (
        f"已消失的 baseline 条目必须提示可裁剪，实际 stdout:\n{proc.stdout}"
    )
    assert "db/gone.py" in proc.stdout, (
        f"裁剪提示必须点名具体条目，实际 stdout:\n{proc.stdout}"
    )


def test_gate_ci_step_exists_and_is_not_softened() -> None:
    """CI 里必须有这一步，且不得被 `|| true` / `continue-on-error` 软化。

    门禁脚本本身再硬，如果 CI 里没人调用它，等于没有门禁。
    这条断言让「接入」本身变成可回归的。
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file(), f"CI workflow 不存在：{workflow}"
    text = workflow.read_text(encoding="utf-8")

    assert "run_layering_gate.py" in text, (
        "ci.yml 里没有任何地方调用 run_layering_gate.py —— "
        "分层门禁只在人手动跑时存在，CI 从不会因此拦下合并。"
    )
    for line in text.splitlines():
        if "run_layering_gate.py" not in line:
            continue
        assert "|| true" not in line, f"门禁调用被 `|| true` 软化：{line.strip()}"
        assert "continue-on-error" not in line, (
            f"门禁调用被 continue-on-error 软化：{line.strip()}"
        )
