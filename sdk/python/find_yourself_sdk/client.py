"""FindYourself 标准客户端核心（A-生态兼容-01）。"""

from __future__ import annotations

from typing import Any
import requests

from .embed import generate_iframe_embed_url
from .models import (
    ContextSlice,
    PluginCard,
    RatingSummary,
    SecurityReviewReport,
    TemplateCard,
)


class FindYourselfClient:
    """FindYourself 主 SDK 客户端。"""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        token: str | None = None,
        csrf_token: str | None = None,
        tenant_id: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.csrf_token = csrf_token
        self.tenant_id = tenant_id
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, requires_csrf: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        requires_csrf: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.request(
            method,
            url,
            params=params,
            json=json,
            headers=self._headers(requires_csrf=requires_csrf),
            timeout=self.timeout,
        )
        if not resp.ok:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"HTTP {resp.status_code} Error: {detail}")
        return resp.json()

    # -----------------------------------------------------------------------
    # 插件市场
    # -----------------------------------------------------------------------

    def list_plugins(
        self,
        query: str = "",
        sort_by: str = "score",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = {"query": query, "sort_by": sort_by, "limit": limit, "offset": offset}
        return self._request("GET", "/api/plugins/marketplace", params=params)

    def get_plugin(self, skill_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/plugins/marketplace/{skill_id}")

    def rate_plugin(self, skill_id: str, rating: float, comment: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/plugins/marketplace/{skill_id}/rate",
            json={"rating": rating, "comment": comment},
            requires_csrf=True,
        )

    def get_plugin_ratings(self, skill_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/plugins/marketplace/{skill_id}/ratings",
            params={"limit": limit, "offset": offset},
        )

    # -----------------------------------------------------------------------
    # 模板体系与市场
    # -----------------------------------------------------------------------

    def get_template_hierarchy(self) -> dict[str, Any]:
        return self._request("GET", "/api/plugins/templates/hierarchy")

    def list_templates(
        self,
        query: str = "",
        scenario: str | None = None,
        layer: str | None = None,
        sort_by: str = "score",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = {
            "query": query,
            "scenario": scenario,
            "layer": layer,
            "sort_by": sort_by,
            "limit": limit,
            "offset": offset,
        }
        # 移除 None 参数
        params = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", "/api/plugins/templates/market", params=params)

    def preview_switch_impact(self, from_id: str, to_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/plugins/templates/impact-preview",
            json={"from_template_id": from_id, "to_template_id": to_id},
        )

    def export_template(self, template_id: str, author: str | None = None, notes: str | None = None) -> dict[str, Any]:
        params = {}
        if author:
            params["author"] = author
        if notes:
            params["notes"] = notes
        return self._request("GET", f"/api/plugins/templates/{template_id}/export", params=params)

    def security_review_template(self, package_doc: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/plugins/templates/security-review", json=package_doc)

    def import_template(
        self,
        package_doc: dict[str, Any],
        confirmed_tools: list[str] | None = None,
        custom_name: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/plugins/templates/import",
            json={
                "package": package_doc,
                "confirmed_tools": confirmed_tools,
                "custom_name": custom_name,
            },
            requires_csrf=True,
        )

    def rate_template(self, template_id: str, rating: float, comment: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/plugins/templates/{template_id}/rate",
            json={"rating": rating, "comment": comment},
            requires_csrf=True,
        )

    # -----------------------------------------------------------------------
    # Cursor 级代码上下文
    # -----------------------------------------------------------------------

    def analyze_code_symbols(self, file_path: str, code: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/plugins/cursor/analyze",
            json={"file_path": file_path, "code": code},
        )

    def search_code_context(self, query: str, files: dict[str, str], limit: int = 10) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/plugins/cursor/search",
            json={"query": query, "files": files, "limit": limit},
        )

    def assemble_code_context(self, query: str, files: dict[str, str], max_chars: int = 4000) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/plugins/cursor/assemble",
            json={"query": query, "files": files, "max_chars": max_chars},
        )

    # -----------------------------------------------------------------------
    # 企业嵌入与单点辅助
    # -----------------------------------------------------------------------

    def generate_embed_url(
        self,
        page: str = "marketplace",
        user_id: str | None = None,
        secret_key: str | None = None,
        theme: str = "light",
    ) -> str:
        return generate_iframe_embed_url(
            self.base_url,
            page=page,
            tenant_id=self.tenant_id,
            user_id=user_id,
            token=self.token,
            secret_key=secret_key,
            theme=theme,
        )
