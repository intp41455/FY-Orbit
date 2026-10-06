"""Claw 治理框架（A-Claw架构 01–06 + 机制/参与切片的落地包）。

子模块：
* ``facts``     —— 全局事实基线库（机制-02）
* ``gates``     —— 三层把关流水线（架构-01/02/03/04）
* ``conflicts`` —— 六类冲突预防+四级升级（架构-05/06、增强-01）
"""

from .conflicts import (  # noqa: F401
    CONFLICT_CLASSES,
    ESCALATION_LEVELS,
    ConflictDetector,
    ConflictService,
    DetectionHit,
    DetectionSignal,
)
from .facts import FactBaselineService, upsert_fact  # noqa: F401
from .gates import (  # noqa: F401
    CrossValidationGate,
    GateFinding,
    GateLayer,
    GateOutcome,
    GateVerdict,
    IndependentQAGate,
    SelfCheckGate,
    ThreeLayerPipeline,
)
