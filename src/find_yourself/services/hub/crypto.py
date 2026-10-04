"""Fernet 凭证加密（W6 任务书 §3）。

主密钥派生（任务书明文指定，docstring 留证）::

    key = base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())

``session_secret`` 取自 ``FY_SESSION_SECRET``（``config.Settings``，≥32 字符，
生产环境由部署脚本注入）。密文入库 / 落盘，**明文凭证绝不出现在日志、API 响应、
或任何对外结构体里**；对外只暴露 :func:`mask_secret` 产生的掩码。

派生自会话密钥是有代价的：轮换 ``FY_SESSION_SECRET`` 会让既有密文不可解密。
这是 v1 的明确取舍（不引入独立 KMS），解密失败一律显式报错而不是静默降级，
以免「解不开」被误当成「没配」。
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from ..errors import DomainError

#: 密文前缀。带版本号，便于未来换算法时区分而不猜。
CIPHER_PREFIX = "enc:v1:"


class HubCryptoUnavailable(DomainError):
    """主密钥缺失 / cryptography 不可用 —— 无法安全加密。

    调用方必须显式处理：要么拒绝写入，要么回退到内存并**如实标注**，
    绝不允许「以为加密了其实没加密」。
    """

    http_status = 503
    default_code = "hub_crypto_unavailable"


def _session_secret() -> str:
    """Read ``FY_SESSION_SECRET``; return "" when Settings itself is unusable.

    ``Settings()`` validates the secret length at construction, so in an
    environment without one it *raises* rather than returning a short value.
    Swallowing that here turns it into the honest "cannot encrypt" path below
    instead of an unrelated 500 deep inside a credential write.
    """
    from ...config import settings

    try:
        return (settings().session_secret or "").strip()
    except Exception:  # noqa: BLE001 — 配置不可用 = 加密不可用，由调用方如实降级
        return ""


@lru_cache(maxsize=1)
def _fernet():
    """Build (and memoise) the Fernet cipher from the derived master key."""
    from cryptography.fernet import Fernet

    secret = _session_secret()
    if len(secret) < 32:
        raise HubCryptoUnavailable(
            "hub_crypto_unavailable",
            "FY_SESSION_SECRET 缺失或短于 32 字符，无法派生凭证加密主密钥；"
            "凭证将只保存在内存中（不落盘）",
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def cipher_available() -> bool:
    """Whether credentials can be encrypted right now (no side effects)."""
    try:
        _fernet()
    except HubCryptoUnavailable:
        return False
    except Exception:  # noqa: BLE001 — cryptography 缺失等环境级故障
        return False
    return True


def encrypt_secret(plain: str) -> str:
    """Encrypt one secret. Raises :class:`HubCryptoUnavailable` when impossible."""
    token = _fernet().encrypt((plain or "").encode("utf-8"))
    return CIPHER_PREFIX + token.decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt one secret produced by :func:`encrypt_secret`.

    A token we cannot decrypt raises :class:`HubCryptoUnavailable` — never
    returns an empty string, which would be indistinguishable from "unset".
    """
    raw = str(token or "")
    if not raw.startswith(CIPHER_PREFIX):
        raise HubCryptoUnavailable(
            "hub_crypto_not_ciphertext", "凭证不是本系统产生的密文，拒绝按明文解读"
        )
    plain = _fernet().decrypt(raw[len(CIPHER_PREFIX):].encode("ascii"))
    return plain.decode("utf-8")


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(CIPHER_PREFIX)


def mask_secret(secret: str) -> str:
    """UI/API-safe mask. Never reveals enough to reconstruct the value."""
    text = secret or ""
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:3]}****{text[-2:]}"
