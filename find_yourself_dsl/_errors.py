"""独立运行库的异常与挂起信号（B4 修复：导出产物可运行）。

语义与平台 :mod:`find_yourself.services.dsl_canvas` 对齐：

* :class:`DslValidationError` —— 文档/参数不合法（平台同名异常的独立镜像；
  同为 ``ValueError`` 子类，嵌入方既有的 ``except ValueError`` 照常命中）。
* :class:`DslSuspended` —— **控制流信号，不是失败**：走到 ``confirm`` /
  ``approval`` 且尚无人工裁决时抛出，与平台的挂起语义一致（挂起被当成
  普通失败正是最难排查的假绿）。独立运行库没有 HITL 编排层，因此裁决
  读取器不存在——这两个动词必定挂起，由嵌入方捕获后自行续跑。

本包是**导出自包含**运行库：纯 Python 标准库，零第三方依赖；平台侧的唯一
真源（``VERB_REGISTRY`` 等）只读镜像，见 ``_schema.py`` 的漂移护栏说明。
"""

from __future__ import annotations

from typing import Any

__all__ = ["DslError", "DslValidationError", "DslSuspended"]


class DslError(Exception):
    """独立运行库异常基类（便于嵌入方一条 except 接住全部）。"""


class DslValidationError(DslError, ValueError):
    """DSL 文档/参数不合法（结构 / 拓扑 / 参数契约）。"""


class DslSuspended(DslError):
    """工作流在 ``confirm`` / ``approval`` 处挂起，等待人工裁决。

    **控制流信号，不是节点失败**：调用方捕获后可据此建人工介入入口，
    裁决后重新驱动剩余节点。字段与平台
    :class:`find_yourself.services.dsl_canvas.DslSuspended` 同名同义，
    便于上层编排用同一套代码处理两种载体。
    """

    def __init__(self, *, checkpoint: str, dsl_digest: str,
                 context: dict[str, Any], options: list[dict[str, Any]],
                 node_id: str) -> None:
        super().__init__(f"工作流在节点 {node_id} 处挂起，等待人工裁决")
        self.checkpoint = checkpoint
        self.dsl_digest = dsl_digest
        self.context = context
        self.options = options
        self.node_id = node_id

    def to_dict(self) -> dict[str, Any]:
        return {"checkpoint": self.checkpoint, "dsl_digest": self.dsl_digest,
                "context": self.context, "options": self.options,
                "node_id": self.node_id}
