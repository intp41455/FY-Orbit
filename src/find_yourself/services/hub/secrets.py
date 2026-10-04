"""Hub 凭证存储：Fernet 加密落盘 + 内存回退（W6 增补 E）。

W3 原来的 ``InMemorySecretStore`` 是**进程内存**的，进程重启即失效。增补 E 要求
知识源凭证迁到 hub 的 Fernet 加密存储，重启不丢。本模块就是那个存储。

双模式与诚实标注（主控裁决 2026-10-04 第 3 条，硬要求）
-------------------------------------------------------
* **加密落盘可用** → ``storage_mode() == ("hub_fernet", True)``
* **不可用**（主密钥缺失 / cryptography 缺失 / 磁盘不可写 / 密文解不开）
  → 自动回退内存，``storage_mode() == ("memory", False)``

``storage_mode()`` 的返回值必须**永远匹配真实存储位置**：写了盘才报落盘，
只进内存就报内存。谎报「已持久化」比不持久化更危险（用户会以为重启还在）。

落盘文件结构（整个文件是密文 envelope，值逐个 Fernet 加密）::

    {"version": 1,
     "scopes": {"knowledge:ima": {"api_key": "enc:v1:...", "base_url": "enc:v1:..."}}}

文件路径：``FY_HUB_SECRETS_PATH`` 环境变量，默认 ``.runtime/hub/secrets.enc.json``。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .crypto import (
    HubCryptoUnavailable,
    cipher_available,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)

DEFAULT_PATH = ".runtime/hub/secrets.enc.json"
_ENV_PATH = "FY_HUB_SECRETS_PATH"

MODE_FERNET = "hub_fernet"
MODE_MEMORY = "memory"


class HubSecretStore:
    """Encrypted at-rest credential store with an honest in-memory fallback.

    Public interface is deliberately identical to W3's ``InMemorySecretStore``
    (``set`` / ``get`` / ``has`` / ``forget`` / ``masked``) so it can act as a
    drop-in shell, plus ``storage_mode()`` for honest UI reporting.
    """

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self._path = Path(path or os.environ.get(_ENV_PATH) or DEFAULT_PATH)
        self._lock = threading.RLock()
        # 回退层：加密/落盘不可用时凭证仍然存在（进程内），但不假装持久化。
        self._memory: dict[str, dict[str, str]] = {}
        self._degraded_reason = ""

    # -- introspection ------------------------------------------------------ #
    @property
    def path(self) -> Path:
        return self._path

    def degraded_reason(self) -> str:
        """Why we fell back to memory. Empty string while encryption works."""
        return self._degraded_reason

    def storage_mode(self) -> tuple[str, bool]:
        """``(storage_label, persist_restart)`` — must match reality."""
        if not cipher_available():
            return (MODE_MEMORY, False)
        if self._degraded_reason:
            return (MODE_MEMORY, False)
        return (MODE_FERNET, True)

    # -- file io ------------------------------------------------------------ #
    def _read_file(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        scopes = raw.get("scopes")
        return scopes if isinstance(scopes, dict) else {}

    def _write_file(self, scopes: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "scopes": scopes}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # -- store API (same shape as InMemorySecretStore) ----------------------- #
    def set(self, scope: str, values: dict[str, str]) -> None:
        from ..errors import ValidationFailed

        cleaned = {k: (v or "").strip() for k, v in dict(values or {}).items()}
        if not any(cleaned.values()):
            raise ValidationFailed(
                "empty_credentials", "凭证为空：至少需要填写 API Key 或 Base URL"
            )
        with self._lock:
            try:
                enc = {k: encrypt_secret(v) for k, v in cleaned.items()}
            except HubCryptoUnavailable as exc:
                self._memory[scope] = cleaned
                self._degraded_reason = str(exc.message)
                return
            try:
                scopes = self._read_file()
                scopes[scope] = enc
                self._write_file(scopes)
            except OSError as exc:
                # 磁盘不可写：进程内仍然可用，但必须如实标注为内存。
                self._memory[scope] = cleaned
                self._degraded_reason = f"凭证落盘失败，已回退内存：{exc.__class__.__name__}"
                return
            self._memory.pop(scope, None)
            self._degraded_reason = ""

    def get(self, scope: str) -> dict[str, str]:
        with self._lock:
            enc = self._read_file().get(scope)
            if isinstance(enc, dict):
                try:
                    return {k: decrypt_secret(v) for k, v in enc.items()}
                except HubCryptoUnavailable:
                    # 解不开（主密钥轮换过）：宁可报内存回退，也不返回半个凭证。
                    pass
            return dict(self._memory.get(scope, {}))

    def has(self, scope: str) -> bool:
        return any(self.get(scope).values())

    def forget(self, scope: str) -> bool:
        with self._lock:
            removed = self._memory.pop(scope, None) is not None
            scopes = self._read_file()
            if scope in scopes:
                scopes.pop(scope)
                try:
                    self._write_file(scopes)
                except OSError:
                    pass
                removed = True
            return removed

    def masked(self, scope: str) -> dict[str, bool]:
        """Which credential fields are present — never the values."""
        return {k: bool(v) for k, v in self.get(scope).items()}

    def mask_map(self, scope: str) -> dict[str, str]:
        """Masked *shapes* for the UI (``api_key -> sk-a****23``).

        Values are masked locally; the plaintext never leaves this method.
        """
        return {k: mask_secret(v) for k, v in self.get(scope).items()}


#: Process-wide singleton used by the knowledge adapters (W3 bridge, 增补 E).
hub_secret_store = HubSecretStore()
