"""B5 运行时首切片：CoordinationSpec 统一执行（N8 地基）。

同一份 spec 驱动执行——拓扑即数据，不是注释：

* ``sequential``：按 members 顺序串行，前者输出进后者输入；
* ``parallel``：ThreadPool 全员并发，结果按 members **声明序**汇聚
  （与 DSL ``parallel`` 同一语义）；
* ``pipeline``：按 members 顺序流经（每成员即一站；stages 数量语义
  由校验层保证 ≥2，本层按成员序执行）；
* ``map`` / ``delegation`` / ``market``：暂不支持，抛
  ``UnsupportedTopology``（不静默降级成别的拓扑跑——那是假执行）。

handler 签名 ``(member, data) -> output``；异常原样上抛（调用方审计）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .coordination import CoordinationSpec

Handler = Callable[[str, Any], Any]


class UnsupportedTopology(Exception):
    """该分解形态暂无执行器：spec 合法，但运行时还没接。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "unsupported_topology"


def _sequential(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    current = initial
    for member in spec.members:
        current = handler(member, current)
        results.append({"member": member, "output": current})
    return {"final": current, "results": results}


def _parallel(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    members = list(spec.members)
    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        futures = [pool.submit(handler, member, initial) for member in members]
        outputs = [fut.result() for fut in futures]
    # 声明序汇聚（完成先后不影响顺序）。
    results = [{"member": member, "output": out}
               for member, out in zip(members, outputs)]
    return {"final": [r["output"] for r in results], "results": results}


def _pipeline(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    return _sequential(spec, handler, initial)


_EXECUTORS = {
    "sequential": _sequential,
    "parallel": _parallel,
    "pipeline": _pipeline,
}


def run_spec(spec: CoordinationSpec, handler: Handler, initial: Any) -> dict[str, Any]:
    """执行一份已校验的 spec，返回 ``{"final", "results"}``。"""
    mode = spec.decomposition.mode
    executor = _EXECUTORS.get(mode)
    if executor is None:
        raise UnsupportedTopology(
            f"分解形态 {mode!r} 暂无执行器（已有：{sorted(_EXECUTORS)}）")
    return executor(spec, handler, initial)
