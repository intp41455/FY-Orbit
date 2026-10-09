"""默认离线运行架构（A-离线优先-01/03）。

产品定义（陛下拍板，不是待决项 · `requirements-inventory-2026-10-05.md:1882`）：

    **默认离线运行架构**：断网可用；仅 LLM API 为唯一外部依赖。

所以本模块的语义是「**默认离线**」，不是「默认在线 + 可切离线」：

* ``FY_OFFLINE_MODE`` 缺省 **True** ⇒ 零配置首启就不去打外网：知识库走本地
  ``hash`` 嵌入 + ``sqlite-vec``，笔记/画布/运行全在本地库，远程模型调用被
  **显式拒绝**并给出降级文案（而不是先连线上、超时了再悄悄退回本地）。
* 只有显式 ``FY_OFFLINE_MODE=0`` 才放开远程调用；此时本模块**不再**猜测网络，
  由调用方自己承担失败——「配了联网就按联网跑」，不做半吊子探测。

诚实边界：本模块是**门**，不是兜底。它不替调用方把远程能力换成别的实现——
没配本地模型时，远程被拒就是被拒（503 + 明确 code），不假装跑成功。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Callable

from ..config import Settings
from ..config import settings as _settings_singleton
from .errors import Conflict

__all__ = [
    "OFFLINE_MODE_ENV",
    "OfflineStatus",
    "OfflineUnavailable",
    "clear_cache",
    "is_offline",
    "offline_mode_enabled",
    "probe_network",
    "remote_block_reason",
    "require_remote",
    "status",
]

#: 架构开关。缺省 True（默认离线）；``FY_OFFLINE_MODE=0`` 才允许远程调用。
OFFLINE_MODE_ENV = "FY_OFFLINE_MODE"

#: 远程被拒时的统一话术（前端直接展示，故写成人话）。
_BLOCK_TEMPLATE = (
    "离线模式（{env}=1）：已禁用远程调用「{feature}」。"
    "本地能力（笔记 / 知识库检索 / 画布运行）不受影响；"
    "需要云端模型时请联网并以 {env}=0 启动。"
)


class OfflineUnavailable(Conflict):
    """离线模式下远程能力被显式拒绝（503 + 稳定 code，不是连接层超时）。

    单独一个类型是为了让 API 层/前端能把它与「网络抖动导致的失败」区分开：
    前者是**设计意图**，要显示「离线模式已禁用远程调用」而不是「网络错误」。
    """

    http_status = 503
    default_code = "offline_mode_remote_blocked"

    def __init__(self, feature: str = "远程模型调用"):
        super().__init__(
            "offline_mode_remote_blocked",
            _BLOCK_TEMPLATE.format(env=OFFLINE_MODE_ENV, feature=feature),
            503,
        )


@dataclass(frozen=True)
class OfflineStatus:
    """离线运行态的对外快照（供 ``GET /api/offline/status`` 与前端指示器）。"""

    offline_mode: bool
    #: 仅在显式联网（``FY_OFFLINE_MODE=0``）时才真去探测；否则为 ``None``
    #: （没探测过就说没探测过，不编一个「有网」）。
    network_reachable: bool | None
    reason: str


def _resolve(settings: Settings | None = None) -> Settings:
    return settings if settings is not None else _settings_singleton()


def offline_mode_enabled(settings: Settings | None = None) -> bool:
    """``FY_OFFLINE_MODE`` 的生效值。缺省（含未设置/非法值）一律 True。"""
    raw = getattr(_resolve(settings), "offline_mode", None)
    if raw is None:
        # 配置对象里没有该字段（旧配置注入）时仍按「默认离线」处理。
        env = os.getenv(OFFLINE_MODE_ENV, "").strip().lower()
        return env not in {"0", "false", "no", "off"}
    return bool(raw)


def is_offline(settings: Settings | None = None) -> bool:
    """是否处于离线模式。**不会抛异常**——调用点可以无条件用它分流。"""
    return offline_mode_enabled(settings)


# 探测结果按 (base_url) 缓存：探测会真的发 DNS/TCP，不能每次检索都做。
_probe_cache: dict[str, bool] = {}


def probe_network(
    settings: Settings | None = None,
    *,
    connect: Callable[[str, int], object] | None = None,
    resolve: Callable[[str], str] | None = None,
) -> bool:
    """真实探测 ``model_base_url`` 主机是否可达（DNS 解析 + TCP 连接）。

    ``connect`` / ``resolve`` 可注入，测试无需真的碰网络（断网验收靠它模拟）。
    """
    s = _resolve(settings)
    base_url = str(getattr(s, "model_base_url", "") or "").strip()
    cache_key = base_url or "<unset>"
    if connect is None and resolve is None and cache_key in _probe_cache:
        return _probe_cache[cache_key]

    host, port = _split_host_port(base_url)
    if not host:
        reachable = False
    else:
        do_resolve = resolve or socket.gethostbyname
        do_connect = connect or (lambda h, p: socket.create_connection((h, p), timeout=2.0))
        try:
            do_resolve(host)
            do_connect(host, port)
            reachable = True
        except Exception:  # noqa: BLE001 — 任何失败都等价于「不可达」
            reachable = False

    if connect is None and resolve is None:
        _probe_cache[cache_key] = reachable
    return reachable


def _split_host_port(base_url: str) -> tuple[str, int]:
    if not base_url:
        return "", 0
    rest = base_url.split("://", 1)[1] if "://" in base_url else base_url
    hostport = rest.split("/", 1)[0]
    if ":" in hostport:
        host, _, port_str = hostport.rpartition(":")
        try:
            return host, int(port_str)
        except ValueError:
            return hostport, 443
    return hostport, 443 if base_url.startswith("https://") else 80


def remote_block_reason(feature: str = "远程模型调用", settings: Settings | None = None) -> str:
    """远程能力被拒的原因文案；允许远程时返回空串（便于 ``if reason:`` 分流）。"""
    if not is_offline(settings):
        return ""
    return _BLOCK_TEMPLATE.format(env=OFFLINE_MODE_ENV, feature=feature)


def require_remote(feature: str = "远程模型调用", settings: Settings | None = None) -> None:
    """远程能力门：离线模式下抛 :class:`OfflineUnavailable`（显式降级，不静默）。"""
    reason = remote_block_reason(feature, settings)
    if reason:
        raise OfflineUnavailable(feature)


def status(settings: Settings | None = None) -> OfflineStatus:
    """离线运行态快照。只在「显式联网」时才真去探测网络。"""
    if not is_offline(settings):
        reachable = probe_network(settings)
        reason = (
            "已显式联网（FY_OFFLINE_MODE=0）：远程调用可用。"
            if reachable
            else "已显式联网（FY_OFFLINE_MODE=0），但目标端点当前不可达：远程调用会失败。"
        )
        return OfflineStatus(offline_mode=False, network_reachable=reachable, reason=reason)
    return OfflineStatus(
        offline_mode=True,
        network_reachable=None,
        reason=(
            "离线模式（默认）：不打外网。本地库 / 本地嵌入 / 本地向量库照常，"
            "远程模型调用被显式拒绝并给出降级提示。"
        ),
    )


def clear_cache() -> None:
    """清空探测缓存（测试或配置热更后调用）。"""
    _probe_cache.clear()
