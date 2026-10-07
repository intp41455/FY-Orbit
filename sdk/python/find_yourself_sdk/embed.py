"""企业嵌入辅助工具（WebApp iframe 嵌入 + 多租户 + SSO 支持 · A-生态兼容-01）。

支持企业系统将 FindYourself 工作台、插件市场或模板中心安全嵌入至自有 WebApp 中。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode


def generate_iframe_embed_url(
    base_url: str,
    page: str = "marketplace",  # 'marketplace' | 'templates' | 'workbench' | 'observability'
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    token: str | None = None,
    secret_key: str | None = None,
    theme: str = "light",
    locale: str = "zh-CN",
    read_only: bool = False,
) -> str:
    """生成企业 WebApp 嵌入用带有安全签名的 iframe URL。"""
    params: dict[str, str] = {
        "embed": "true",
        "theme": theme,
        "locale": locale,
    }
    if read_only:
        params["read_only"] = "true"
    if tenant_id:
        params["tenant_id"] = tenant_id
    if user_id:
        params["user_id"] = user_id
    if token:
        params["token"] = token

    # 如果提供企业共享秘钥，计算 HMAC-SHA256 防伪签名
    if secret_key and user_id:
        timestamp = str(int(time.time()))
        params["ts"] = timestamp
        message = f"{tenant_id or ''}:{user_id}:{timestamp}".encode("utf-8")
        sig = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
        params["sig"] = sig

    clean_base = base_url.rstrip("/")
    path_map = {
        "marketplace": "/plugins",
        "templates": "/templates",
        "workbench": "/workbench",
        "observability": "/observability",
    }
    route = path_map.get(page, f"/{page.lstrip('/')}")
    qs = urlencode(params)
    return f"{clean_base}{route}?{qs}"


def verify_embed_signature(
    tenant_id: str | None,
    user_id: str,
    timestamp: str,
    signature: str,
    secret_key: str,
    max_drift_seconds: int = 300,
) -> bool:
    """验证企业 iframe 嵌入请求的合法性与时效性。"""
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    now = int(time.time())
    if abs(now - ts_int) > max_drift_seconds:
        return False

    message = f"{tenant_id or ''}:{user_id}:{timestamp}".encode("utf-8")
    expected = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
