"""W3 知识源注册表 + 凭证存储。

凭证策略（W6 增补 E 后的现状，主控裁决 2026-10-04）
--------------------------------------------------
凭证改由 **hub 的 Fernet 加密存储** 承载（``services/hub/secrets.py``），
``secret_store`` 是它的**接口壳**：``set/get/has/forget/masked`` 签名与原来的
``InMemorySecretStore`` 完全一致，因此 W3 的所有调用方与测试语义不变，
但凭证**重启不丢**，且落盘的是密文（``enc:v1:...``），明文不入库、不回显。

双模式与诚实标注（硬要求）：

* 加密可用且写盘成功 → ``storage="hub_fernet"`` / ``persist_restart=True``；
* 加密不可用（主密钥缺失 / 依赖缺失 / 磁盘不可写 / 密文解不开）
  → 自动回退进程内存，并如实报 ``storage="memory"`` / ``persist_restart=False``。

``storage_mode()`` 的返回值**永远匹配真实存储位置**：绝不谎报已持久化。
``InMemorySecretStore`` 保留为回退引擎，不再是默认实现。
环境变量 ``FY_IMA_API_KEY`` / ``FY_IMA_BASE_URL`` 仍可作为开机预置。
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from ...errors import NotFound, ValidationFailed
from ...hub.secrets import HubSecretStore
from .baidu_pan import BaiduPanSource
from .base import (
    KnowledgeSource,
    RawDocument,
    SourceCapabilities,
    SourceRef,
    UnsupportedCapability,
    retry_call,
)
from .ima import ImaSource

__all__ = [
    "KnowledgeSource",
    "SourceCapabilities",
    "SourceRef",
    "RawDocument",
    "UnsupportedCapability",
    "retry_call",
    "ImaSource",
    "BaiduPanSource",
    "HubSecretStore",
    "InMemorySecretStore",
    "SecretStoreProtocol",
    "SOURCE_STATUS",
    "secret_store",
    "build_source",
    "list_source_status",
]


class SecretStoreProtocol(Protocol):
    """W3 适配器与 hub 桥接（``hub_bridge``）共同依赖的最小凭证接口。

    ``InMemorySecretStore``（回退引擎）与 ``HubSecretStore``（默认实现）都满足它；
    桥接层因此能注入测试替身，而不必知道底下到底是加密落盘还是进程内存。
    """

    def set(self, scope: str, values: dict[str, str]) -> None: ...
    def get(self, scope: str) -> dict[str, str]: ...
    def has(self, scope: str) -> bool: ...
    def forget(self, scope: str) -> bool: ...
    def masked(self, scope: str) -> dict[str, bool]: ...


class InMemorySecretStore:
    """Process-local credential holder. Never persisted, never returned verbatim.

    Kept as the **fallback engine** behind :class:`HubSecretStore` (W6 增补 E)：
    hub 加密存储不可用时，凭证仍然只在进程内存里，并由 ``storage_mode()``
    如实标注为 ``memory`` / 重启即失效。
    """

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}

    def set(self, source_id: str, values: dict[str, str]) -> None:
        cleaned = {k: (v or "").strip() for k, v in values.items()}
        if not any(cleaned.values()):
            raise ValidationFailed(
                "empty_credentials", "凭证为空：至少需要填写 API Key 或 Base URL"
            )
        self._values[source_id] = cleaned

    def get(self, source_id: str) -> dict[str, str]:
        return dict(self._values.get(source_id, {}))

    def has(self, source_id: str) -> bool:
        return any(self._values.get(source_id, {}).values())

    def forget(self, source_id: str) -> bool:
        return self._values.pop(source_id, None) is not None

    def masked(self, source_id: str) -> dict[str, bool]:
        """Which credential fields are present — never the values."""
        return {k: bool(v) for k, v in self._values.get(source_id, {}).items()}


#: 默认凭证存储 = hub 的 Fernet 加密存储（接口壳，签名与 InMemorySecretStore 一致）。
secret_store = HubSecretStore()

SOURCE_STATUS: dict[str, dict[str, str]] = {
    "ima": {
        "display_name": "ima 知识库",
        "credential_fields": "api_key,base_url",
        "hint": "在 ima 开放平台创建个人应用，取 API Key 与 API Base URL 填入",
    },
    "baidu_pan": {
        "display_name": "百度网盘",
        "credential_fields": "app_key,app_secret,redirect_uri",
        "hint": "v1 仅骨架，未接入",
    },
}


def build_source(source_id: str) -> KnowledgeSource:
    """Instantiate an adapter from memory store + environment fallback."""
    if source_id == "ima":
        creds = secret_store.get("ima")
        return ImaSource(
            api_key=creds.get("api_key") or os.environ.get("FY_IMA_API_KEY", ""),
            base_url=creds.get("base_url") or os.environ.get("FY_IMA_BASE_URL", ""),
        )
    if source_id == "baidu_pan":
        creds = secret_store.get("baidu_pan")
        return BaiduPanSource(
            app_key=creds.get("app_key", ""),
            app_secret=creds.get("app_secret", ""),
            redirect_uri=creds.get("redirect_uri", ""),
        )
    raise NotFound("unknown_source", f"未知知识源：{source_id}")


def list_source_status(*, probe: bool = False) -> list[dict[str, Any]]:
    """Adapter cards payload for the UI. Honest about what is NOT connected.

    ``storage`` / ``persist_restart`` 取自 ``secret_store.storage_mode()``，
    永远匹配凭证的**真实**存放位置（W6 增补 E 的诚实性硬要求）。
    """
    storage, persist_restart = secret_store.storage_mode()
    out: list[dict[str, Any]] = []
    for source_id in sorted(SOURCE_STATUS):
        meta = SOURCE_STATUS[source_id]
        fields = [f.strip() for f in meta["credential_fields"].split(",") if f.strip()]
        source = build_source(source_id)
        status = source.status(probe=probe)
        stored = secret_store.get(source_id)
        # ima.py 的 health_check 文案仍写着「仅存内存，不入库」——W6 之前的行为。
        # 该文案在 ima.py 里（禁改文件），这里做定点替换以免对用户撒谎；
        # 若上游文案被改，替换不生效会退化为旧文案，故同步在报告里列为未尽事项。
        detail = str(status.get("detail") or "").replace(
            "（仅存内存，不入库）", f"（凭证加密存储：{storage}）"
        )
        status.update(
            {
                "credential_fields": fields,
                "hint": meta["hint"],
                # 只报「哪些字段已填」，绝不回显值。
                "credentials_present": {f: bool(stored.get(f)) for f in fields},
                "storage": storage,
                "persist_restart": persist_restart,
                "detail": detail,
            }
        )
        out.append(status)
    return out