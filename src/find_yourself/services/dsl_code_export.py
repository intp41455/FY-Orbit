"""受限 DSL → 可读 Python 代码导出 + 代码 → DSL 逆向解析（需求 5③）。

为什么导出成**受限 Python 构造调用**而不是自由代码：画布上能做的事必须能用
受限动词集表达，导出物必须继承这个边界。因此导出器只会产出四种调用
（``Flow`` / ``input_node`` / ``transform_node`` / ``output_node`` / ``edge``），
且每个 ``verb`` 都取自 :data:`~find_yourself.services.dsl_canvas.VERB_REGISTRY`。
逆向解析器用 :mod:`ast` **静态解析**（绝不 ``eval``/``exec``），只接受上面
那几种调用形态与字面量参数；任何其他语句（``import``、赋值、循环、函数定义、
属性访问、算术表达式……）一律抛
:class:`~find_yourself.services.dsl_canvas.DslValidationError`。

于是有两条可测的不变量：

1. **封闭性**：导出物里不可能出现受限动词集之外的动词 —— 解析器会拒绝它。
2. **往返一致**：``parse_dsl_code(export_dsl_code(doc)) == canonical_dsl(doc)``，
   其中 :func:`canonical_dsl` 与既有 ``graph_to_dsl`` 的省略规则一致。

导出**不含任何密钥**（DSL 文档里本来就没有），也不含时间戳 —— 输出是确定性的，
因此往返测试可以做逐字节比对。

自包含可运行（B4 修复）
----------------------
导出脚本 ``import find_yourself_dsl``，而该模块过去并不存在——用户拿到的
``.py`` import 即崩（ModuleNotFoundError）。现在：

* 仓库根顶层 :mod:`find_yourself_dsl` 是真实可运行的最小运行库（纯 Python
  零第三方依赖，图定义/状态/执行循环的最小内核，与 :mod:`dsl_canvas` 的图
  结构语义一致）；
* :func:`export_dsl_code` 的结果自带 ``runtime_files``（运行库全部源码，
  相对路径 → 文本），:func:`write_export_bundle` 把脚本 + 运行库一并写出到
  导出目录——导出产物**自包含**，拷到任何机器都能 ``import`` 并运行；
  运行库落点可用 env ``FY_DSL_RUNTIME_DIR`` 指定，默认探测仓库根。
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

from .dsl_canvas import (
    CODE_EXPORT_FILENAME,
    NODE_PARAMS_SCHEMAS,
    VERB_REGISTRY,
    DslValidationError,
    canonical_dsl,
    compile_dsl,
    validate_dsl,
)

#: 导出文件里允许出现的函数名（解析器的白名单，封闭）。
FLOW_CTOR = "Flow"
NODE_CALLERS = ("input_node", "transform_node", "output_node", "edge")

#: 导出自包含运行库的包名与落点（B4 修复：导出产物 import 即崩的根治）。
DSL_RUNTIME_PACKAGE = "find_yourself_dsl"


def _locate_runtime_dir() -> Path:
    """定位 ``find_yourself_dsl`` 运行库源码目录。

    env ``FY_DSL_RUNTIME_DIR`` 是**显式覆盖**：设置后只信它（指向不存在的
    目录是配置错误，明确报错，绝不悄悄回落掩盖配置问题）；未设置时按
    仓库根顶层（src 布局回推三级）→ 当前工作目录探测。都找不到时明确报错，
    绝不导出半成品。
    """
    env = os.environ.get("FY_DSL_RUNTIME_DIR")
    candidates = [Path(env)] if env else \
        [Path(__file__).resolve().parents[3] / DSL_RUNTIME_PACKAGE,
         Path.cwd() / DSL_RUNTIME_PACKAGE]
    for cand in candidates:
        if (cand / "__init__.py").is_file():
            return cand
    raise DslValidationError(
        f"导出失败：找不到 {DSL_RUNTIME_PACKAGE} 运行库源码"
        "（导出产物需要它才能 import）"
        "；可用环境变量 FY_DSL_RUNTIME_DIR 指定其所在目录")


def runtime_source_files() -> dict[str, str]:
    """运行库全部 ``*.py`` 源码：``find_yourself_dsl/<name>.py`` → 文本。

    供 :func:`export_dsl_code` 附进结果、:func:`write_export_bundle` 写盘；
    排除 ``__pycache__``，按路径稳定排序（导出是确定性的）。
    """
    root = _locate_runtime_dir()
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts:
            continue
        files[f"{DSL_RUNTIME_PACKAGE}/{rel.as_posix()}"] = path.read_text(
            encoding="utf-8")
    if not files:
        raise DslValidationError(
            f"导出失败：{root} 里没有 {DSL_RUNTIME_PACKAGE} 的任何 Python 源文件")
    return files


def write_export_bundle(doc: dict[str, Any], directory: str | os.PathLike[str],
                        *, filename: str = CODE_EXPORT_FILENAME) -> dict[str, Any]:
    """把画布导出成**自包含**的目录：``flow_restricted.py`` + ``find_yourself_dsl/``。

    这是「导出产物可运行」的落盘路径：写完的目录拷到任何装有 Python ≥3.9
    的机器上，``import flow_restricted`` + ``find_yourself_dsl.run()`` 即可执行。
    非法 DSL 依旧在写盘**之前**抛 :class:`DslValidationError`，绝不写半成品。
    """
    exported = export_dsl_code(doc, filename=filename)
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = [filename]
    (out_dir / filename).write_text(exported["code"], encoding="utf-8",
                                    newline="\n")
    for rel, source in exported["runtime_files"].items():
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8", newline="\n")
        written.append(rel)
    return {"directory": str(out_dir), "entry": filename,
            "runtime_dirname": DSL_RUNTIME_PACKAGE, "files": written,
            "node_count": exported["node_count"],
            "edge_count": exported["edge_count"]}

#: 构造调用名→ 节点 type（解析时反查）。
_CALL_TO_TYPE = {
    "input_node": "input",
    "transform_node": "transform",
    "output_node": "output",
}

_HEADER = '''# -*- coding: utf-8 -*-
"""Find Yourself 受限 DSL 导出（由协作画布 / 工作流工坊生成）。

本文件由**受限动词集**导出：只含 Flow / input_node / transform_node /
output_node / edge 五种构造调用，每个 transform 节点的 verb 都取自平台注册表
{source}。

可用``find_yourself.services.dsl_code_export.parse_dsl_code()`` 逆向解析回
等价 DSL 文档（往返无损）。**不要手写自由代码**：平台不接受动词集之外的动词。
"""

from find_yourself_dsl import Flow, edge, input_node, output_node, transform_node
'''


def _py_literal(value: Any) -> str:
    """把 JSON 兼容值渲染成可被 ``ast.literal_eval`` 读回的 Python 字面量。

    非 JSON 兼容类型（tuple、自定义对象等）**明确报错**，绝不 ``repr`` 一个
    平台读不回来的东西。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_py_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise DslValidationError(
                f"导出失败：参数字典的键必须是字符串，实际有 {type(value)!r}")
        inner = ", ".join(f"{k!r}: {_py_literal(v)}" for k, v in value.items())
        return "{" + inner + "}"
    raise DslValidationError(
        f"导出失败：参数值类型 {type(value).__name__} 不是 JSON 兼容类型")


def _call(_fn: str, /, *args: str, **kwargs: str) -> str:
    """拼一个构造调用。``_fn`` 用位置-only，避免与 ``name=`` 这类参数键撞名。"""
    parts = list(args) + [f"{k}={v}" for k, v in kwargs.items()]
    return f"{_fn}({', '.join(parts)})"


def export_dsl_code(doc: dict[str, Any], *,
                    filename: str = CODE_EXPORT_FILENAME) -> dict[str, Any]:
    """把一份 DSL 文档导出成可读的受限 Python 代码。

    非法 DSL（含白名单外动词）一律抛
    :class:`~find_yourself.services.dsl_canvas.DslValidationError`，
    **绝不生成半成品代码**。除结构校验外还过一遍 :func:`compile_dsl`
    的环检测：导出的代码必须是能跑的那张画布。
    """
    validate_dsl(doc)
    compile_dsl(doc)
    canonical = canonical_dsl(doc)

    lines: list[str] = [_HEADER.format(source=list(VERB_REGISTRY)), ""]
    lines.append(_call(FLOW_CTOR, version=_py_literal(canonical["version"])))
    lines.append("")

    for node in canonical["nodes"]:
        lines.append(_render_node(node))
    if canonical["nodes"] and canonical["edges"]:
        lines.append("")
    for edge in canonical["edges"]:
        lines.append(_render_edge(edge))

    code = "\n".join(lines) + "\n"
    return {
        "filename": filename,
        "language": "python",
        "requires_python": ">=3.9",
        "code": code,
        "node_count": len(canonical["nodes"]),
        "edge_count": len(canonical["edges"]),
        # 受限动词集清单随导出物一同交付，读者无需查文档即可核对边界。
        "verbs": list(VERB_REGISTRY),
        # B4 自包含：随导出物交付可运行库源码（相对路径 → 文本），
        # 消费方落盘后脚本即可 import；详见 write_export_bundle。
        "runtime_dirname": DSL_RUNTIME_PACKAGE,
        "runtime_files": runtime_source_files(),
    }


def _render_node(node: dict[str, Any]) -> str:
    ntype = node["type"]
    caller = {v: k for k, v in _CALL_TO_TYPE.items()}[ntype]
    kwargs: dict[str, str] = {}
    if ntype == "transform":
        kwargs["verb"] = _py_literal(node["verb"])
    for key, value in (node.get("params") or {}).items():
        kwargs[key] = _py_literal(value)
    return _call(caller, _py_literal(node["id"]), **kwargs)


def _render_edge(edge: dict[str, Any]) -> str:
    kwargs: dict[str, str] = {}
    cond = edge.get("condition")
    if cond is not None:
        kwargs["condition"] = _py_literal(cond)
    return _call("edge", _py_literal(edge["from"]), _py_literal(edge["to"]), **kwargs)


# ---------------------------------------------------------------------------
# 逆向解析：ast 静态解析，绝不 eval/exec
# ---------------------------------------------------------------------------

#: 允许出现在构造参数位置的节点类型（其余一律拒绝）。
_LITERAL_NODES = (
    ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.UnaryOp, ast.USub, ast.UAdd,
)


def _literal(node: ast.AST) -> Any:
    """把一个字面量 AST 节点求值成Python 值；非字面量即报错。"""
    for sub in ast.walk(node):
        # Load/Store 只是 ctx 标记（Python 3.13 的 ast.walk 会产出），对求值无影响。
        if isinstance(sub, ast.expr_context):
            continue
        if not isinstance(sub, _LITERAL_NODES):
            raise DslValidationError(
                f"解析失败：构造参数只能是字面量，出现了 {type(sub).__name__}")
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise DslValidationError(f"解析失败：参数不是合法字面量（{exc}）") from None


def _keywords(call: ast.Call) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise DslValidationError("解析失败：构造调用不支持 ** 解包")
        out[kw.arg] = _literal(kw.value)
    return out


def _only_allowed(call: ast.Call, allowed: tuple[str, ...]) -> str:
    if not isinstance(call.func, ast.Name) or call.func.id not in allowed:
        got = (call.func.id if isinstance(call.func, ast.Name)
               else type(call.func).__name__)
        raise DslValidationError(
            f"解析失败：只允许 {allowed} 这几种构造调用，出现了 {got}")
    return call.func.id


def parse_dsl_code(code: str) -> dict[str, Any]:
    """把导出的受限 Python 代码解析回等价 DSL 文档。

    静态解析（:func:`ast.parse`）后逐语句比对白名单形态，**从不执行代码**。
    非法代码抛 :class:`~find_yourself.services.dsl_canvas.DslValidationError`。
    """
    if not isinstance(code, str) or not code.strip():
        raise DslValidationError("解析失败：代码为空")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise DslValidationError(
            f"解析失败：第 {exc.lineno} 行不是合法 Python（{exc.msg}）") from None

    version: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_flow = False

    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and isinstance(stmt.value.value, str):
            continue  # 模块 docstring
        if isinstance(stmt, ast.Import):
            # 裸import（import os）绝不静默：明确报错，绝不「看起来能跑」。
            raise DslValidationError(
                f"解析失败：不允许 import {stmt.names[0].name if stmt.names else '?'}")
        if isinstance(stmt, ast.ImportFrom):
            # 导出头部的 `from find_yourself_dsl import ...`：只放行该模块。
            if stmt.module != "find_yourself_dsl" or stmt.level:
                raise DslValidationError(
                    f"解析失败：不允许 import {stmt.module!r}")
            continue
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            raise DslValidationError(
                f"解析失败：第 {stmt.lineno} 行不是受限构造调用"
                f"（{type(stmt).__name__}）")
        call = stmt.value
        name = _only_allowed(call, (FLOW_CTOR,) + NODE_CALLERS)

        if name == FLOW_CTOR:
            if seen_flow or call.args:
                raise DslValidationError("解析失败：Flow 只能构造一次且不带位置参数")
            seen_flow = True
            version = _keywords(call).get("version")
            continue
        if not seen_flow:
            raise DslValidationError("解析失败：必须先构造 Flow(version=...)")

        kwargs = _keywords(call)
        if name == "edge":
            if len(call.args) != 2:
                raise DslValidationError("解析失败：edge 需要 exactly 两个位置参数")
            src, dst = (_literal(a) for a in call.args)
            if not isinstance(src, str) or not isinstance(dst, str):
                raise DslValidationError("解析失败：edge 的两端必须是字符串节点 id")
            edge: dict[str, Any] = {"from": src, "to": dst}
            if "condition" in kwargs:
                edge["condition"] = kwargs["condition"]
            edges.append(edge)
            continue

        if len(call.args) != 1:
            raise DslValidationError(
                f"解析失败：{name} 需要 1 个位置参数（节点 id）")
        node_id = _literal(call.args[0])
        if not isinstance(node_id, str):
            raise DslValidationError("解析失败：节点 id 必须是字符串")
        ntype = _CALL_TO_TYPE[name]
        node: dict[str, Any] = {"id": node_id, "type": ntype}
        params: dict[str, Any] = {}
        if ntype == "transform":
            verb = kwargs.pop("verb", None)
            if verb not in VERB_REGISTRY:
                raise DslValidationError(
                    f"解析失败：transform 节点 {node_id} 的 verb 必须是"
                    f" {tuple(VERB_REGISTRY)}，实际是 {verb!r}")
            node["verb"] = verb
        params.update(kwargs)
        # 越过动词闸门后再校验参数契约（封闭：多余键在此被拒）。
        # 先把 params 挂进节点再校验——契约校验读的是 node["params"]。
        if params:
            node["params"] = params
        validate_dsl({"version": version or "1", "nodes": [node], "edges": []})
        nodes.append(node)

    if version is None:
        raise DslValidationError("解析失败：缺少 Flow(version=...) 构造")
    doc = {"version": version, "nodes": nodes, "edges": edges}
    validate_dsl(doc)
    return canonical_dsl(doc)


__all__ = [
    "CODE_EXPORT_FILENAME",
    "DSL_RUNTIME_PACKAGE",
    "NODE_PARAMS_SCHEMAS",
    "export_dsl_code",
    "parse_dsl_code",
    "runtime_source_files",
    "write_export_bundle",
]