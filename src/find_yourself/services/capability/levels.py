"""统一能力网关 · 深层本机系统触达的五级分级 (补齐包1 A-能力网关-02)。

五级（每级可独立开关，越级需显式授权，可一键降级）：

===== ==================================================================
级别  语义
===== ==================================================================
L1    沙箱内（``runtime/sandbox.py`` 隔离 runner / ``.runtime/artifacts`` 制品沙箱）
L2    沙箱外工作区（用户工作区文件读写，仍受路径边界约束）
L3    进程树（派生/控制本机进程，含 CLI driver 类跨 agent 通道）
L4    桌面自动化 / 无障碍注入（受 automation 四档权限门约束，full 强制 TTL）
L5    驱动级（**可选**，默认关；开启需显式授权并逐次审计）
===== ==================================================================

设计要点：

* **每级独立开关**：默认 L1/L2 开、L3-L5 关（默认最小必要）。开关状态 =
  config 覆盖 ⊕ 本地 JSON 运行态（``.runtime/capability_levels.json``），
  与 ``services/automation/permissions.py`` 同款「本机开关本地持久化、不进业务库」。
* **越级**：请求级别高于能力默认级别即越级，除级别开关外还必须有一条
  显式越级授予（见 grants 的 ``escalation`` 四元组）。
* **一键降级**：:meth:`LevelRegistry.degrade_to` 关掉目标级别以上的所有级别，
  并返回被关闭的级别清单（broker 据此回收相应授予并审计）。
* **诚实边界**：开关是「授权表达」，不是操作系统级强制。L1 沙箱的真实隔离
  能力见 ``runtime/sandbox.py`` 的诚实声明（进程级隔离，不是安全边界）；
  本模块绝不夸大，风险提示随 :meth:`describe` 一并给出。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: 五级标识。保持字符串常量（JSON/DB/审计里直接可见，不搞魔法数字）。
LEVEL_L1 = "L1"  # 沙箱内
LEVEL_L2 = "L2"  # 沙箱外工作区
LEVEL_L3 = "L3"  # 进程树
LEVEL_L4 = "L4"  # 桌面自动化/无障碍注入
LEVEL_L5 = "L5"  # 驱动级（可选）

LEVEL_ORDER: dict[str, int] = {LEVEL_L1: 1, LEVEL_L2: 2, LEVEL_L3: 3, LEVEL_L4: 4, LEVEL_L5: 5}
LEVEL_IDS: tuple[str, ...] = (LEVEL_L1, LEVEL_L2, LEVEL_L3, LEVEL_L4, LEVEL_L5)

_PERSIST_ENV = "FY_CAPABILITY_LEVELS"
_DEFAULT_DIR = ".runtime"


@dataclass(frozen=True)
class LevelSpec:
    """一个级别的静态描述：能力清单 + 风险提示 + 默认开关。"""

    id: str
    name: str
    description: str
    risk_notes: str
    #: 该级别默认承载的能力类型（能力清单，前端提示用）。
    capabilities: tuple[str, ...]
    #: 默认是否启用（默认最小必要：只有 L1/L2 默认开）。
    default_enabled: bool
    #: 该级别上的能力是否**永远**需要显式授予（L3+ 为 True，小白档也不放行）。
    requires_explicit_grant: bool


LEVEL_SPECS: dict[str, LevelSpec] = {
    LEVEL_L1: LevelSpec(
        id=LEVEL_L1, name="沙箱内",
        description="隔离沙箱与制品目录内的文件读写/脚本执行",
        risk_notes="低风险；真实隔离等级为进程级（非安全边界），见 runtime/sandbox.py",
        capabilities=("local.fs.read", "local.fs.write", "local.sandbox.exec"),
        default_enabled=True, requires_explicit_grant=False,
    ),
    LEVEL_L2: LevelSpec(
        id=LEVEL_L2, name="沙箱外工作区",
        description="用户工作区文件的读写（仍受网关路径边界约束）",
        risk_notes="中风险：可触达用户真实文件；建议按路径前缀细粒度授予",
        capabilities=("local.workspace.read", "local.workspace.write"),
        default_enabled=True, requires_explicit_grant=False,
    ),
    LEVEL_L3: LevelSpec(
        id=LEVEL_L3, name="进程树",
        description="派生与控制本机进程树（含 CLI 驱动外部 agent）",
        risk_notes="高风险：等同执行任意命令；必须进程白名单 + 显式授予 + 审计",
        capabilities=("local.process.spawn", "cross_agent.cli.exec"),
        default_enabled=False, requires_explicit_grant=True,
    ),
    LEVEL_L4: LevelSpec(
        id=LEVEL_L4, name="桌面自动化/无障碍注入",
        description="屏幕观察、光标移动、键鼠注入（受 automation 四档门约束）",
        risk_notes="高风险：键鼠注入等同 SYSTEM_FS 级；full 档强制短 TTL 并到期回落",
        capabilities=("local.desktop.observe", "local.desktop.input", "local.desktop.full"),
        default_enabled=False, requires_explicit_grant=True,
    ),
    LEVEL_L5: LevelSpec(
        id=LEVEL_L5, name="驱动级（可选）",
        description="内核/驱动级触达（本实现只提供授权表达与审计，不提供驱动）",
        risk_notes="最高风险：默认禁用；开启须显式授权，且绝不默认随产品启用",
        capabilities=("local.driver",),
        default_enabled=False, requires_explicit_grant=True,
    ),
}


def is_escalation(spec_default_level: str | None, requested_level: str | None) -> bool:
    """请求级别是否构成「越级」（高于能力默认级别）。"""
    if requested_level is None or spec_default_level is None:
        return False
    if requested_level not in LEVEL_ORDER or spec_default_level not in LEVEL_ORDER:
        return False
    return LEVEL_ORDER[requested_level] > LEVEL_ORDER[spec_default_level]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LevelRegistry:
    """五级开关的运行态（config 默认 + 本地 JSON 覆盖），线程安全。"""

    def __init__(
        self,
        *,
        overrides: dict[str, bool] | None = None,
        persist_path: str | os.PathLike | None = None,
        clock=None,
    ) -> None:
        raw = persist_path or os.environ.get(_PERSIST_ENV) or _DEFAULT_DIR
        p = Path(raw)
        self._file = p / "capability_levels.json" if p.is_dir() or p.suffix == "" else p
        # RLock 而非 Lock：degrade_to 持锁期间会调用 enabled()（再入同锁）。
        self._lock = threading.RLock()
        self._clock = clock or _utcnow
        # 三层合成：规格默认 → config 覆盖 → 本地运行态覆盖。
        self._config_overrides: dict[str, bool] = dict(overrides or {})
        self._runtime: dict[str, bool] = {}
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self._file.is_file():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        runtime = data.get("levels", {})
        if isinstance(runtime, dict):
            self._runtime = {
                str(k): bool(v) for k, v in runtime.items() if k in LEVEL_ORDER
            }

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": self._clock().isoformat(),
            "levels": dict(self._runtime),
        }
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # -- queries -------------------------------------------------------------

    def enabled(self, level: str) -> bool:
        """该级别当前是否启用（未配置的级别回落到规格默认）。"""
        if level not in LEVEL_ORDER:
            return False
        with self._lock:
            if level in self._runtime:
                return self._runtime[level]
            if level in self._config_overrides:
                return bool(self._config_overrides[level])
            return LEVEL_SPECS[level].default_enabled

    def spec(self, level: str) -> LevelSpec | None:
        return LEVEL_SPECS.get(level)

    def describe(self) -> list[dict]:
        """每级能力清单 + 风险提示 + 当前开关（02：每级能力清单+风险提示）。"""
        out = []
        for level_id in LEVEL_IDS:
            spec = LEVEL_SPECS[level_id]
            out.append({
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "risk_notes": spec.risk_notes,
                "capabilities": list(spec.capabilities),
                "enabled": self.enabled(spec.id),
                "requires_explicit_grant": spec.requires_explicit_grant,
            })
        return out

    # -- mutations（owner-only 由 broker 把关；本层只做状态机）-----------------

    def set_enabled(self, level: str, enabled: bool) -> None:
        """独立开关某一级别（写入运行态并持久化）。未知级别抛 ValueError。"""
        if level not in LEVEL_ORDER:
            raise ValueError(f"unknown level: {level!r}")
        with self._lock:
            self._runtime[level] = bool(enabled)
            self._save()

    def degrade_to(self, level: str) -> list[str]:
        """一键降级：关闭目标级别以上的全部级别，返回被关闭的级别。

        目标级别本身保持/恢复为其「开启」状态（降级的语义是收深不收浅）。
        """
        if level not in LEVEL_ORDER:
            raise ValueError(f"unknown level: {level!r}")
        target_rank = LEVEL_ORDER[level]
        closed: list[str] = []
        with self._lock:
            for level_id in LEVEL_IDS:
                if LEVEL_ORDER[level_id] > target_rank:
                    if self.enabled(level_id):
                        closed.append(level_id)
                    self._runtime[level_id] = False
            self._save()
        return closed
