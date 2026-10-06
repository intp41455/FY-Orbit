"""包6 · A-云盘RAG-01 · 万能云盘连接器契约（「配置即接入任意云盘」）。

设计要点（对齐 ``sources/base.py`` 既有协议，不推倒）：

* :class:`DriveRoute` —— 一朵云盘 = 一组 HTTP 端点模板（授权页 / 换 token /
  账号信息 / 目录列举 / 文件下载 / 服务端检索）。**路径与参数全部是配置**，
  接一朵新云盘 = 写一个 :class:`DriveProfile` 数据 + 一个 ``KnowledgeSource``
  子类壳，不再需要重写请求循环。
* :class:`DriveProfile` —— 除端点外还声明：凭证字段清单、可下载的文本扩展名、
  列举响应字段名映射（各家云盘字段名不同：``server_mtime`` / ``modified_at`` …）、
  递归深度与单次同步文件数上限（免费额度护栏，见 docs/knowledge 数据主权文档）。
* :class:`UniversalCloudDriveConnector` —— 通用引擎：健康检查（真实探活）、
  目录列举（:meth:`list_sources`）、文件抓取（:meth:`fetch_document`，按扩展名
  解码或走 ``ingest.parse_bytes``）、mtime 游标增量（:meth:`changes_since`）、
  OAuth 授权码换 token / 刷新 token。凭证**只从调用方传入**（SECRETS 门禁：
  ``services/knowledge/sources/__init__.py`` 的 ``secret_store``，即 hub Fernet
  加密存储），连接器自己不落盘、不回显。
* 诚实性铁律继承自 base：拿不到就抛 :class:`ValidationFailed` /
  :class:`UnsupportedCapability`，绝不返回构造条目；429/5xx 走
  :func:`retry_call` 退避重试，4xx 凭证错误绝不重试。

真实连通性边界：本模块与百度网盘实现的所有单测均用 ``httpx.MockTransport``
打桩（不打真网）；**真实云盘连通性待用户配置凭证后验收**（交付报告登记）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generator, Mapping
from urllib.parse import quote, urlencode

import httpx

from ...errors import ValidationFailed
from .base import (
    KnowledgeSource,
    RawDocument,
    SourceCapabilities,
    SourceRef,
    UnsupportedCapability,
    retry_call,
)

__all__ = [
    "DriveRoute",
    "DriveProfile",
    "UniversalCloudDriveConnector",
    "DriveTransientError",
]


class DriveTransientError(Exception):
    """429 / 5xx / 传输层错误：交给 :func:`retry_call` 退避重试。"""


# --------------------------------------------------------------------------- #
# 配置：一朵云盘的全部协议差异
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DriveRoute:
    """端点模板。``{xxx}`` 占位符在运行期用凭证/调用参数填充。"""

    authorize_url: str = ""
    #: POST（form）：{grant_type},{code|refresh_token},{client_id},{client_secret},{redirect_uri}
    token_url: str = ""
    #: GET：健康检查 / 账号信息，{access_token}
    userinfo_url: str = ""
    userinfo_params: dict[str, str] = field(default_factory=dict)
    #: GET：目录列举，{access_token},{dir},{limit}
    list_url: str = ""
    list_params: dict[str, str] = field(default_factory=dict)
    #: GET：文件下载，{access_token},{path}
    download_url: str = ""
    download_params: dict[str, str] = field(default_factory=dict)
    #: GET：服务端检索（可选；没有就如实报不支持），{access_token},{query},{limit}
    search_url: str = ""
    search_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DriveProfile:
    """一朵云盘的协议档案：端点 + 字段映射 + 抓取边界。"""

    drive_type: str
    display_name: str
    route: DriveRoute
    #: OAuth 应用三件套（缺一即「未配置」）
    required_fields: tuple[str, ...] = ("app_key", "app_secret", "redirect_uri")
    #: 授权后才会有的字段（增量/下载能力的前置）
    token_fields: tuple[str, ...] = ("access_token", "refresh_token")
    #: 直接按文本解码的扩展名
    text_extensions: tuple[str, ...] = (".md", ".markdown", ".txt")
    #: 下载字节后走 ``ingest.parse_bytes`` 解析的扩展名
    parse_extensions: tuple[str, ...] = (".pdf", ".docx")
    #: 列举响应里条目数组的键 + 各家字段名映射
    list_items_key: str = "list"
    list_name_key: str = "server_filename"
    list_path_key: str = "path"
    list_isdir_key: str = "isdir"
    list_size_key: str = "size"
    list_mtime_key: str = "server_mtime"
    list_fsid_key: str = "fs_id"
    #: 递归深度与单次文件数上限（免费额度/超时护栏，可被调用方收紧）
    max_depth: int = 3
    max_files_per_sync: int = 200
    #: 授权 scope（authorize_url 未含 scope 时用）
    oauth_scope: str = "basic,netdisk"


def _now_ms() -> float:
    return time.perf_counter() * 1000


# --------------------------------------------------------------------------- #
# 通用引擎
# --------------------------------------------------------------------------- #


class UniversalCloudDriveConnector(KnowledgeSource):
    """读一朵「可列举、可下载」的云盘。凭证只从构造参数进入（SECRETS 门禁）。"""

    #: 默认能力声明：无服务端检索、支持全文、默认不声明增量（有 token 的实例
    #: 会在 __init__ 里如实升级为 incremental=True）、429/5xx 可重试。
    capabilities = SourceCapabilities(
        searchable=False, full_text=True, incremental=False, retryable=True
    )

    def __init__(
        self,
        profile: DriveProfile,
        *,
        credentials: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        timeout: float = 15.0,
        attempts: int = 3,
        max_files_per_sync: int | None = None,
        token_resolver: Callable[[str], str] | None = None,
    ):
        self.profile = profile
        self.source_id = profile.drive_type
        self.display_name = profile.display_name
        self._creds: dict[str, str] = {
            k: (v or "").strip() for k, v in dict(credentials or {}).items()
        }
        self._client = client
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.max_files = int(max_files_per_sync or profile.max_files_per_sync)
        # token 兜底解析器（如百度盘子类注入「secret store → env」两段式）。
        self._token_resolver = token_resolver
        # 实例能力如实升级：能拿到 access_token 才声明增量（mtime 游标可用）。
        has_token = bool(self._cred("access_token") or self._cred("refresh_token"))
        if has_token and not self.capabilities.incremental:
            self.capabilities = replace(self.capabilities, incremental=True)
        #: 最近一次 fetch 的诚实清单：跳过了哪些文件、为什么（绝不静默）。
        self.last_sync: dict[str, Any] = {"skipped": [], "fetched": 0}

    # -- 凭证 ---------------------------------------------------------------- #
    def _cred(self, key: str) -> str:
        value = self._creds.get(key, "")
        if value:
            return value
        if self._token_resolver is not None:
            try:
                return (self._token_resolver(key) or "").strip()
            except Exception:  # noqa: BLE001 — 兜底解析失败按无凭证处理
                return ""
        return ""

    def is_configured(self) -> bool:
        return all(self._cred(f) for f in self.profile.required_fields)

    def has_token(self) -> bool:
        return bool(self._cred("access_token") or self._cred("refresh_token"))

    def _require_token(self) -> str:
        token = self._cred("access_token")
        if not token:
            raise ValidationFailed(
                f"{self.source_id}_not_authorized",
                f"{self.display_name} 已配置应用凭证但尚未完成 OAuth 授权"
                "（缺 access_token）；请先走授权流程获取并写入凭证存储",
            )
        return token

    def require_configured(self) -> None:
        if not self.is_configured():
            raise ValidationFailed(
                f"{self.source_id}_not_configured",
                f"{self.display_name} 未接入：缺少 {','.join(self.profile.required_fields)}，"
                "请在知识库适配器卡片填写（凭证走 SECRETS 门禁加密存储）",
            )

    # -- OAuth --------------------------------------------------------------- #
    def authorize_url(self, *, state: str = "") -> str:
        """构造 OAuth 授权页跳转 URL（真实协议模板，非占位）。"""
        route = self.profile.route
        if not route.authorize_url:
            raise UnsupportedCapability(
                "oauth_not_supported",
                f"{self.display_name} 的连接器档案未配置 authorize_url",
            )
        self.require_configured()
        params = {
            "response_type": "code",
            "client_id": self._cred("app_key") or self._cred("client_id"),
            "redirect_uri": self._cred("redirect_uri"),
            "scope": self.profile.oauth_scope,
            "display": "page",
        }
        if state:
            params["state"] = state
        sep = "&" if "?" in route.authorize_url else "?"
        return f"{route.authorize_url}{sep}{urlencode(params, quote_via=quote)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        """授权码换 token（POST form）。返回的 dict 由**调用方**写入 SECRETS 门禁。"""
        return self._token_request({"grant_type": "authorization_code", "code": code})

    def refresh_tokens(self) -> dict[str, Any]:
        refresh = self._cred("refresh_token")
        if not refresh:
            raise ValidationFailed(
                f"{self.source_id}_no_refresh_token",
                f"{self.display_name} 没有 refresh_token，无法刷新 access_token",
            )
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh})

    def _token_request(self, extra: dict[str, str]) -> dict[str, Any]:
        route = self.profile.route
        if not route.token_url:
            raise UnsupportedCapability(
                "oauth_not_supported",
                f"{self.display_name} 的连接器档案未配置 token_url",
            )
        form = {
            **extra,
            "client_id": self._cred("app_key") or self._cred("client_id"),
            "client_secret": self._cred("app_secret") or self._cred("client_secret"),
            "redirect_uri": self._cred("redirect_uri"),
        }
        payload = self._request("POST", route.token_url, form=form)
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise ValidationFailed(
                f"{self.source_id}_oauth_failed",
                f"{self.display_name} 换取 token 失败：响应缺少 access_token"
                f"（{_brief(payload)}）",
            )
        return payload

    # -- health -------------------------------------------------------------- #
    def health_check(self, *, probe: bool = False) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "available": False,
                "configured": False,
                "degraded": False,
                "latency_ms": None,
                "detail": (
                    f"未接入：请在知识库适配器卡片填写 "
                    f"{','.join(self.profile.required_fields)}（凭证走 SECRETS 门禁加密存储，"
                    "不入库、不回显）"
                ),
            }
        if not self.has_token():
            return {
                "available": False,
                "configured": True,
                "degraded": False,
                "latency_ms": None,
                "detail": (
                    "已配置应用凭证，但尚未完成 OAuth 授权（缺 access_token）。"
                    "请走授权流程后重试；在此之前本连接器不会返回任何数据"
                ),
            }
        if not probe:
            return {
                "available": True,
                "configured": True,
                "degraded": False,
                "latency_ms": None,
                "detail": "凭证可用（未发起探测请求；点击「测试连接」才会真的请求云盘）",
            }
        started = _now_ms()
        try:
            self._userinfo()
        except (ValidationFailed, DriveTransientError) as exc:
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
            "detail": f"凭证可用：{self.display_name} 账号信息请求成功",
        }

    def _userinfo(self) -> Any:
        route = self.profile.route
        if not route.userinfo_url:
            raise UnsupportedCapability(
                "userinfo_not_supported",
                f"{self.display_name} 的连接器档案未配置 userinfo_url",
            )
        return self._request(
            "GET", route.userinfo_url,
            params=self._fill(route.userinfo_params, {"access_token": self._require_token()}),
        )

    # -- 目录 / 文件 ---------------------------------------------------------- #
    def list_sources(self) -> list[SourceRef]:
        """根目录下的一级子目录 = 可同步集合（kind=folder）。"""
        self.require_configured()
        root_files = self.list_files("/", recursive=False)
        dirs = [
            e for e in root_files
            if e["is_dir"] and e["path"] not in ("/", "")
        ]
        return [
            SourceRef(
                source_id=self.source_id,
                external_id=e["path"],
                name=e["name"] or e["path"],
                kind="folder",
                updated_at=None,
            )
            for e in dirs
        ]

    def list_files(self, dir_path: str = "/", *, recursive: bool = True) -> list[dict[str, Any]]:
        """列举目录（默认递归到 ``profile.max_depth``）。返回规范化条目 dict。

        按 ``path`` 去重：云盘递归列表可能同条目多视角出现，去重保证
        ``list_sources`` / ``fetch_document`` 的清单幂等。
        """
        self.require_configured()
        token = self._require_token()
        route = self.profile.route
        if not route.list_url:
            raise UnsupportedCapability(
                "list_not_supported",
                f"{self.display_name} 的连接器档案未配置 list_url",
            )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def walk(path: str, depth: int) -> None:
            if len(out) >= self.max_files:
                return
            params = self._fill(
                route.list_params,
                {"access_token": token, "dir": path, "limit": str(self.max_files)},
            )
            payload = self._request("GET", route.list_url, params=params)
            for raw in self._items_of(payload):
                entry = self._normalize_entry(raw)
                if entry is None or entry["path"] in seen:
                    continue
                seen.add(entry["path"])
                out.append(entry)
                if recursive and entry["is_dir"] and depth < self.profile.max_depth:
                    walk(entry["path"], depth + 1)

        walk(dir_path or "/", 1)
        return out

    def changes_since(self, dir_path: str, cursor_mtime: int) -> list[dict[str, Any]]:
        """mtime 游标增量：返回 ``server_mtime > cursor_mtime`` 的文件条目。

        先过 :func:`ensure_capability` 同款实例能力检查——连接器没有 token 时
        不声明增量，调用方按能力路由时会显式失败，绝不静默降级成全量。
        """
        if not self.capabilities.incremental:
            raise UnsupportedCapability(
                "capability_not_supported",
                f"知识源 {self.source_id} 不支持能力 'incremental'（无 access_token）",
            )
        files = [
            e for e in self.list_files(dir_path, recursive=True)
            if not e["is_dir"]
        ]
        return [e for e in files if int(e.get("mtime") or 0) > int(cursor_mtime)]

    def fetch_document(self, ref: SourceRef) -> Generator[RawDocument, None, None]:
        """抓取一个目录（或单个文件）下的**可索引文本**，产出 RawDocument。

        不支持的扩展名不产出也不假装：记入 ``last_sync["skipped"]``（带原因），
        同步层据此向用户如实汇报。注意：本方法**故意不是生成器函数**——
        生成器在被迭代前不执行函数体，调用方会拿到"空迭代器"误以为已接入；
        这里先即时校验（未配置当场抛），再返回真迭代器（v1 骨架同款教训）。
        """
        self.require_configured()
        self.last_sync = {"skipped": [], "fetched": 0}
        return self._iter_documents(ref)

    def _iter_documents(self, ref: SourceRef) -> Generator[RawDocument, None, None]:
        if ref.kind == "file":
            entries = [{
                "name": ref.name or ref.external_id,
                "path": ref.external_id,
                "is_dir": False,
                "fs_id": ref.external_id,
                "size": 0,
                "mtime": 0,
            }]
        else:
            entries = [
                e for e in self.list_files(ref.external_id, recursive=True)
                if not e["is_dir"]
            ]
        for entry in entries[: self.max_files]:
            name = entry["name"]
            ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
            if ext not in self.profile.text_extensions + self.profile.parse_extensions:
                self.last_sync["skipped"].append({"name": name, "reason": "unsupported_extension"})
                continue
            try:
                data = self._download(entry["path"])
            except ValidationFailed as exc:
                self.last_sync["skipped"].append({"name": name, "reason": str(exc.message)})
                continue
            text = self._text_from(name, data)
            if text is None:
                self.last_sync["skipped"].append({"name": name, "reason": "no_extractable_text"})
                continue
            self.last_sync["fetched"] += 1
            yield RawDocument(
                source_id=self.source_id,
                external_id=entry.get("fs_id") or entry["path"],
                name=name,
                text=text,
                updated_at=str(entry.get("mtime") or "") or None,
                metadata={
                    "drive_path": entry["path"],
                    "drive_mtime": entry.get("mtime"),
                    "drive_size": entry.get("size"),
                    "preview_only": False,
                },
            )

    def search_metadata(self, ref: SourceRef, query: str, limit: int = 10) -> list[dict[str, Any]]:
        route = self.profile.route
        if not route.search_url:
            raise UnsupportedCapability(
                "source_not_searchable",
                f"知识源 {self.source_id} 不支持服务端检索，只能拉取到本地索引后检索",
            )
        self.require_configured()
        params = self._fill(
            route.search_params,
            {
                "access_token": self._require_token(),
                "query": query,
                "limit": str(max(1, min(limit, 50))),
            },
        )
        payload = self._request("GET", route.search_url, params=params)
        return [x for x in self._items_of(payload) if isinstance(x, dict)]

    # -- http ---------------------------------------------------------------- #
    def _fill(self, templates: Mapping[str, str], ctx: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, tpl in templates.items():
            try:
                out[key] = tpl.format(**ctx)
            except KeyError as exc:  # 配置错误：当场炸，而不是发出半截请求
                raise ValidationFailed(
                    "drive_route_misconfigured",
                    f"{self.display_name} 路由参数 {key}!={tpl} 缺少占位符值 {exc}",
                ) from exc
        return out

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
    ) -> Any:
        target = f"{url}?{urlencode(params)}" if params else url

        def call(client: httpx.Client) -> Any:
            resp = client.request(method, target, data=form, timeout=self.timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise DriveTransientError(
                    f"{self.display_name} 接口返回 HTTP {resp.status_code}"
                )
            return self._decode(resp, target)

        if self._client is not None:
            return retry_call(lambda: call(self._client), attempts=self.attempts)
        with httpx.Client(timeout=self.timeout) as client:
            try:
                return retry_call(lambda: call(client), attempts=self.attempts)
            except DriveTransientError as exc:
                raise ValidationFailed(
                    f"{self.source_id}_api_error", str(exc)
                ) from exc

    def _decode(self, resp: httpx.Response, target: str) -> Any:
        if resp.status_code // 100 != 2:
            raise ValidationFailed(
                f"{self.source_id}_api_error",
                f"{self.display_name} 接口返回 HTTP {resp.status_code}"
                f"（{_brief(resp.text)}）",
            )
        if not resp.content:
            return {}
        try:
            payload = resp.json()
        except ValueError:
            # 下载端点成功时直接回文件字节流，不是 JSON——原样返回字节。
            return resp.content
        if isinstance(payload, dict):
            errno = payload.get("errno")
            if errno not in (None, 0):
                raise ValidationFailed(
                    f"{self.source_id}_api_errno",
                    f"{self.display_name} 接口返回 errno={errno}"
                    f"（{_ERRNO_HINTS.get(errno, '详见云盘开放平台错误码表')}）",
                )
            if payload.get("error"):
                raise ValidationFailed(
                    f"{self.source_id}_oauth_failed",
                    f"{self.display_name} OAuth 接口返回 error={payload.get('error')}"
                    f"（{payload.get('error_description', '')}）",
                )
        return payload

    def _download(self, path: str) -> bytes:
        route = self.profile.route
        if not route.download_url:
            raise UnsupportedCapability(
                "download_not_supported",
                f"{self.display_name} 的连接器档案未配置 download_url",
            )
        result = self._request(
            "GET", route.download_url,
            params=self._fill(
                route.download_params, {"access_token": self._require_token(), "path": path}
            ),
        )
        if isinstance(result, bytes):
            return result
        # dict 到这里说明云盘用 JSON 报了业务错误（_decode 已放行 errno=0 以外情形兜底）
        raise ValidationFailed(
            f"{self.source_id}_download_failed",
            f"{self.display_name} 下载 {path} 未返回文件内容",
        )

    # -- 响应形状 ------------------------------------------------------------- #
    def _items_of(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in (self.profile.list_items_key, "data", "items", "list", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _normalize_entry(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        p = self.profile
        path = str(_pick(raw, p.list_path_key) or "").strip()
        if not path:
            return None
        name = str(_pick(raw, p.list_name_key) or path.rstrip("/").rsplit("/", 1)[-1])
        is_dir = bool(int(_pick(raw, p.list_isdir_key) or 0))
        return {
            "path": path,
            "name": name,
            "is_dir": is_dir,
            "size": int(_pick(raw, p.list_size_key) or 0),
            "mtime": int(_pick(raw, p.list_mtime_key) or 0),
            "fs_id": str(_pick(raw, p.list_fsid_key) or path),
        }

    def _text_from(self, name: str, data: bytes) -> str | None:
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext in self.profile.text_extensions:
            for encoding in ("utf-8", "utf-8-sig", "gb18030"):
                try:
                    return data.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return None
        if ext in self.profile.parse_extensions:
            from ..ingest import parse_bytes  # 本地导入避免环

            try:
                return parse_bytes(name, data).text
            except Exception:  # noqa: BLE001 — 解析失败如实记 skipped，不抛断整批
                return None
        return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _pick(raw: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _brief(text: Any, cap: int = 120) -> str:
    s = " ".join(str(text or "").split())
    return s[:cap] if s else "空响应体"


#: 常见 xpan errno 的人类可读提示（完整表以云盘开放平台文档为准）。
_ERRNO_HINTS: dict[int, str] = {
    2: "参数错误",
    -6: "身份验证失败（access_token 无效或已过期）",
    111: "access_token 已过期，请用 refresh_token 刷新",
    12: "批量操作数超限",
    31064: "文件不存在",
}
