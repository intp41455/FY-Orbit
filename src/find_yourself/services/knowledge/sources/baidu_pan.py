"""W3 · 百度网盘适配器 **骨架（v1 不实现拉取）**。

任务书 §2.4 明确：百度网盘 v1 只做骨架，**不伪装可用**。因此：

* ``health_check`` 恒为 ``available=False`` + ``detail`` 说明缺什么
  （开发者平台 App Key / 授权码流程 / 增量游标）；
* ``list_sources`` / ``fetch_document`` / ``search_metadata`` 一律抛
  ``ValidationFailed('baidu_pan_not_implemented')``，前端据此显示「未接入（待实现）」，
  绝不会返回空列表或假条目让人误以为「已接入但没有内容」；
* ``oauth_authorize_url`` 返回**占位**说明文本并说明为什么不能给出真 URL
  （缺 App Key 与已备案的回调地址），而不是编一个看起来能用的链接。

后续实现路径（对齐设计文档 §2）：OAuth2.0 授权码 → refresh_token 本机加密落盘 →
文件级下载进切分管线 → ``iter_changes`` 基于 mtime + fs_id 增量。
"""

from __future__ import annotations

from typing import Any, Iterator

from ...errors import ValidationFailed
from .base import KnowledgeSource, RawDocument, SourceCapabilities, SourceRef

NOT_IMPLEMENTED = "baidu_pan_not_implemented"


class BaiduPanSource(KnowledgeSource):
    source_id = "baidu_pan"
    display_name = "百度网盘"
    capabilities = SourceCapabilities(
        searchable=False, full_text=True, incremental=False, retryable=True
    )

    def __init__(self, *, app_key: str = "", app_secret: str = "", redirect_uri: str = ""):
        self.app_key = (app_key or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.redirect_uri = (redirect_uri or "").strip()

    def is_configured(self) -> bool:
        # v1 即便填了凭证也不具备可用能力，故 configured 永远为 False。
        return False

    def require_configured(self) -> None:
        """报「未实现」而不是「未配置」——两者都不可用，但对用户要说清真实原因。"""
        raise ValidationFailed(
            NOT_IMPLEMENTED,
            "百度网盘适配器尚未实现（v1 仅骨架），配置凭证也不会让它可用",
        )

    def health_check(self, *, probe: bool = False) -> dict[str, Any]:
        return {
            "available": False,
            "configured": False,
            "degraded": False,
            "latency_ms": None,
            "detail": (
                "未接入：百度网盘适配器 v1 仅有骨架。"
                "待实现 OAuth2.0 授权码流程（需百度网盘开放平台 App Key/Secret 与已备案回调地址）、"
                "refresh_token 本机加密落盘、文件级下载与 mtime+fs_id 增量同步。"
                "在此之前本适配器不会返回任何数据。"
            ),
        }

    def list_sources(self) -> list[SourceRef]:
        raise ValidationFailed(
            NOT_IMPLEMENTED,
            "百度网盘适配器尚未实现（v1 仅骨架）：不能列出目录，也不会返回空列表冒充已接入",
        )

    def fetch_document(self, ref: SourceRef) -> Iterator[RawDocument]:
        # 故意**不是**生成器函数：生成器在被迭代前不会执行函数体，调用方会拿到一个
        # “空迭代器”并误以为「已接入但没内容」。这里必须当场报错。
        raise ValidationFailed(
            NOT_IMPLEMENTED, "百度网盘适配器尚未实现（v1 仅骨架）：不能拉取文档"
        )

    def search_metadata(self, ref: SourceRef, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise ValidationFailed(
            NOT_IMPLEMENTED, "百度网盘适配器尚未实现（v1 仅骨架）：无服务端检索"
        )

    def oauth_authorize_url(self) -> str:
        raise ValidationFailed(
            NOT_IMPLEMENTED,
            "百度网盘 OAuth 跳转未实现：需先在开放平台申请 App Key/Secret 并配置已备案的回调地址，"
            "本项目不提供占位授权链接（点开必然失败）",
        )