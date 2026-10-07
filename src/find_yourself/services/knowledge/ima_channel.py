"""B1 · ima 公共知识库通道（派单书 B-IMA-01~05 · 2026-10-07）。

**通道优先级（B-IMA-01「MCP 优先」/ B-IMA-02「OpenAPI 兜底」）**：

1. **MCP**（主通道，2026-10-07 主控实测打通）：复用 :mod:`find_yourself.adapters.mcp`
   客户端，服务器定义取 ``Settings.mcp_servers["ima"]``（``FY_MCP_SERVERS`` JSON），
   支持 stdio 子进程（``command``）与远端（``url`` + ``transport=http|sse|ws``）两种
   挂法；三方法 ``get_knowledge_base_list`` / ``search_knowledge_base`` /
   ``search_knowledge``。
2. **REST OpenAPI**（兜底）：既有 :class:`~.sources.ima.ImaSource`（端点路径已经
   G1 对官方文档核对关闭），凭证缺 base_url 或请求失败时不假装成功。
3. **本地缓存**（B3 验收 3「断网时走本地缓存，不报错」）：每次真实检索成功后把
   **规范化命中全量**写进 ``Settings.ima_cache_path``（默认 ``.runtime/ima_cache``）；
   MCP/REST 都失败但缓存命中时返回缓存结果并如实打 ``cached=true`` +
   ``cache_time``，**绝不把缓存冒充实时结果**。缓存也没有时才报
   ``ima_unavailable``（诚实失败）。

**凭证（G2 / B-IMA-05 SECRETS 门禁）**：设置页写入 hub Fernet 加密存储
（scope ``ima``：``app_id`` / ``api_key`` / ``secret_key`` / ``base_url``），通道在
发起 MCP 连接时把凭证并入子进程 env（``IMA_APP_ID`` 等）或 HTTP 头（
``Authorization: Bearer <api_key>``）——明文不入库、不落盘、不回显。

**实测事实（2026-10-07，派单书「已实测打通的事实」表）**：

* 库名「八字紫微奇门印度占星塔罗排盘算命｜天地玄黄」，库 ID ``7509748362520236``，
  type 1004，陛下自有库（B-IMA-03 自建库全量访问，无 300 字预览限制）；
* ``search_knowledge`` 一次返回 100 命中 / 121K 字符全文；
* 命中字段：``media_id`` / ``title`` / ``introduction`` / ``tags`` /
  ``can_fetch_content`` / ``can_preview`` / ``folder_info``（``media_type`` 7=md 等）。

**出处回溯（B2 · G4）**：每条命中附 ``src`` = ``ima://{kb_id}/{media_id}``，
前端展示为可点击出处并把 ``content``（全文）就地展开——不构造不存在的跳转 URL；
远端命中若自带 ``url`` 字段则原样透传（``origin_url``）。

诚实边界：任何一次真实调用失败都以 :class:`ValidationFailed` 显式上抛（带阶段
说明），**绝不返回构造出来的假条目**；分页与过滤在本地对真实命中切片，MCP 单次
返回之外的页如实标 ``total``（不伪造更大的命中总数）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..errors import ValidationFailed
from .sources.base import SourceRef
from .sources.ima import ImaSource

__all__ = [
    "DEFAULT_KB_ID",
    "ImaChannelConfig",
    "ImaChannel",
    "get_ima_channel",
    "reset_ima_channel",
]

#: 实测库 ID（八字紫微奇门印度占星塔罗排盘算命｜天地玄黄 · type 1004 · 陛下自有库）。
DEFAULT_KB_ID = "7509748362520236"

#: MCP 工具名（2026-10-07 实测三方法；改名走 ImaChannelConfig）。
DEFAULT_LIST_TOOL = "get_knowledge_base_list"
DEFAULT_SEARCH_TOOL = "search_knowledge"

#: 单条命中正文回传上限（字符）。实测平均单命中 ~1.2K，正常不会触顶；
#: 触顶时打 ``content_truncated=True``，绝不静默截断。
CONTENT_CAP = 20000

#: search_knowledge 的参数形状候选（开放平台参数名未稳定，按序试探并记忆成功形）。
_SEARCH_ARG_SHAPES: tuple[tuple[str, str], ...] = (
    ("kb_id", "query"),
    ("knowledge_base_id", "query"),
    ("query",),
)


@dataclass(frozen=True)
class ImaChannelConfig:
    """通道配置：库 ID + 缓存目录 + MCP 服务器定义 + secret store 凭证。"""

    kb_id: str = DEFAULT_KB_ID
    cache_path: str = ".runtime/ima_cache"
    #: ``Settings.mcp_servers.get("ima")``：{"command": [...], "env": {...}} 或
    #: {"url": "...", "transport": "http", "headers": {...}}。None = MCP 未配置。
    mcp_server: dict[str, Any] | None = None
    #: secret store scope ``ima`` 的凭证（app_id/api_key/secret_key/base_url）。
    credentials: dict[str, str] = field(default_factory=dict)
    list_tool: str = DEFAULT_LIST_TOOL
    search_tool: str = DEFAULT_SEARCH_TOOL

    def fingerprint(self) -> str:
        """连接复用键：配置或凭证变动即换新客户端，不跨凭证复用连接。"""
        raw = json.dumps(
            {
                "kb_id": self.kb_id,
                "mcp_server": self.mcp_server,
                "creds_present": sorted(k for k, v in self.credentials.items() if v),
                "creds_digest": hashlib.sha256(
                    json.dumps(self.credentials, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ImaChannel:
    """ima 知识库检索通道：MCP → REST → 缓存，逐级诚实降级。"""

    def __init__(
        self,
        config: ImaChannelConfig,
        *,
        client_factory: Callable[[ImaChannelConfig], Any | None] | None = None,
        rest_factory: Callable[[ImaChannelConfig], ImaSource | None] | None = None,
    ):
        self.config = config
        self._client_factory = client_factory or _default_client_factory
        self._rest_factory = rest_factory or _default_rest_factory
        self._client: Any | None = None
        self._client_built = False
        self._search_shape: tuple[str, ...] | None = None
        self._lock = threading.Lock()

    # -- 配置 / 状态 ---------------------------------------------------------- #

    def is_configured(self) -> bool:
        """MCP 服务器已定义，或 REST 凭证（api_key + base_url）齐备。"""
        return self._mcp_ready() or self._rest_ready()

    def _mcp_ready(self) -> bool:
        server = self.config.mcp_server or {}
        has_cmd = isinstance(server.get("command"), list) and bool(server.get("command"))
        has_url = bool(str(server.get("url") or "").strip())
        return has_cmd or has_url

    def _rest_ready(self) -> bool:
        rest = self._rest_source()
        return rest is not None and rest.is_configured()

    def _rest_source(self) -> ImaSource | None:
        return self._rest_factory(self.config)

    def status(self) -> dict[str, Any]:
        """适配器卡 / 设置页用：如实报告每条通道是否配置、是否可用。"""
        creds = self.config.credentials
        present = {k: bool(creds.get(k)) for k in ("app_id", "api_key", "secret_key", "base_url")}
        status: dict[str, Any] = {
            "source_id": "ima",
            "kb_id": self.config.kb_id,
            "configured": self.is_configured(),
            "channels": {
                "mcp": {"configured": self._mcp_ready()},
                "rest": {"configured": self._rest_ready()},
            },
            "credentials_present": present,
            "cache_path": self.config.cache_path,
        }
        if not self._mcp_ready() and not self._rest_ready():
            status["detail"] = "未接入：请在设置页填写 ima 凭证，或在 FY_MCP_SERVERS 配置 ima MCP 服务器"
            return status
        if self._mcp_ready():
            # 真实探活：列库（顺带核对默认库是否可达）。
            started = time.monotonic() * 1000
            try:
                bases = self._mcp_list_bases()
                status["channels"]["mcp"]["available"] = True
                status["channels"]["mcp"]["latency_ms"] = round(time.monotonic() * 1000 - started)
                status["channels"]["mcp"]["bases"] = len(bases)
                matched = [b for b in bases if str(b.get("kb_id") or b.get("id") or "") == self.config.kb_id]
                status["kb_matched"] = bool(matched)
                status["detail"] = "ima MCP 通道可用" + (
                    f"（已核对默认库 {self.config.kb_id}）" if matched else ""
                )
            except Exception as exc:  # noqa: BLE001 — 探活失败如实上报
                status["channels"]["mcp"]["available"] = False
                status["channels"]["mcp"]["error"] = f"{type(exc).__name__}: {exc}"
                status["detail"] = f"ima MCP 探活失败：{exc}"
                if self._rest_ready():
                    status["detail"] += "；REST 兜底凭证已配置，检索时将自动兜底"
        else:
            status["detail"] = "MCP 未配置，将走 REST OpenAPI 兜底（api_key + base_url）"
        return status

    # -- 检索 ------------------------------------------------------------------ #

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        type_: str | None = None,
        tag: str | None = None,
        kb_id: str | None = None,
    ) -> dict[str, Any]:
        """真实检索：MCP 优先 → REST 兜底 → 本地缓存 → 诚实报错。

        分页与 ``type`` / ``tag`` 过滤在本地对**真实命中**切片；``total`` 是过滤后
        的真实命中数，绝不因为翻页而编造更大的总数。
        """
        query = (query or "").strip()
        if not query:
            raise ValidationFailed("ima_query_required", "检索词不能为空")
        kb = (kb_id or self.config.kb_id or DEFAULT_KB_ID).strip()

        hits: list[dict[str, Any]] = []
        channel = ""
        errors: list[str] = []
        if self._mcp_ready():
            try:
                hits = self._mcp_search(query, kb)
                channel = "mcp"
            except Exception as exc:  # noqa: BLE001 — 逐级降级，最后诚实汇总
                errors.append(f"mcp: {type(exc).__name__}: {exc}")
        if not hits and self._rest_ready():
            try:
                hits = self._rest_search(query, kb)
                channel = "rest"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"rest: {type(exc).__name__}: {exc}")

        cached_at: str | None = None
        if not hits:
            cached = self._load_cache(kb, query)
            # 只有「真实通道都失败」或「从未配置」时才允许缓存顶上；
            # 实时检索成功但零命中必须如实返回零命中，不能拿旧缓存冒充。
            degraded = bool(errors) or not self.is_configured()
            if cached is not None and degraded:
                hits, cached_at, channel = cached["hits"], cached["cached_at"], cached["channel"] + "+cache"
            elif errors:
                raise ValidationFailed(
                    "ima_unavailable",
                    "ima 检索失败且无本地缓存：" + "；".join(errors),
                )
            elif not self.is_configured():
                raise ValidationFailed(
                    "ima_not_configured",
                    "ima 未接入：请在设置页填写凭证或配置 FY_MCP_SERVERS['ima']",
                )
            # else：通道可用但本次检索零命中——如实返回空（不是错误）。
        else:
            self._write_cache(kb, query, hits, channel)

        filtered = _filter_hits(hits, type_=type_, tag=tag)
        total = len(filtered)
        pages = max(1, -(-total // page_size))
        page = min(page, pages)
        start = (page - 1) * page_size
        slice_ = filtered[start : start + page_size]

        return {
            "query": query,
            "kb_id": kb,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "results": slice_,
            "channel": channel,
            "cached": cached_at is not None,
            "cache_time": cached_at,
            "errors": errors,
        }

    # -- MCP 通道 --------------------------------------------------------------- #

    def _mcp_client(self) -> Any:
        with self._lock:
            if not self._client_built:
                self._client = self._client_factory(self.config)
                self._client_built = True
            return self._client

    def _ensure_initialized(self, client: Any) -> None:
        if not getattr(client, "_initialized", False):
            client.initialize()

    def _mcp_search(self, query: str, kb: str) -> list[dict[str, Any]]:
        client = self._mcp_client()
        if client is None:
            raise ValidationFailed("ima_mcp_unavailable", "ima MCP 客户端不可用")
        self._ensure_initialized(client)
        payload = self._call_search(client, query, kb)
        raw_hits = _extract_hits(payload)
        return [_normalize_mcp_hit(raw, kb) for raw in raw_hits]

    def _call_search(self, client: Any, query: str, kb: str) -> Any:
        """按候选参数形状调用 ``search_knowledge``；成功形记在客户端实例上。"""
        shapes: list[tuple[str, ...]] = []
        if self._search_shape:
            shapes.append(self._search_shape)
        shapes.extend(s for s in _SEARCH_ARG_SHAPES if s not in shapes)
        last_error: Exception | None = None
        for shape in shapes:
            args: dict[str, Any] = {"query": query}
            if "kb_id" in shape:
                args["kb_id"] = kb
            if "knowledge_base_id" in shape:
                args["knowledge_base_id"] = kb
            try:
                payload = client.call_tool(self.config.search_tool, args)
                self._search_shape = shape
                return payload
            except Exception as exc:  # noqa: BLE001 — 形状试探：失败换下一形状
                last_error = exc
                if not _is_shape_error(exc):
                    raise
        raise last_error if last_error else ValidationFailed(
            "ima_mcp_error", "ima MCP 检索未返回任何结果"
        )

    def _mcp_list_bases(self) -> list[dict[str, Any]]:
        client = self._mcp_client()
        if client is None:
            raise ValidationFailed("ima_mcp_unavailable", "ima MCP 客户端不可用")
        self._ensure_initialized(client)
        payload = client.call_tool(self.config.list_tool, {})
        return [x for x in _extract_hits(payload) if isinstance(x, dict)]

    # -- REST 兜底 --------------------------------------------------------------- #

    def _rest_search(self, query: str, kb: str) -> list[dict[str, Any]]:
        rest = self._rest_source()
        if rest is None or not rest.is_configured():
            raise ValidationFailed("ima_rest_unconfigured", "REST 兜底未配置 api_key/base_url")
        # OpenAPI 兜底无服务端分页：一次最多取 50 条，再由本地切片（如实标注）。
        raw_hits = rest.search_metadata(SourceRef("ima", kb, "自建库"), query, limit=50)
        return [_normalize_rest_hit(raw, kb) for raw in raw_hits]

    # -- 本地缓存（B3 · 断网兜底） ---------------------------------------------- #

    def _cache_file(self, kb: str, query: str) -> str:
        key = hashlib.sha256(f"{kb}|{query}".encode("utf-8")).hexdigest()[:32]
        return str(Path(self.config.cache_path) / f"{key}.json")

    def _write_cache(self, kb: str, query: str, hits: list[dict[str, Any]], channel: str) -> None:
        try:
            path = Path(self._cache_file(kb, query))
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "kb_id": kb,
                "query": query,
                "channel": channel,
                "cached_at": _utc_now(),
                "hits": hits,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # 缓存写失败不影响实时结果（本机磁盘不可写时如实降级为无缓存）。
            pass

    def _load_cache(self, kb: str, query: str) -> dict[str, Any] | None:
        try:
            path = Path(self._cache_file(kb, query))
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            hits = payload.get("hits")
            if not isinstance(hits, list):
                return None
            return {
                "hits": hits,
                "cached_at": payload.get("cached_at"),
                "channel": str(payload.get("channel") or "cache"),
            }
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# 命中规范化（B2 · 出处回溯）
# --------------------------------------------------------------------------- #

def _normalize_mcp_hit(raw: dict[str, Any], kb: str) -> dict[str, Any]:
    media_id = str(
        raw.get("media_id") or raw.get("id") or raw.get("item_id") or ""
    ).strip()
    title = str(raw.get("title") or raw.get("name") or "").strip()
    introduction = str(raw.get("introduction") or raw.get("summary") or "").strip()
    content = raw.get("content") or raw.get("text") or raw.get("body") or introduction
    content = str(content)
    truncated = False
    if len(content) > CONTENT_CAP:
        content = content[:CONTENT_CAP]
        truncated = True
    tags_raw = raw.get("tags")
    tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
    folder = ""
    folder_info = raw.get("folder_info")
    if isinstance(folder_info, dict):
        folder = str(folder_info.get("name") or folder_info.get("title") or "")
    elif folder_info:
        folder = str(folder_info)
    media_type = raw.get("media_type") or raw.get("type")
    return {
        "media_id": media_id,
        "title": title,
        "introduction": introduction,
        "content": content,
        "content_truncated": truncated,
        "tags": tags,
        "folder": folder,
        "type": str(media_type) if media_type is not None else "",
        "can_fetch_content": bool(raw.get("can_fetch_content")),
        "can_preview": bool(raw.get("can_preview")),
        "preview_only": not bool(raw.get("can_fetch_content")) and bool(raw.get("can_preview")),
        "origin_url": str(raw.get("url") or raw.get("link") or ""),
        # 出处回溯（G4）：media_id 级别的稳定指针，前端展示为 `src:`。
        "src": f"ima://{kb}/{media_id}" if media_id else f"ima://{kb}",
    }


def _normalize_rest_hit(raw: dict[str, Any], kb: str) -> dict[str, Any]:
    external_id = str(raw.get("external_id") or raw.get("media_id") or "").strip()
    snippet = str(raw.get("snippet") or "")
    return {
        "media_id": external_id,
        "title": str(raw.get("title") or ""),
        "introduction": snippet,
        "content": snippet,
        "content_truncated": False,
        "tags": [],
        "folder": "",
        "type": "",
        "can_fetch_content": bool(snippet),
        "can_preview": bool(snippet),
        "preview_only": False,
        "origin_url": "",
        "src": f"ima://{kb}/{external_id}" if external_id else f"ima://{kb}",
    }


def _extract_hits(payload: Any) -> list[dict[str, Any]]:
    """从 MCP 返回的常见包裹形状里取出命中数组（list / data / items / results）。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "list", "results", "hits", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _extract_hits(value)
                if nested:
                    return nested
        # MCP tools/call 包装：{"content": [{"type":"text","text": "..."}]}
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        return _extract_hits(json.loads(str(item.get("text") or "")))
                    except ValueError:
                        continue
    return []


def _filter_hits(
    hits: list[dict[str, Any]], *, type_: str | None, tag: str | None
) -> list[dict[str, Any]]:
    out = hits
    if type_:
        wanted = type_.strip().lower()
        out = [
            h for h in out
            if str(h.get("type") or "").lower() == wanted
            or str(h.get("folder") or "").lower() == wanted
        ]
    if tag:
        wanted_tag = tag.strip()
        out = [h for h in out if wanted_tag in (h.get("tags") or [])]
    return out


def _is_shape_error(exc: Exception) -> bool:
    """参数形状试探只对「参数不合法」类错误重试，其余错误原样上抛。

    ``-32602`` 是标准 invalid params；``-32005``（Tool failed）只有在消息带
    参数类关键词时才算形状问题——真实服务器与进程内桩服务器都会把
    ``KeyError("kb_id")`` 包成 ``Tool failed: KeyError``。
    """
    code = getattr(exc, "code", None)
    if code == -32602:
        return True
    if code == -32005:
        text = str(exc).lower()
        return any(
            k in text
            for k in ("param", "argument", "required", "missing", "unknown",
                      "invalid", "keyerror", "valueerror", "typeerror")
        )
    return False


def _default_client_factory(config: ImaChannelConfig) -> Any | None:
    """按 ``FY_MCP_SERVERS['ima']`` 造 MCP 客户端；凭证并入 env / 头（不落盘）。"""
    server = dict(config.mcp_server or {})
    creds = config.credentials
    env_extra = {
        k: v
        for k, v in (
            ("IMA_APP_ID", creds.get("app_id")),
            ("IMA_API_KEY", creds.get("api_key")),
            ("IMA_SECRET_KEY", creds.get("secret_key")),
            ("IMA_BASE_URL", creds.get("base_url")),
        )
        if v
    }
    command = server.get("command")
    if isinstance(command, list) and command:
        from ...adapters.mcp import McpClient  # 局部导入避免环

        env = {k: str(v) for k, v in (server.get("env") or {}).items()}
        env.update(env_extra)
        return McpClient.from_subprocess([str(c) for c in command], env=env)
    url = str(server.get("url") or "").strip()
    if url:
        from ...adapters.mcp import McpClient  # 局部导入避免环

        headers = {str(k): str(v) for k, v in (server.get("headers") or {}).items()}
        if creds.get("api_key"):
            headers.setdefault("Authorization", f"Bearer {creds['api_key']}")
        return McpClient.from_url(
            url, transport=str(server.get("transport") or "http"), headers=headers
        )
    return None


def _default_rest_factory(config: ImaChannelConfig) -> ImaSource:
    creds = config.credentials
    return ImaSource(api_key=creds.get("api_key") or "", base_url=creds.get("base_url") or "")


# --------------------------------------------------------------------------- #
# 进程级单例（路由层用）：配置指纹变化即重建，凭证不跨指纹复用。
# --------------------------------------------------------------------------- #

_CHANNEL: ImaChannel | None = None
_CHANNEL_FINGERPRINT: str | None = None
_CHANNEL_LOCK = threading.Lock()


def build_ima_channel_config() -> ImaChannelConfig:
    """从 Settings + secret store 读当前配置（G2 接线点）。"""
    from ...config import settings as load_settings
    from .sources import secret_store

    cfg = load_settings()
    mcp_server = None
    raw = getattr(cfg, "mcp_servers", None) or {}
    if isinstance(raw, dict) and isinstance(raw.get("ima"), dict):
        mcp_server = raw["ima"]
    return ImaChannelConfig(
        kb_id=str(getattr(cfg, "ima_kb_id", "") or DEFAULT_KB_ID).strip(),
        cache_path=str(getattr(cfg, "ima_cache_path", "") or ".runtime/ima_cache"),
        mcp_server=mcp_server,
        credentials=secret_store.get("ima"),
    )


def get_ima_channel() -> ImaChannel:
    global _CHANNEL, _CHANNEL_FINGERPRINT
    config = build_ima_channel_config()
    fingerprint = config.fingerprint()
    with _CHANNEL_LOCK:
        if _CHANNEL is None or _CHANNEL_FINGERPRINT != fingerprint:
            _CHANNEL = ImaChannel(config)
            _CHANNEL_FINGERPRINT = fingerprint
    return _CHANNEL


def reset_ima_channel() -> None:
    """测试钩子：强制下一条请求重建通道。"""
    global _CHANNEL, _CHANNEL_FINGERPRINT
    with _CHANNEL_LOCK:
        _CHANNEL = None
        _CHANNEL_FINGERPRINT = None


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
