"""包6 · A-云盘RAG-01 · 百度网盘适配器 **协议级实装**（v2，运行在通用连接器上）。

v1 骨架的历史行为（未配置态）**原样保留**——存量测试
``tests/unit/test_knowledge_sources.py`` 锁定的语义不回退：

* 未配置（缺 app_key/app_secret/redirect_uri 任一）时：
  ``health_check`` → ``available=False`` + detail 含「未接入」；
  ``list_sources`` / ``fetch_document`` / ``oauth_authorize_url`` /
  ``search_metadata`` 一律抛 ``ValidationFailed('baidu_pan_not_implemented')``。
* **配置后**（应用三件套齐全）走百度网盘开放平台协议（本文件实装）：

  - OAuth2.0 授权码流程：:meth:`oauth_authorize_url` 生成真实授权页 URL，
    :meth:`exchange_code` 换 token、:meth:`refresh_tokens` 刷新
    （端点 ``openapi.baidu.com/oauth/2.0/*``）；
  - 目录列举：``pan.baidu.com/rest/2.0/xpan/file?method=list``（``errno`` 语义）；
  - 文件下载：``…xpan/file?method=download``（文本扩展名直接解码，
    ``.pdf/.docx`` 字节流走 ``ingest.parse_bytes`` 解析）；
  - 增量同步：mtime（``server_mtime``）游标 :meth:`changes_since`
    （拿到 access_token 的实例才声明 ``incremental`` 能力）。
* 凭证入口走现有 SECRETS 门禁（``sources/__init__.secret_store``，hub Fernet
  加密存储）：token 只经 :meth:`persist_credentials` 写入凭证存储，连接器自身
  不落盘、不回显；也支持环境变量 ``FY_BAIDU_PAN_ACCESS_TOKEN`` /
  ``FY_BAIDU_PAN_REFRESH_TOKEN`` 开机预置。

诚实边界（交付报告同步登记）：所有单测用 ``httpx.MockTransport`` 打桩，
**真实云盘连通性待用户配置凭证后验收**；百度开放平台无服务端全文检索 API，
``search_metadata`` 如实报「不支持」（同步入库后走本地 RAG 检索）。
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from ...errors import ValidationFailed
from .base import SourceRef
from .connector import DriveProfile, DriveRoute, UniversalCloudDriveConnector

NOT_IMPLEMENTED = "baidu_pan_not_implemented"

#: 百度网盘开放平台协议端点（OAuth2.0 授权码 + xpan 文件 API）。
BAIDU_ROUTE = DriveRoute(
    authorize_url="https://openapi.baidu.com/oauth/2.0/authorize",
    token_url="https://openapi.baidu.com/oauth/2.0/token",
    userinfo_url="https://openapi.baidu.com/rest/2.0/passport/users/getInfo",
    userinfo_params={"access_token": "{access_token}"},
    list_url="https://pan.baidu.com/rest/2.0/xpan/file",
    list_params={
        "method": "list",
        "access_token": "{access_token}",
        "dir": "{dir}",
        "limit": "{limit}",
        "web": "1",
        "order": "name",
    },
    download_url="https://pan.baidu.com/rest/2.0/xpan/file",
    download_params={
        "method": "download",
        "access_token": "{access_token}",
        "path": "{path}",
    },
    search_url="",  # 开放平台无服务端全文检索 API：如实不支持
)

BAIDU_PROFILE = DriveProfile(
    drive_type="baidu_pan",
    display_name="百度网盘",
    route=BAIDU_ROUTE,
    required_fields=("app_key", "app_secret", "redirect_uri"),
    token_fields=("access_token", "refresh_token"),
    text_extensions=(".md", ".markdown", ".txt"),
    parse_extensions=(".pdf", ".docx"),
    list_items_key="list",
    list_name_key="server_filename",
    list_path_key="path",
    list_isdir_key="isdir",
    list_size_key="size",
    list_mtime_key="server_mtime",
    list_fsid_key="fs_id",
    oauth_scope="basic,netdisk",
)


class BaiduPanSource(UniversalCloudDriveConnector):
    """百度网盘开放平台适配器（通用连接器 + 百度协议档案）。"""

    def __init__(
        self,
        *,
        app_key: str = "",
        app_secret: str = "",
        redirect_uri: str = "",
        access_token: str = "",
        refresh_token: str = "",
        client: Any | None = None,
        timeout: float = 15.0,
    ):
        super().__init__(
            BAIDU_PROFILE,
            credentials={
                "app_key": app_key,
                "app_secret": app_secret,
                "redirect_uri": redirect_uri,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
            client=client,
            timeout=timeout,
            token_resolver=self._resolve_token,
        )

    # -- 凭证兜底（SECRETS 门禁 → 环境变量，两段式，与 build_source 同构） ------ #
    def _resolve_token(self, key: str) -> str:
        """token 兜底解析：凭证存储 scope ``baidu_pan`` 优先，其次环境变量。

        令牌不作为构造参数传入 build_source（该装配点在 ``sources/__init__.py``，
        非本包独占），因此运行期在此惰性读取——语义等价且零装配点改动。
        """
        if key not in ("access_token", "refresh_token"):
            return ""
        from . import secret_store  # 本地导入：sources/__init__ 装载完成后才可用

        stored = secret_store.get("baidu_pan").get(key, "")
        if stored:
            return stored
        return os.environ.get(f"FY_BAIDU_PAN_{key.upper()}", "")

    # -- 兼容语义：未配置态的错误码保持 v1 骨架的行为 --------------------------- #
    def require_configured(self) -> None:
        if not self.is_configured():
            raise ValidationFailed(
                NOT_IMPLEMENTED,
                "百度网盘适配器未配置应用凭证（app_key/app_secret/redirect_uri）。"
                "v2 已按开放平台协议实装：配置后即可走 OAuth 授权 → 目录列举 → "
                "文件下载 → mtime 增量；凭证走 SECRETS 门禁加密存储",
            )

    def search_metadata(self, ref: SourceRef, query: str, limit: int = 10) -> list[dict[str, Any]]:
        # 百度开放平台不提供服务端全文检索：这不是「没实现」，是「平台没有」——
        # 但错误码沿用 NOT_IMPLEMENTED 以保住存量调用方的分支语义。
        raise ValidationFailed(
            NOT_IMPLEMENTED,
            "百度网盘开放平台无服务端全文检索 API：请先同步入库，再用本地 RAG（/api/kb/search）检索",
        )

    # -- OAuth（兼容旧方法名 + 落 SECRETS 门禁的持久化助手） -------------------- #
    def oauth_authorize_url(self, *, state: str = "") -> str:
        return self.authorize_url(state=state)

    def persist_credentials(self, tokens: Mapping[str, str], store: Any | None = None) -> dict[str, bool]:
        """把 token 写入 SECRETS 门禁（hub Fernet 加密存储）。明文不落日志、不回显。"""
        if store is None:
            from . import secret_store as store  # noqa: N813 — 本地导入
        fields = {k: str(tokens.get(k) or "") for k in ("access_token", "refresh_token")}
        if not any(fields.values()):
            raise ValidationFailed(
                "empty_credentials", "凭证为空：至少需要 access_token 或 refresh_token"
            )
        store.set("baidu_pan", fields)
        return {k: bool(v) for k, v in fields.items()}

    def complete_oauth(self, code: str, store: Any | None = None) -> dict[str, Any]:
        """授权码 → token → 写入凭证存储，一步完成。返回脱敏结果（不含明文 token）。"""
        tokens = self.exchange_code(code)
        present = self.persist_credentials(tokens, store=store)
        return {
            "authorized": True,
            "credentials_present": present,
            "expires_in": tokens.get("expires_in"),
            "scope": tokens.get("scope"),
        }
