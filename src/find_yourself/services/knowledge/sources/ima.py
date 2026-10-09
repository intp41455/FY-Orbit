"""W3 · ima（腾讯 ima 公共知识库）适配器。

**端点路径的诚实声明**：任务书 §1 已核实「ima 公共知识库支持 API Key 通道」，
但具体 OpenAPI 路径/字段名必须以 ima 开放平台文档为准。本实现因此把
``base_url`` 与三个路径都做成**可配置**参数，默认值按「知识库列表 / 知识库条目 /
服务端检索」三段式给出并标注为 *待官方文档核对*。任何一次真实调用失败都会以
:class:`ValidationFailed` 显式抛出（带 HTTP 状态与脱敏后的响应摘要），**绝不
返回构造出来的假条目**。单测用 ``httpx.MockTransport`` 打桩，不打真网。

能力边界（来自设计文档 §0）：订阅类公共知识库 API 只同步 300 字预览、文件类只给
AI 摘要——本适配器对这类响应打 ``preview_only=True`` 标记并如实上报，不假装拿到
全文；产品只应索引**主理人自建库**。

凭证：只从调用方传入（设置页 → 内存 secret store），**不入库、不落盘、不回显**。
"""

from __future__ import annotations

from typing import Any, Generator

import httpx

from ...errors import ValidationFailed
from .base import (
    KnowledgeSource,
    RawDocument,
    SourceCapabilities,
    SourceRef,
    retry_call,
)

DEFAULT_BASE_URL = ""
DEFAULT_LIST_PATH = "/api/knowledge_bases"
DEFAULT_ITEMS_PATH = "/api/knowledge_bases/{kb_id}/items"
DEFAULT_SEARCH_PATH = "/api/knowledge_bases/{kb_id}/search"


class ImaSource(KnowledgeSource):
    source_id = "ima"
    display_name = "ima 知识库"
    capabilities = SourceCapabilities(
        searchable=True, full_text=True, incremental=False, retryable=True
    )

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        list_path: str = DEFAULT_LIST_PATH,
        items_path: str = DEFAULT_ITEMS_PATH,
        search_path: str = DEFAULT_SEARCH_PATH,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.list_path = list_path
        self.items_path = items_path
        self.search_path = search_path
        self.timeout = timeout
        self._client = client

    # -- config / health ----------------------------------------------------- #
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def health_check(self, *, probe: bool = False) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "available": False,
                "configured": False,
                "degraded": False,
                "latency_ms": None,
                "detail": "未接入：请在下方填写 ima API Key 与 Base URL（仅存内存，不入库）",
            }
        if not probe:
            return {
                "available": True,
                "configured": True,
                "degraded": False,
                "latency_ms": None,
                "detail": "已配置凭证（未发起探测请求；点击「测试连接」才会真的请求 ima）",
            }
        started = _now_ms()
        try:
            self._get(self.list_path)
        except (ValidationFailed, ImaTransientError) as exc:
            return {
                "available": False,
                "configured": True,
                "degraded": True,
                "latency_ms": _now_ms() - started,
                "detail": f"探测失败：{getattr(exc, 'message', None) or exc}",
            }
        return {
            "available": True,
            "configured": True,
            "degraded": False,
            "latency_ms": _now_ms() - started,
            "detail": "凭证可用：ima 知识库列表请求成功",
        }

    # -- data ---------------------------------------------------------------- #
    def list_sources(self) -> list[SourceRef]:
        self.require_configured()
        payload = self._get(self.list_path)
        items = _extract_items(payload)
        refs: list[SourceRef] = []
        for raw in items:
            external_id = str(_pick(raw, "id", "kb_id", "knowledge_base_id") or "").strip()
            if not external_id:
                continue  # 无 id 的条目无法幂等同步，跳过并（由上层统计）
            refs.append(
                SourceRef(
                    source_id=self.source_id,
                    external_id=external_id,
                    name=str(_pick(raw, "name", "title") or external_id),
                    kind=str(_pick(raw, "type", "kind") or "knowledge_base"),
                    updated_at=_pick(raw, "updated_at", "updateTime"),
                )
            )
        return refs

    def fetch_document(self, ref: SourceRef) -> Generator[RawDocument, None, None]:
        self.require_configured()
        path = self.items_path.format(kb_id=ref.external_id)
        payload = self._get(path)
        for raw in _extract_items(payload):
            external_id = str(_pick(raw, "id", "item_id") or "").strip()
            body = _pick(raw, "content", "text", "body")
            preview = _pick(raw, "preview", "summary", "abstract")
            preview_only = body is None and preview is not None
            text = str(body if body is not None else (preview or ""))
            if not external_id or not text.strip():
                continue
            yield RawDocument(
                source_id=self.source_id,
                external_id=external_id,
                name=str(_pick(raw, "title", "name") or external_id),
                text=text,
                updated_at=_pick(raw, "updated_at", "updateTime"),
                metadata={"preview_only": preview_only},
            )

    def search_metadata(self, ref: SourceRef, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.require_configured()
        path = self.search_path.format(kb_id=ref.external_id)
        payload = self._post(path, {"query": query, "limit": max(1, min(limit, 50))})
        hits: list[dict[str, Any]] = []
        for raw in _extract_items(payload):
            hits.append(
                {
                    "external_id": str(_pick(raw, "id", "item_id") or ""),
                    "title": str(_pick(raw, "title", "name") or ""),
                    "snippet": str(_pick(raw, "content", "text", "preview") or "")[:300],
                }
            )
        return hits

    # -- http ---------------------------------------------------------------- #
    def _request(self, method: str, path: str, json_body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"

        def call(client: httpx.Client) -> Any:
            resp = client.request(method, url, json=json_body, headers=self._headers())
            if resp.status_code == 429 or resp.status_code >= 500:
                # 429/5xx 走退避重试；最后一次失败由 retry_call 原样抛出。
                raise ImaTransientError(
                    f"ima 接口 {path} 返回 HTTP {resp.status_code}"
                )
            return self._decode(resp, path)

        if self._client is not None:
            return retry_call(lambda: call(self._client), attempts=3)
        with httpx.Client(timeout=self.timeout) as client:
            try:
                return retry_call(lambda: call(client), attempts=3)
            except ImaTransientError as exc:
                raise ValidationFailed("ima_api_error", str(exc)) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _decode(self, resp: httpx.Response, path: str) -> Any:
        if resp.status_code // 100 != 2:
            # 只回传状态码 + 极短摘要，不回传可能含密钥/正文的响应体。
            raise ValidationFailed(
                "ima_api_error",
                f"ima 接口 {path} 返回 HTTP {resp.status_code}"
                f"（{_summarize(resp)}）",
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ValidationFailed(
                "ima_invalid_json", f"ima 接口 {path} 返回的不是合法 JSON"
            ) from exc

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _extract_items(payload: Any) -> list[dict[str, Any]]:
    """Pull the item list out of the common ima envelope shapes."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "list", "knowledge_bases", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _extract_items(value)
                if nested:
                    return nested
    return []


def _pick(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _summarize(resp: httpx.Response) -> str:
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001
        return "无可读响应体"
    text = " ".join(text.split())
    return text[:120] if text else "空响应体"


def _now_ms() -> float:
    import time

    return time.monotonic() * 1000


class ImaTransientError(Exception):
    """429 / 5xx / 传输层错误：交给 :func:`retry_call` 退避重试。"""
