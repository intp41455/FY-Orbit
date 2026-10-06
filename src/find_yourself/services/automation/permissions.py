"""GUI 自动化权限门 (W10-B).

本模块是「眼睛和手」能力的**唯一放行处**。设计原则（任务书 §W10-B.2）：

* **默认全关**：进程启动时档位为 :data:`MODE_OFF`，任何 ``automation.*`` 工具调用
  都被拒。绝不默认开启。
* **三档显式开关**：``readonly``（只读屏幕）/ ``safe``（移动光标）/ ``full``
  （点击/打字/按键 = SYSTEM_FS 级注入）。档位顺序见 :data:`MODE_ORDER`。
* **full 档必须带 TTL**：不允许永久完全控制；到期自动回落到 ``off``。
  这是参照 auth 五维作用域凭据的「短时效、显式确认」思路。
* **每次放行或拒绝都产生一条审计事件**（交给注入的 ``on_event`` 回调；
  默认落到 ``.runtime/automation_audit.jsonl``）。

持久化刻意用本地 JSON（本地优先，无迁移），不写数据库：这是**这台机器本机**
的控制开关，不是跨租户业务数据，也因此绝不能进 ``db/models.py``（Core 所有权）。

诚实边界：本模块不关心工具到底做了什么，只负责「这一档位是否允许该风险级」。
真正的系统调用在 :mod:`find_yourself.services.automation.backend`，且在测试里
全部被替换成 fake。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..errors import PermissionDenied

#: 四态：off 是「未开启」基线；其余三档是用户显式选择。
MODE_OFF = "off"
MODE_READONLY = "readonly"
MODE_SAFE = "safe"
MODE_FULL = "full"

#: 档位权重，越大越放权。用于比较「当前档是否够解锁某工具」。
MODE_ORDER: dict[str, int] = {
    MODE_OFF: 0,
    MODE_READONLY: 1,
    MODE_SAFE: 2,
    MODE_FULL: 3,
}

#: full 档默认 TTL（秒）。完全控制是高风险注入权，短时效 + 到期回落。
DEFAULT_FULL_TTL_SECONDS = 15 * 60
#: full 档 TTL 上限（秒）。不允许把完全控制挂成几小时。
MAX_FULL_TTL_SECONDS = 60 * 60

_PERSIST_ENV = "FY_AUTOMATION_PERMS"
_DEFAULT_DIR = ".runtime"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AutomationMode:
    """当前权限档位的一个不可变快照（含 TTL）。"""

    mode: str
    enabled_at: datetime | None
    expires_at: datetime | None

    @property
    def weight(self) -> int:
        return MODE_ORDER.get(self.mode, 0)

    def is_expired(self, now: datetime | None = None) -> bool:
        # 只有 full 档带 TTL；其它档不需要过期（它们本就不注入）。
        # now 由 manager 注入（可测试时钟）；缺省才用真实 UTC 墙钟。
        if self.mode != MODE_FULL or self.expires_at is None:
            return False
        current = now or _utcnow()
        return current >= self.expires_at

    def effective_mode(self, now: datetime | None = None) -> str:
        """TTL 过期后，完全控制自动回落为 off。"""
        if self.is_expired(now):
            return MODE_OFF
        return self.mode


#: 审计事件回调签名。``action`` 形如 ``automation.screenshot``；
#: ``allowed`` 为 False 表示这是一次被权限门拦截的调用。
OnEvent = Callable[[str, dict, bool], None]


def _default_audit_log(path: Path) -> OnEvent:
    def _write(action: str, details: dict, allowed: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utcnow().isoformat(),
            "action": action,
            "details": details,
            "allowed": allowed,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return _write


class AutomationPermissionManager:
    """进程内权限门，带本地 JSON 持久化与审计回调。

    Parameters
    ----------
    persist_path:
        权限状态文件。测试传入 tmp_path 下的文件以隔离。
    on_event:
        每次放行/拒绝都会回调。默认追加到 ``.runtime/automation_audit.jsonl``。
    clock:
        可注入的「现在」时钟（测试用），返回 timezone-aware datetime。
    """

    def __init__(
        self,
        *,
        persist_path: str | os.PathLike | None = None,
        on_event: OnEvent | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        raw = persist_path or os.environ.get(_PERSIST_ENV) or _DEFAULT_DIR
        p = Path(raw)
        # 允许传「目录」或「文件」两种形态，统一成文件路径。
        self._file = p / "automation_permissions.json" if p.is_dir() or p.suffix == "" else p
        self._lock = threading.Lock()
        self._clock = clock
        self._mode = AutomationMode(mode=MODE_OFF, enabled_at=None, expires_at=None)
        self._on_event = on_event or _default_audit_log(
            Path(os.environ.get(_PERSIST_ENV, _DEFAULT_DIR)) / "automation_audit.jsonl"
        )
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._file.is_file():
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        mode = data.get("mode", MODE_OFF)
        if mode not in MODE_ORDER:
            mode = MODE_OFF

        def _parse(key: str) -> datetime | None:
            raw = data.get(key)
            if not raw:
                return None
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        self._mode = AutomationMode(
            mode=mode,
            enabled_at=_parse("enabled_at"),
            expires_at=_parse("expires_at"),
        )

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": self._mode.mode,
            "enabled_at": self._mode.enabled_at.isoformat() if self._mode.enabled_at else None,
            "expires_at": self._mode.expires_at.isoformat() if self._mode.expires_at else None,
        }
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # -- public API ------------------------------------------------------------

    def current(self) -> AutomationMode:
        """返回当前（含 TTL 过期回落）的档位快照。"""
        now = self._clock()
        with self._lock:
            eff = self._mode.effective_mode(now=now)
            if eff != self._mode.mode:
                # TTL 过期：把回落持久化下来，避免每次都重算。
                self._mode = AutomationMode(mode=eff, enabled_at=None, expires_at=None)
                self._save()
            return self._mode

    def set_mode(self, mode: str, *, ttl_seconds: int | None = None) -> AutomationMode:
        """切换档位。

        * ``off``：立即收回全部权限。
        * ``readonly`` / ``safe``：立即生效，无需 TTL（不注入鼠标键盘）。
        * ``full``：**必须**由调用方显式给 ``ttl_seconds``；不给就拒绝。
          TTL 越界会被钳制到 ``[60, MAX_FULL_TTL_SECONDS]``。
        """
        if mode not in MODE_ORDER:
            raise PermissionDenied(
                "automation_bad_mode",
                f"Unknown automation mode '{mode}'; expected one of {sorted(MODE_ORDER)}",
                422,
            )
        now = self._clock()
        expires_at: datetime | None = None
        if mode == MODE_FULL:
            if ttl_seconds is None:
                raise PermissionDenied(
                    "automation_full_requires_ttl",
                    "Full GUI control must be explicitly granted with a TTL "
                    "(short-lived). Permanent 'full' is refused.",
                    403,
                )
            ttl = max(60, min(int(ttl_seconds), MAX_FULL_TTL_SECONDS))
            expires_at = now + timedelta(seconds=ttl)
        with self._lock:
            self._mode = AutomationMode(
                mode=mode,
                enabled_at=now if mode != MODE_OFF else None,
                expires_at=expires_at,
            )
            self._save()
        self._on_event(
            "automation.mode_change",
            {"mode": mode, "expires_at": expires_at.isoformat() if expires_at else None},
            True,
        )
        return self._mode

    def verdict(self, action: str, *, min_mode: str, tool_level: int) -> tuple[bool, str]:
        """非抛出版权限判定：返回 ``(allowed, reason)``。

        补齐包1（A-能力网关-01）收编点：统一能力网关的 AutomationModeGate
        用本方法取这一路的裁决输入，语义与 :meth:`require` 完全一致
        （权重比较 + full 档 TTL 过期回落）。``require`` 现在也复用本判定，
        保证「直接调用」与「经网关裁决」永远同一真相，不会漂移成两套门。
        """
        current = self.current()
        allowed = current.weight >= MODE_ORDER[min_mode]
        if not allowed:
            return False, (
                f"GUI automation mode '{min_mode}' required, currently "
                f"'{current.mode}' (tool_level={tool_level})"
            )
        return True, f"automation mode '{current.mode}' permits (min '{min_mode}')"

    def require(self, action: str, *, min_mode: str, tool_level: int) -> None:
        """权限门：当前档是否允许执行 ``action``。不允许则抛 PermissionDenied。

        Parameters
        ----------
        action:
            工具名（审计用），如 ``automation.click``。
        min_mode:
            解锁该工具所需的最低档位（:data:`MODE_READONLY` 等）。
        tool_level:
            harness L0-L6 的 required_level（仅用于审计与说明，不单独判定，
            因为档位与风险类的映射在本服务里集中管理，避免两处真相）。
        """
        current = self.current()
        allowed, reason = self.verdict(action, min_mode=min_mode, tool_level=tool_level)
        details = {
            "min_mode": min_mode,
            "current_mode": current.mode,
            "tool_level": tool_level,
        }
        if not allowed:
            self._on_event(action, {**details, "reason": "mode_insufficient"}, False)
            raise PermissionDenied(
                "automation_permission_denied",
                f"'{action}' requires GUI automation mode '{min_mode}' "
                f"(currently '{current.mode}'). Enable it in Settings → System Permissions; "
                f"'full' control is time-limited by design.",
                403,
            )
        # 放行也记一笔（动作本体的参数由工具 handler 另行补记）。
        self._on_event(action, details, True)

    def record_executed(self, action: str, details: dict) -> None:
        """工具真正执行后补记一笔业务审计（含真实参数摘要）。"""
        self._on_event(action, details, True)
