"""find_yourself_dsl —— Find Yourself 受限 DSL 的**独立可运行库**（B4 修复）。

为什么存在
----------
平台把画布导出成受限 Python（``from find_yourself_dsl import Flow, ...``），
但过去这个模块并不存在——用户拿到的 ``.py`` **import 即崩**
（ModuleNotFoundError）。本包补上这块缺口：一个纯 Python、**零第三方依赖**
的最小运行库，使导出产物开箱即跑。

语义边界
--------
* 图结构 / 动词语义 / 调度语义与平台
  ``find_yourself.services.dsl_canvas`` 一致（只读镜像，防漂移靠平台侧
  parity 测试：``tests/unit/dsl_sdk/test_runtime_parity.py``）。
* ``agent``：未注入 ``agent_resolver`` 时诚实失败（平台同款行为）。
* ``confirm`` / ``approval``：独立运行库没有 HITL/治理编排层，走到即抛
  :class:`~find_yourself_dsl._errors.DslSuspended`（挂起 ≠ 失败），由嵌入方
  捕获并自行续跑。
* ``approval.op`` 的治理白名单校验属平台编译期（唯一真源
  ``services/proposal.py``），运行库不重抄。

最小用法
--------
>>> import flow_restricted          # 平台导出的画布脚本（import 期装配图）
>>> from find_yourself_dsl import run, DslSuspended
>>> result = run()                  # 执行当前图
>>> result.status, result.output
('succeeded', ...)
>>> to_document()                   # 拿回平台同构 DSL 文档
"""

from ._errors import DslError, DslSuspended, DslValidationError
from ._engine import (
    AgentResolver,
    CompiledPlan,
    FlowResult,
    NodeLog,
    execute_node,
    run,
)
from ._graph import (
    Flow,
    FlowBuilder,
    canonical_dsl,
    dsl_digest,
    edge,
    input_node,
    output_node,
    reset_current_flow,
    to_document,
    transform_node,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # 构造调用（导出文件的 import 面必须逐字覆盖这五个名字）
    "Flow", "input_node", "transform_node", "output_node", "edge",
    # 运行
    "run", "execute_node", "to_document", "reset_current_flow",
    "canonical_dsl", "dsl_digest",
    # 类型
    "FlowBuilder", "FlowResult", "NodeLog", "CompiledPlan", "AgentResolver",
    # 异常
    "DslError", "DslValidationError", "DslSuspended",
]
