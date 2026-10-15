"""Task lifecycle stages — L1 single source (N7 R5-1 解环).

``Stage`` 原先住在 ``workflows/models.py``（L2），``db/models.py``（L1）为派生
``TASK_STAGES``（DB CHECK ``ck_task_stage``）而反向导入 L2，构成 R5 违规。
下沉到 L1 后：

* ``db/models.py`` 从本模块取（L1 内部边）；
* ``workflows/models.py`` 从本模块重导出（L2→L1 合法方向，既有导入方零改动）；
* DB CHECK 与工作流状态机仍是同一份枚举派生，不会漂移（原注释意图保留）。
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    queued = "queued"
    requirements = "requirements"
    planning = "planning"
    execution = "execution"
    awaiting_approval = "awaiting_approval"
    awaiting_input = "awaiting_input"
    reconciling = "reconciling"
    review = "review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
