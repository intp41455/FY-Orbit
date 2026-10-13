"""CI 门禁：后端三层分层校验（G7-b / 需求 N7），按结果决定退出码。

判据来源（本脚本不自行发明规则，全部锚定已 Accepted 的规范）：

* ``docs/architecture/three-layer-model.md``
  * §4.1 R1–R7：R5 ``dep(L1,L2)`` ❌、R6 ``dep(L1,L3)`` / ``dep(L2,L3)`` ❌、
    R7 L2 内部不得成环；
  * §4.3 W1：``api/routes/*.py`` 直接 import ``runtime.*`` 属越层直连，
    **严重度取决于子层口径**，故本脚本用 ``--w1 hard|warn`` 切换，
    默认按 ADR-0001 D3 取 ``warn``；
  * §6 已知偏离 baseline（V1 / V2 / W1）；
  * §9.2 目录锚点、§9.4 违规识别样式。
* ``docs/adr/0001-three-layer-architecture.md`` D3：``runtime`` 是 L2.0 引擎底座，
  ``services`` 是 L2.1 领域服务，**合法方向是 ``services → runtime``**。

与 ``run_base_contract_gate.py`` 同样的 fail-closed 纪律
------------------------------------------------------
「0 违规」不等于「契约满足」。目录被改名 / checkout 不全 / 扫到 0 个文件时，
本脚本一律 ``exit 1`` 并打 ``::error::``，绝不在看不到扫描面的情况下报绿。

为什么用 ``ast`` 而不是 §9.4 的正则
----------------------------------
§9.4 给的识别样式只覆盖**相对导入**（``from ..services`` / ``from ...api``）。
本仓存在**绝对导入**的等价违规（如 ``db/workbench_models.py`` 里的
``from find_yourself.services.hub import ...``），正则口径会整类漏掉。
因此本脚本按 §9.4 的**语义**（依赖方向）用 ``ast`` 解析，相对/绝对导入一律覆盖，
§9.4 的正则仅作为口径说明保留在文档里。

环检测的判别纪律
----------------
``runtime → services`` 与 ``services → runtime`` 两条边**同时存在不等于成环**：
只有存在 A∈runtime、B∈services 使 A→B 且 B→A（或更长路径）时才成环。
本脚本用 Tarjan 求强连通分量（SCC）判定，SCC 内节点数 > 1 才是环；
单个 SCC 会输出成环模块对与环路径。

用法::

    PYTHONPATH=<tree>/src python scripts/p0/run_layering_gate.py
        [--scan-root DIR] [--baseline FILE] [--w1 hard|warn] [--report-cycle-paths]

``--scan-root`` / ``--baseline`` 用于把门禁指向别的树（反向验证测试据此构造
「被破坏的契约」并断言本脚本真的会红）；环境变量 ``FY_LAYERING_SCAN_ROOT`` /
``FY_LAYERING_BASELINE`` 次之。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: §9.2 目录锚点里「必须存在」的扫描面。缺任何一处 → fail-closed。
#: 只列**规范点名、且缺失即意味着分层无法判定**的目录。
REQUIRED_SCAN_DIRS = (
    "src/find_yourself/db",
    "src/find_yourself/services",
    "src/find_yourself/runtime",
    "src/find_yourself/api/routes",
)

#: 被扫描的源码根：(相对锚点, 该根下的顶层包名)。``find_yourself_dsl`` 在仓库根
#: 而非 src/ 下（§9.2 的 L2 锚点含 ``find_yourself_dsl/**``）。
SOURCE_ROOTS = (
    ("src", "find_yourself"),
    (".", "find_yourself_dsl"),
)

L1, L2, L3 = "L1", "L2", "L3"

#: ADR-0001 D3 子层：runtime = L2.0 引擎底座 < services = L2.1 领域服务。
SUBLAYER_RUNTIME = "L2.0"
SUBLAYER_SERVICES = "L2.1"


# --------------------------------------------------------------------------
# 层归属
# --------------------------------------------------------------------------
def classify_layer(module: str) -> str | None:
    """按 §9.2 目录锚点判定模块的层；不属于任一层返回 ``None``。

    L1 是「点名单」+ ``db/**``；L2 是若干目录树；L3 是 ``api/`` 下的点名单 +
    ``api/routes/**``。注意 ``api/errors.py`` 归 L1（虽在 api/ 下），
    ``runtime/hooks.py`` 归 L1（虽在 runtime/ 下）——先判点名单再判目录树。
    """
    if module == "find_yourself_dsl" or module.startswith("find_yourself_dsl."):
        return L2

    parts = module.split(".")
    if parts[:1] != ["find_yourself"]:
        return None

    # L1 点名单（§9.2）
    if module in ("find_yourself.contracts", "find_yourself.config",
                  "find_yourself.api.errors", "find_yourself.runtime.hooks",
                  "find_yourself.identity", "find_yourself.scheduling",
                  "find_yourself.hashing", "find_yourself.anchors",
                  "find_yourself.audit", "find_yourself.budget",
                  "find_yourself.offline"):
        return L1
    # L1 目录：db/**
    if parts[1:2] == ["db"]:
        return L1

    # L3 点名单 + api/routes/**（§9.2）
    if parts[1:2] == ["api"]:
        if parts[2:3] == ["routes"]:
            return L3
        if len(parts) == 3 and parts[2] in ("app", "main", "deps", "oidc", "schemas"):
            return L3
        if len(parts) == 2:  # find_yourself.api 包自身
            return L3
        return None

    # L2 目录树（§9.2）
    if parts[1:2] and parts[1] in ("services", "runtime", "adapters", "workflows",
                                   "charts", "skills"):
        return L2
    return None


def sublayer_of(module: str) -> str | None:
    """ADR-0001 D3 子层归属；只对 L2 内部有意义。"""
    if classify_layer(module) != L2:
        return None
    if module == "find_yourself.runtime" or module.startswith("find_yourself.runtime."):
        return SUBLAYER_RUNTIME
    if module == "find_yourself_dsl" or module.startswith("find_yourself_dsl."):
        return SUBLAYER_RUNTIME
    return SUBLAYER_SERVICES


# --------------------------------------------------------------------------
# import 解析（ast）
# --------------------------------------------------------------------------
def _module_name(py: Path, source_dir: Path) -> str:
    rel = py.relative_to(source_dir).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(module: str, is_pkg_init: bool) -> str:
    if is_pkg_init:
        return module
    parts = module.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else ""


def _resolve_relative(package: str, level: int, tail: str) -> str | None:
    """把 ``from ..x import y``（level=2）解析成绝对模块名。"""
    base = package.split(".") if package else []
    if base and base[-1] == "__init__":
        base = base[:-1]
    up = level - 1
    if up > 0:
        if len(base) < up:
            return None
        base = base[: len(base) - up]
    if not tail:
        return ".".join(base) if base else None
    return ".".join([*base, *tail.split(".")])


class ImportGraph:
    """两层边的导入图。

    * ``edges`` —— 模块 → import 到的**原始绝对模块名**（相对导入已按
      ``level`` 解析成绝对名）。**刻意不要求目标文件真实存在**：§9.4 的判定口径
      是 import 语句本身的方向，目标文件缺失时那条 import 依然是反向依赖
      （而且它本身还是坏代码）。方向判定只看这一层。
    * ``deps`` —— 模块 → **树内真实存在**的依赖模块。环检测必须用这一层，
      否则会把并不存在的模块名算进图里，凭空造出「环」。
    """

    def __init__(self) -> None:
        self.modules: dict[str, Path] = {}
        self.edges: dict[str, set[str]] = {}
        self.deps: dict[str, set[str]] = {}
        self.unresolved: list[tuple[str, int, str]] = []

    def add_module(self, module: str, path: Path) -> None:
        self.modules[module] = path

    def finalize(self) -> None:
        known = set(self.modules)
        for module, targets in list(self.edges.items()):
            deps: set[str] = set()
            for target in targets:
                probe = target
                while probe:
                    if probe in known:
                        deps.add(probe)
                        break
                    if "." not in probe:
                        break
                    probe = probe.rsplit(".", 1)[0]
            self.deps[module] = deps

    def importers_of_target(self, target_prefix: str) -> set[str]:
        """返回所有「import 目标落在 ``target_prefix`` 之下」的源模块。"""
        hit: set[str] = set()
        for module, targets in self.edges.items():
            if any(t == target_prefix or t.startswith(target_prefix + ".")
                   for t in targets):
                hit.add(module)
        return hit


def _parse_imports(py: Path, module: str, is_pkg_init: bool,
                   graph: ImportGraph) -> None:
    package = _package_of(module, is_pkg_init)
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except SyntaxError as exc:  # 语法坏掉的文件不能被静默跳过
        graph.unresolved.append((module, exc.lineno or 0, f"SyntaxError: {exc.msg}"))
        return

    targets = graph.edges.setdefault(module, set())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            level = node.level or 0
            base = (_resolve_relative(package, level, node.module or "")
                    if level else (node.module or None))
            if not base:
                continue
            if node.module:
                targets.add(base)
                continue
            # ``from . import X``：X 可能是包的一个属性（真的依赖包 __init__），
            # 也可能是同级子模块（依赖的是那个文件）。只有后者能指向具体模块，
            # 否则 ``automation/registry`` 与 ``automation/__init__`` 之间会被
            # 凭空造出一条自指假边。build_graph 先注册全部模块再解析，故此处
            # 可以查 ``graph.modules`` 判定。
            for alias in node.names:
                if f"{base}.{alias.name}" in graph.modules:
                    targets.add(f"{base}.{alias.name}")
                else:
                    targets.add(base)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)


def build_graph(root: Path) -> ImportGraph:
    graph = ImportGraph()
    for rel_src, top_package in SOURCE_ROOTS:
        source_dir = (root / rel_src).resolve()
        if not source_dir.is_dir():
            continue
        package_root = source_dir / top_package
        if not package_root.is_dir():
            continue
        for py in sorted(package_root.rglob("*.py")):
            module = _module_name(py, source_dir)
            if not module:
                continue
            graph.add_module(module, py)
    for module, py in graph.modules.items():
        _parse_imports(py, module, py.name == "__init__.py", graph)
    graph.finalize()
    return graph


# --------------------------------------------------------------------------
# Tarjan 强连通分量（真环检测）
# --------------------------------------------------------------------------
def tarjan_scc(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """迭代版 Tarjan：返回所有 size > 1 的强连通分量（即真正的环）。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, succs = work[-1]
            if succs:
                nxt = succs.pop()
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, sorted(edges.get(nxt, ()))))
                elif on_stack.get(nxt):
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        comp.append(w)
                        if w == node:
                            break
                    out.append(comp)
    return out


def find_cycle_path(start: str, goal: str,
                    edges: dict[str, set[str]]) -> list[str] | None:
    """BFS 求 ``start`` 回到 ``goal`` 的最短路径；用于把环打印成可读的链条。"""
    prev: dict[str, str] = {}
    seen = {start}
    frontier = [start]
    while frontier:
        following: list[str] = []
        for node in frontier:
            for nxt in sorted(edges.get(node, ())):
                if nxt == goal:
                    path = [nxt, node]
                    while path[-1] != start:
                        path.append(prev[path[-1]])
                    path.reverse()
                    return path
                if nxt not in seen:
                    seen.add(nxt)
                    prev[nxt] = node
                    following.append(nxt)
        frontier = following
    return None


# --------------------------------------------------------------------------
# baseline
# --------------------------------------------------------------------------
def _violation_id(rule: str, path: str, detail: str) -> str:
    return f"{rule}|{path}|{detail}"


def load_baseline(baseline_path: Path, root: Path) -> tuple[dict[str, dict], list[str]]:
    """读 baseline；返回 (id → record, 读取期问题)。**文件不存在不是错误**。

    baseline 缺失意味着「零已知债务」——门禁只会更严，不会更松，
    所以不 fail-closed（否则临时树无法用于反向验证）。
    但**存在却读不出来**必须报错：那会让门禁静默把全部已知债务当新增，
    报出一堆假红，或（更糟）让人以为 baseline 已生效。
    """
    if not baseline_path.is_file():
        return {}, [f"baseline 文件不存在：{baseline_path}（按零已知债务处理）"]
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"baseline 文件不是合法 JSON：{baseline_path}（{exc}）"]
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return {}, [f"baseline 文件缺少 records 数组：{baseline_path}"]
    records: dict[str, dict] = {}
    problems: list[str] = []
    for index, record in enumerate(data["records"]):
        if not isinstance(record, dict) or not record.get("id"):
            problems.append(f"baseline 第 {index} 条记录缺少 id，无法匹配：已忽略")
            continue
        records[str(record["id"])] = record
    return records, problems


def resolve_baseline(cli_value: str | None, root: Path) -> Path:
    if cli_value:
        return Path(cli_value).resolve()
    env = os.environ.get("FY_LAYERING_BASELINE")
    if env:
        return Path(env).resolve()
    return root / "scripts" / "p0" / "layering_baseline.json"


def resolve_scan_root(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).resolve()
    env = os.environ.get("FY_LAYERING_SCAN_ROOT")
    return Path(env).resolve() if env else ROOT


def rel_to_root(root: Path, module: str, graph: ImportGraph) -> str:
    py = graph.modules[module]
    try:
        return py.relative_to(root).as_posix()
    except ValueError:
        return py.as_posix()


# --------------------------------------------------------------------------
# 各项检查
# --------------------------------------------------------------------------
def collect_reverse_deps(graph: ImportGraph, root: Path) -> list[dict]:
    """R5 / R6：L1 或 L2 反向依赖更高层（L2→L3、L1→L2、L1→L3）。"""
    violations: list[dict] = []
    for module, targets in graph.edges.items():
        src_layer = classify_layer(module)
        if src_layer not in (L1, L2):
            continue
        for target in sorted(targets):
            dst_layer = classify_layer(target)
            if dst_layer is None or dst_layer == src_layer:
                continue
            layer_rank = {L1: 1, L2: 2, L3: 3}
            if layer_rank[dst_layer] <= layer_rank[src_layer]:
                # 向下或同层：R1/R2/R3/R4 允许。
                continue
            # §4.1：R5 = dep(L1,L2)；R6 = dep(L1,L3) 与 dep(L2,L3)。
            rule = "R5" if (src_layer, dst_layer) == (L1, L2) else "R6"
            path = rel_to_root(root, module, graph)
            violations.append({
                "id": _violation_id(rule, path, target),
                "kind": "reverse_dep",
                "rule": rule,
                "path": path,
                "module": module,
                "target": target,
                "target_path": rel_to_root(root, target, graph) if target in graph.modules else target,
                "detail": (f"{src_layer} → {dst_layer} 反向依赖：{module} → {target}"),
            })
    return violations


def collect_w1(graph: ImportGraph, root: Path) -> list[dict]:
    """W1（§4.3）：``api/routes/*.py`` 直接 import ``runtime.*``。"""
    violations: list[dict] = []
    for module, targets in graph.edges.items():
        parts = module.split(".")
        if parts[:3] != ["find_yourself", "api", "routes"]:
            continue
        for target in sorted(targets):
            if not (target == "find_yourself.runtime"
                    or target.startswith("find_yourself.runtime.")):
                continue
            path = rel_to_root(root, module, graph)
            violations.append({
                "id": _violation_id("W1", path, target),
                "kind": "skip_layer_direct",
                "rule": "W1",
                "path": path,
                "module": module,
                "target": target,
                "target_path": rel_to_root(root, target, graph) if target in graph.modules else target,
                "detail": f"L3 路由越层直连执行引擎：{module} → {target}",
            })
    return violations


def collect_v1_cycles(graph: ImportGraph, root: Path,
                      report_paths: bool) -> list[dict]:
    """V1 / R7：L2 内部成环（Tarjan SCC）。

    **双向边 ≠ 成环**：只有真正回到自己的强连通分量才算环，
    因此 ``services → runtime`` 单向依赖不会被误判（ADR-0001 D3 合法方向）。
    """
    l2_nodes = [m for m in graph.modules if classify_layer(m) == L2]
    l2_edges = {m: {t for t in graph.deps.get(m, ()) if classify_layer(t) == L2}
                for m in l2_nodes}
    violations: list[dict] = []
    for comp in sorted(tarjan_scc(l2_nodes, l2_edges), key=lambda c: sorted(c)):
        if len(comp) < 2:
            continue
        members = sorted(comp)
        # 「runtime ⇄ services 环」= 该 SCC 同时含 L2.0(runtime/dsl) 与
        # L2.1(services/其余) 两侧成员；纯 services 内部的环单列为 l2_cycle。
        has_runtime_side = any(sublayer_of(m) == SUBLAYER_RUNTIME for m in members)
        has_services_side = any(sublayer_of(m) == SUBLAYER_SERVICES for m in members)
        kind = ("runtime_services_cycle" if has_runtime_side and has_services_side
                else "l2_cycle")
        paths = sorted(rel_to_root(root, m, graph) for m in members)
        entry = {
            "id": _violation_id("V1", " ⇄ ".join(paths), kind),
            "kind": kind,
            "rule": "V1",
            "path": " ⇄ ".join(paths),
            "detail": (f"L2 内部成环（{kind}，{len(members)} 个模块）："
                       + ", ".join(paths)),
        }
        if report_paths:
            entry["cycle_path"] = _render_cycle(members, l2_edges, graph, root)
        violations.append(entry)
    return violations


def _render_cycle(members: list[str], edges: dict[str, set[str]],
                  graph: ImportGraph, root: Path) -> list[str]:
    for start in members:
        for goal in members:
            if start >= goal:
                continue
            path = find_cycle_path(start, goal, edges)
            if path:
                return [rel_to_root(root, m, graph) for m in path]
    return []


def collect_d3_cross_pairs(graph: ImportGraph, root: Path) -> list[tuple[str, str]]:
    """ADR-0001 D3：A∈runtime、B∈services 且 A→B、B→A 的**交叉环对**。

    这是「runtime ⇄ services 是否真成环」的直接证据，与 SCC 互为交叉验证：
    SCC 覆盖任意长度的环，本项只认 2 元交叉对。
    """
    runtime = {m for m in graph.modules if sublayer_of(m) == SUBLAYER_RUNTIME}
    services = {m for m in graph.modules if sublayer_of(m) == SUBLAYER_SERVICES}
    pairs: list[tuple[str, str]] = []
    for a in sorted(runtime):
        for b in sorted(graph.deps.get(a, ())):
            if b in services and a in graph.deps.get(b, ()):
                pairs.append((a, b))
    return pairs


def collect_d3_reverse_deps(graph: ImportGraph, root: Path) -> list[dict]:
    """D3R：ADR-0001 D3 **单向**反向依赖 —— 源属 L2.0(runtime)、目标属 L2.1(services)。

    与既有两条规则的区别正是本项存在的理由：

    * ``collect_reverse_deps()`` 是**粗粒度三层**判定，``runtime`` 与 ``services``
      同为 L2，落到 ``dst_layer == src_layer`` 分支被 continue 掉；
    * ``collect_d3_cross_pairs()`` 只认「A→B **且** B→A」的**双向**交叉对，
      单向边一个都抓不到。

    于是 ADR-0001 D3 明文禁止的方向（``runtime → services``）落进双向盲区：
    存量看不见、新增也拦不住。本项按 ``sublayer_of()`` 的子层口径补上这条，
    判据只看**单向**边，与是否成环无关（D3 禁止的是方向，不是环）。

    用 ``graph.edges``（原始 import 名）而非 ``deps``：方向判定只看 import 语句
    本身，目标模块缺失时那条 import 依然是反向依赖，与 R5/R6/W1 同口径。
    """
    violations: list[dict] = []
    for module, targets in graph.edges.items():
        if sublayer_of(module) != SUBLAYER_RUNTIME:
            continue
        for target in sorted(targets):
            if sublayer_of(target) != SUBLAYER_SERVICES:
                continue
            path = rel_to_root(root, module, graph)
            violations.append({
                "id": _violation_id("D3R", path, target),
                "kind": "sublayer_reverse_dep",
                "rule": "D3R",
                "path": path,
                "module": module,
                "target": target,
                "target_path": rel_to_root(root, target, graph) if target in graph.modules else target,
                "detail": (f"L2.0 → L2.1 反向依赖（ADR-0001 D3 禁止方向）："
                           f"{module} → {target}"),
            })
    return violations


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="后端三层分层门禁（fail-closed）")
    parser.add_argument("--scan-root", default=None,
                        help="扫描根目录；默认取 FY_LAYERING_SCAN_ROOT，再默认本仓根")
    parser.add_argument("--baseline", default=None,
                        help="baseline 文件；默认取 FY_LAYERING_BASELINE，"
                             "再默认 <scan-root>/scripts/p0/layering_baseline.json")
    parser.add_argument("--w1", choices=("hard", "warn"), default="warn",
                        help="W1（路由越层直连 runtime）严重度；默认 warn（ADR-0001 D3 口径）")
    parser.add_argument("--report-cycle-paths", action="store_true",
                        help="额外打印成环模块的环路径链条")
    args = parser.parse_args(argv)

    root = resolve_scan_root(args.scan_root)
    baseline_path = resolve_baseline(args.baseline, root)

    # **假绿的头号来源**：扫描目录被改名 / 未 checkout 出来时，扫描结果为
    # 0 个文件、0 条违规，旧写法会「通过」——门禁恰恰在最需要它的时候消失。
    missing = [d for d in REQUIRED_SCAN_DIRS if not (root / d).is_dir()]
    if missing:
        for d in missing:
            print(f"::error:: 扫描目录不存在：{root / d}（REQUIRED_SCAN_DIRS="
                  f"{list(REQUIRED_SCAN_DIRS)}）")
        print("阻塞（fail-closed）：扫描面缺失，无法判定三层分层状态。")
        return 1

    graph = build_graph(root)
    scanned = len(graph.modules)
    if not scanned:
        print("::error:: 扫描到 0 个 Python 模块——门禁不得在无扫描面时报告通过。")
        print("阻塞（fail-closed）：扫描结果为空，无法证明分层契约已满足。")
        return 1

    for module, lineno, message in graph.unresolved:
        print(f"::error:: {rel_to_root(root, module, graph) if module in graph.modules else module}"
              f":{lineno} 解析失败：{message}")

    reverse = collect_reverse_deps(graph, root)
    w1 = collect_w1(graph, root)
    cycles = collect_v1_cycles(graph, root, args.report_cycle_paths)
    cross_pairs = collect_d3_cross_pairs(graph, root)
    d3r = collect_d3_reverse_deps(graph, root)

    known, baseline_problems = load_baseline(baseline_path, root)
    for problem in baseline_problems:
        print(f"::warning:: {problem}")
    if baseline_problems and baseline_path.is_file() and not known:
        print("阻塞（fail-closed）：baseline 存在但读不出任何记录，无法区分"
              "「已知债务」与「新增违规」。")
        return 1

    hard = [v for v in reverse + cycles + d3r]
    soft = w1 if args.w1 == "hard" else []

    new_hard = [v for v in hard if v["id"] not in known]
    debt_hard = [v for v in hard if v["id"] in known]
    new_soft = [v for v in soft if v["id"] not in known]
    debt_soft = [v for v in soft if v["id"] in known]

    print(f"三层分层门禁：root={root} scanned={scanned} "
          f"baseline={baseline_path}")
    print(f"  R5/R6 反向依赖 : 新增 {len([v for v in new_hard if v['rule'] in ('R5', 'R6')])}"
          f" / 已知债务 {len([v for v in debt_hard if v['rule'] in ('R5', 'R6')])}"
          f" / 合计 {len([v for v in reverse])}")
    print(f"  V1 同层环      : 新增 {len([v for v in new_hard if v['rule'] == 'V1'])}"
          f" / 已知债务 {len([v for v in debt_hard if v['rule'] == 'V1'])}"
          f" / 合计 {len(cycles)}")
    print(f"  D3R runtime→services 单向反向依赖 : "
          f"新增 {len([v for v in new_hard if v['rule'] == 'D3R'])}"
          f" / 已知债务 {len([v for v in debt_hard if v['rule'] == 'D3R'])}"
          f" / 合计 {len(d3r)}  [严重度=hard]")
    print(f"  W1 越层直连    : 新增 {len(new_soft)} / 已知债务 {len(debt_soft)}"
          f" / 合计 {len(w1)}  [严重度={args.w1}]")
    print(f"  ADR-0001 D3 交叉环对（runtime→services 且 services→runtime 的同一对模块）: "
          f"{len(cross_pairs)}")
    for a, b in cross_pairs:
        print(f"    ⇄ {rel_to_root(root, a, graph)} ⇄ {rel_to_root(root, b, graph)}")

    if cycles:
        print("  V1 环明细：")
        for v in cycles:
            state = "新增违规" if v["id"] not in known else "已知债务"
            print(f"    [{state}] {v['detail']}")
            if args.report_cycle_paths:
                chain = v.get("cycle_path") or []
                if chain:
                    print("        环路径: " + " → ".join(chain)
                          + f" → {chain[0]}")

    if w1:
        print("  W1 现状全量清单：")
        for v in w1:
            state = "新增违规" if v["id"] not in known else "已知债务"
            print(f"    [{state}] {v['path']} → {v['target']}")

    for v in debt_hard + debt_soft:
        print(f"::notice:: 已知债务（baseline 内）[{v['rule']}] {v['path']}：{v['detail']}")

    for v in new_hard:
        print(f"::error:: [{v['rule']}] {v['path']}：{v['detail']}（不在 baseline 内）")
    for v in new_soft:
        print(f"::error:: [{v['rule']}] {v['path']}：{v['detail']}（--w1 hard，不在 baseline 内）")

    # baseline 只能变短不能变长：已不再复现的条目必须提示裁剪，
    # 否则它会慢慢变成一张永不过期的免死金牌。
    seen_ids = {v["id"] for v in hard + w1}
    stale = sorted(set(known) - seen_ids)
    if stale:
        print(f"  baseline 有 {len(stale)} 条已不再复现，可裁剪"
              f"（baseline 只能变短不能变长）：")
        for record_id in stale:
            print(f"    ::notice:: 可裁剪的 baseline 条目：{record_id}")

    if graph.unresolved:
        print("阻塞（fail-closed）：存在无法解析的源文件，扫描面不完整。")
        return 1
    if new_hard or new_soft:
        print(f"阻塞（fail-closed）：{len(new_hard) + len(new_soft)} 条 baseline 外新增违规。")
        return 1
    print("通过：无 baseline 外新增违规；已知债务计数见上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
