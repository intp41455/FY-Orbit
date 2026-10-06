"""统一能力网关 · 授权档位 (补齐包1 A-能力网关-05)。

两个档位：

* **小白友好档（novice，默认）**：粗粒度、零配置。零配置下载即用的关键：
  默认级别在 L1/L2 的本机能力（沙箱内 + 工作区）无需任何授予即可用
  （路径边界与更严门仍然生效）；其余能力依赖各自已有的显式开关
  （automation 四档 / 级别开关 / 四元组授予）。
* **细粒度档（fine）**：按四元组（能力 × 资源 × 时限 × 可撤回）细配；
  novice 的粗粒度隐式放行**整体失效**，一切走显式授予——颗粒度换易用性。

切档语义（切档有提示与回退）：

* 切档 **owner-only**（broker 把关），切换结果带「提示」返回
  （前端据此弹提示：当前哪些行为会变化）；
* 切档自动记录「上一个档位」，:meth:`ProfileManager.revert` 一键回退；
* 档位持久化在本地 JSON（``.runtime/capability_profile.json``），同
  automation 权限门的「本机开关不进业务库」惯例；
* 切档本身写入审计哈希链（由 broker 负责，本模块不碰审计）。

诚实边界：档位是「裁决策略选择器」，不是权限本身。novice 不放大任何
深级别能力（L3-L5 永远需要显式授予，两档一致），fine 不放宽任何路径边界。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

PROFILE_NOVICE = "novice"
PROFILE_FINE = "fine"
PROFILES = (PROFILE_NOVICE, PROFILE_FINE)

_PERSIST_ENV = "FY_CAPABILITY_PROFILE"
_DEFAULT_DIR = ".runtime"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProfileManager:
    """档位运行态：当前档 + 上一档（供一键回退），本地 JSON 持久化。"""

    def __init__(
        self,
        *,
        default: str = PROFILE_NOVICE,
        persist_path: str | os.PathLike | None = None,
        clock=None,
    ) -> None:
        raw = persist_path or os.environ.get(_PERSIST_ENV) or _DEFAULT_DIR
        p = Path(raw)
        self._file = p / "capability_profile.json" if p.is_dir() or p.suffix == "" else p
        self._lock = threading.Lock()
        self._clock = clock or _utcnow
        self._profile = default if default in PROFILES else PROFILE_NOVICE
        self._previous: str | None = None
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self._file.is_file():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        profile = data.get("profile")
        if profile in PROFILES:
            self._profile = profile
        previous = data.get("previous")
        self._previous = previous if previous in PROFILES else None

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profile": self._profile,
            "previous": self._previous,
            "updated_at": self._clock().isoformat(),
        }
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # -- queries -------------------------------------------------------------

    def current(self) -> str:
        with self._lock:
            return self._profile

    def is_fine(self) -> bool:
        return self.current() == PROFILE_FINE

    def previous(self) -> str | None:
        with self._lock:
            return self._previous

    @staticmethod
    def switch_notice(old: str, new: str) -> str:
        """切档提示文案（05：切档有提示）。"""
        if new == PROFILE_FINE:
            return (
                "已切换到细粒度档：零配置隐式放行已全部收回，"
                "每个能力都需按「能力×资源×时限×可撤回」四元组显式授予；"
                "此前可直接使用的沙箱读写现在也需要授予。可随时一键回退小白友好档。"
            )
        return (
            "已切换到小白友好档：沙箱内读写等低风险能力恢复零配置可用；"
            "深级别能力（进程树/桌面注入/驱动级）仍需显式授权，不会自动放开。"
        )

    # -- mutations（owner-only 由 broker 把关）---------------------------------

    def switch(self, profile: str) -> dict:
        """切到目标档位，返回带提示与回退信息的通知。"""
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile!r}")
        with self._lock:
            old = self._profile
            if old == profile:
                return {
                    "changed": False,
                    "from": old,
                    "to": profile,
                    "previous": self._previous,
                    "hint": f"当前已是 {profile} 档，未发生变化。",
                }
            self._previous = old
            self._profile = profile
            self._save()
            return {
                "changed": True,
                "from": old,
                "to": profile,
                "previous": self._previous,
                "hint": self.switch_notice(old, profile),
            }

    def revert(self) -> dict:
        """回退到上一个档位（05：切档可回退）。没有历史时抛 ValueError。"""
        with self._lock:
            if self._previous is None:
                raise ValueError("no previous profile to revert to")
            old = self._profile
            target = self._previous
            self._previous = None
            self._profile = target
            self._save()
            return {
                "changed": True,
                "from": old,
                "to": target,
                "previous": None,
                "hint": self.switch_notice(old, target),
            }
