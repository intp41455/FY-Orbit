"""hub ↔ knowledge 桥接（验收 F2）。

问题
----
W6 把统一适配层落在 hub（``services/hub/``），W3 的知识源适配器落在
``services/knowledge/sources/``。两侧此前各自为政：中台里配了一条
``kind=knowledge_source`` 的连接，知识库链路**看不见也调不到**它。

本模块只做桥接，**不改 W3 的既有协议**（``KnowledgeSource`` ABC、
``SourceRef`` / ``RawDocument``、``build_source`` / ``list_source_status``
的签名与返回结构全部保持原样），也不改 hub 的适配器协议。

桥接的两件事
------------
1. **发现**：把 hub 里 ``kind=knowledge_source`` 的连接并进知识库的源列表，
   卡片上标注 ``source="hub:<connection_id>"``。连接不存在 / 未配置时如实显示
   「未接入（可在中台页配置）」，**绝不谎报可用**。
2. **调用**：``source_id`` 形如 ``hub:<connection_id>`` 时，凭证一律经 **hub 的
   加密存储**取得，优先级：

   a. 该 hub 连接自身的 Fernet 密文（``HubService._decrypt_secrets``）——中台页
      在这条连接上手填的凭证；
   b. 增补 E 已迁的 hub 加密凭证库 ``secret_store``（scope = 知识源 id）——
      知识库适配器卡片里配置过的凭证，中台连接直接复用，无需二次录入。

   **任何分支都不读 W3 原来的进程内存 store**（``InMemorySecretStore`` 现仅是
   hub store 加密不可用时的回退引擎，不再是独立来源）。

诚实约定
--------
* hub 里没有对应连接 → 抛 ``NotFound('unknown_hub_source')``，由 API 层如实
  报「未知知识源」，不静默返回空列表冒充「已接入但没内容」。
* 连接存在但一个凭证字段都没解出来 → 卡片 ``available=False`` +
  detail 说明原因；调用时由 ``require_configured()`` 抛
  ``ValidationFailed('<source_id>_not_configured')``。
* 探活结果直接取 hub 的 ``last_health_*``，**从不在这里重新推断**。
"""

from __future__ import annotations

from typing import Any

from ..errors import NotFound, PermissionDenied, ValidationFailed
from .sources import (
    BaiduPanSource,
    ImaSource,
    SecretStoreProtocol,
    secret_store,
)

#: 知识库 source_id 里识别「这条源来自 hub」的前缀。
HUB_SOURCE_PREFIX = "hub:"

#: hub 的 knowledge_source 连接 config 里，用哪个键指明底层 W3 源。
_SOURCE_ID_KEY = "source_id"

_BUILTIN_SOURCE_IDS = ("ima", "baidu_pan")


def is_hub_source(source_id: str) -> bool:
    """``hub:<conn_id>`` → True；W3 原生 id（ima / baidu_pan）→ False。"""
    return isinstance(source_id, str) and source_id.startswith(HUB_SOURCE_PREFIX)


def hub_connection_id(source_id: str) -> str:
    """``hub:c-123`` → ``c-123``。不是 hub 源则抛错，避免误用。"""
    if not is_hub_source(source_id):
        raise ValidationFailed("not_a_hub_source", f"'{source_id}' 不是 hub 知识源")
    conn_id = source_id[len(HUB_SOURCE_PREFIX):].strip()
    if not conn_id:
        raise ValidationFailed("hub_source_missing_id", "hub 知识源缺少连接 id")
    return conn_id


def hub_source_id(connection_id: str) -> str:
    """``c-123`` → ``hub:c-123``。"""
    return f"{HUB_SOURCE_PREFIX}{connection_id}"


# --------------------------------------------------------------------------- #
# 凭证解析
# --------------------------------------------------------------------------- #

def _credentials_from_connection(hub, conn) -> dict[str, str]:
    """连接自带的 Fernet 密文 → 明文（只在本函数内解密，绝不外传）。"""
    return hub._decrypt_secrets(conn)


def _credentials_from_shared_store(store: SecretStoreProtocol, source_id: str) -> dict[str, str]:
    """增补 E 已迁的 hub 加密凭证库（scope = 知识源 id）。"""
    try:
        return dict(store.get(source_id) or {})
    except Exception:  # noqa: BLE001 — 取不到就当没有，不让桥接崩掉整个列表
        return {}


def resolve_hub_credentials(
    session, owner_id: str, conn, *, store: SecretStoreProtocol | None = None,
) -> tuple[str, dict[str, str], str]:
    """``(source_id, credentials, origin)``；``origin`` 说明凭证来自哪条路径。

    ``origin`` 取值：``connection``（连接自带）/ ``shared_store``（复用知识库
    已配置的凭证）/ ``none``（两边都没有）。UI 与测试都靠它断言「确实走了
    hub 凭证路径」，而不是碰巧拿到了空值。
    """
    from ..hub.connections import HubService
    cfg = dict(conn.endpoint_config or {})
    builtin = str(cfg.get(_SOURCE_ID_KEY) or "").strip()
    if not builtin:
        raise ValidationFailed(
            "hub_knowledge_source_unmapped",
            f"hub 连接 '{conn.name}' 未声明 {_SOURCE_ID_KEY}，"
            f"无法映射到知识源；内置可用：{', '.join(_BUILTIN_SOURCE_IDS)}",
        )
    if builtin not in _BUILTIN_SOURCE_IDS:
        raise ValidationFailed(
            "hub_unknown_knowledge_source",
            f"未知知识源 '{builtin}'；内置可用：{', '.join(_BUILTIN_SOURCE_IDS)}",
        )

    hub = HubService(session)
    # 端点参数（base_url / redirect_uri ...）在 hub 里是明文 config 的一部分，
    # 不在 secret_config 里。适配器需要它们才能构造，因此先铺底再用密文覆盖。
    # 密文优先：连接上手填的凭证永远压过 config 里的同名字段。
    merged: dict[str, str] = {
        k: str(v) for k, v in cfg.items() if k != _SOURCE_ID_KEY and v not in (None, "")
    }
    conn_secrets = _credentials_from_connection(hub, conn)
    if any(conn_secrets.values()):
        merged.update({k: v for k, v in conn_secrets.items() if v})
        return builtin, merged, "connection"

    shared = _credentials_from_shared_store(store or secret_store, builtin)
    if any(shared.values()):
        merged.update({k: v for k, v in shared.items() if v})
        return builtin, merged, "shared_store"

    return builtin, merged, "none"


# --------------------------------------------------------------------------- #
# 调用
# --------------------------------------------------------------------------- #

def _get_owned_connection(session, owner_id: str, conn_id: str):
    from ...db.workbench_models import HubConnection

    conn = session.get(HubConnection, conn_id)
    if conn is None:
        raise NotFound(
            "unknown_hub_source",
            f"中台没有这条知识源连接（id={conn_id}）；可能已被删除，请到中台页确认",
        )
    if conn.owner_id != owner_id:
        # 与 HubService._get 同一口径：不泄露对方任何字段。
        raise PermissionDenied("hub_connection_forbidden", "无权访问该连接", 403)
    if conn.kind != "knowledge_source":
        raise ValidationFailed(
            "hub_not_knowledge_source",
            f"连接 '{conn.name}' 的类型是 {conn.kind}，不是 knowledge_source",
        )
    return conn


def build_hub_source(
    session, owner_id: str, source_id: str, *, store: SecretStoreProtocol | None = None,
):
    """``hub:<conn_id>`` → 实现了 W3 ``KnowledgeSource`` 协议的适配器实例。

    凭证来自 hub 加密存储（连接自带 或 共享凭证库），不读进程内存旧 store。
    """
    conn = _get_owned_connection(session, owner_id, hub_connection_id(source_id))
    builtin, creds, _origin = resolve_hub_credentials(
        session, owner_id, conn, store=store
    )
    if builtin == "ima":
        return ImaSource(
            api_key=creds.get("api_key", ""),
            base_url=creds.get("base_url", ""),
        )
    return BaiduPanSource(
        app_key=creds.get("app_key", ""),
        app_secret=creds.get("app_secret", ""),
        redirect_uri=creds.get("redirect_uri", ""),
    )


def credential_origin(
    session, owner_id: str, source_id: str, *, store: SecretStoreProtocol | None = None,
) -> str:
    """只回 ``origin``，供测试与诊断断言凭证路径（不解密给调用方）。"""
    conn = _get_owned_connection(session, owner_id, hub_connection_id(source_id))
    return resolve_hub_credentials(session, owner_id, conn, store=store)[2]


# --------------------------------------------------------------------------- #
# 发现：并入知识库源列表
# --------------------------------------------------------------------------- #

def _hub_state_text(state: str) -> str:
    return {
        "active": "已启用",
        "error": "异常",
        "needs_credentials": "缺凭证",
        "disabled": "已停用",
    }.get(state, state)


def hub_source_card(
    session, owner_id: str, conn, *, store: SecretStoreProtocol | None = None,
) -> dict[str, Any]:
    """把一条 hub 连接渲染成 W3 源卡片的形状（字段与原生卡片完全一致）。

    复用 ``build_source`` 的 ``status()`` 逻辑不做——那会再解一次凭证。这里
    直接借 ``ImaSource``/``BaiduPanSource`` 的 ``status()``，但实例用的是 hub
    解析出来的凭证，因此 available 反映的是 hub 侧凭证的真实可用性。
    """
    try:
        builtin, creds, origin = resolve_hub_credentials(
            session, owner_id, conn, store=store
        )
    except ValidationFailed as exc:
        # 映射不上（缺 source_id / 未知源）：卡片仍展示，但明确不可用 + 原因。
        return {
            "source_id": hub_source_id(conn.id),
            "source": hub_source_id(conn.id),
            "display_name": conn.name,
            "available": False,
            "configured": False,
            "degraded": True,
            "latency_ms": conn.last_health_latency_ms,
            "detail": f"中台连接未映射到内置知识源：{exc.message}",
            "hint": "到「超级中台」页编辑该连接，config 里补 source_id（ima / baidu_pan）",
            "credential_fields": [],
            "credentials_present": {},
            "storage": "hub",
            "persist_restart": True,
            "capabilities": {"searchable": False, "full_text": True,
                             "incremental": False, "retryable": True},
            "origin": "unmapped",
            "connection_id": conn.id,
            "connection_state": conn.state,
            "connection_state_text": _hub_state_text(conn.state),
            "health": {
                "ok": conn.last_health_ok,
                "checked_at": conn.last_health_at.isoformat() if conn.last_health_at else None,
                "latency_ms": conn.last_health_latency_ms,
                "detail": conn.last_health_detail,
            },
        }

    if builtin == "ima":
        adapter = ImaSource(api_key=creds.get("api_key", ""),
                            base_url=creds.get("base_url", ""))
        fields = ["api_key", "base_url"]
        hint = "凭证来自中台连接（加密存储）；也可在知识库适配器卡片里配置后复用"
    else:
        adapter = BaiduPanSource(
            app_key=creds.get("app_key", ""),
            app_secret=creds.get("app_secret", ""),
            redirect_uri=creds.get("redirect_uri", ""),
        )
        fields = ["app_key", "app_secret", "redirect_uri"]
        hint = "百度网盘适配器 v1 仅骨架，未接入"

    status = adapter.status(probe=False)
    has_any = any(creds.values())
    detail = str(status.get("detail") or "")
    if not has_any:
        # 一个凭证都没有 → 明确说「未接入」，并给出在哪配。绝不谎报可用。
        detail = "未接入（可在中台页配置）"
    elif conn.last_health_ok is False and conn.last_health_detail:
        # hub 侧探活失败过：把真实原因带上（不覆盖适配器自身的文案为空时）。
        detail = f"{detail}；中台最近探活失败：{conn.last_health_detail}".strip("；")

    status.update(
        {
            "source_id": hub_source_id(conn.id),
            "source": hub_source_id(conn.id),
            "display_name": conn.name,
            "detail": detail,
            "hint": hint,
            "credential_fields": fields,
            "credentials_present": {f: bool(creds.get(f)) for f in fields},
            "storage": "hub",
            "persist_restart": True,
            "origin": origin,
            "builtin_source_id": builtin,
            "connection_id": conn.id,
            "connection_state": conn.state,
            "connection_state_text": _hub_state_text(conn.state),
            "health": {
                "ok": conn.last_health_ok,
                "checked_at": conn.last_health_at.isoformat() if conn.last_health_at else None,
                "latency_ms": conn.last_health_latency_ms,
                "detail": conn.last_health_detail,
            },
        }
    )
    return status


def list_hub_source_cards(
    session, owner_id: str, *, store: SecretStoreProtocol | None = None,
) -> list[dict[str, Any]]:
    """owner 名下所有 ``kind=knowledge_source`` 的 hub 连接 → 源卡片。

    单条连接解析失败**不影响整批**（它自己降级成「未接入」卡片）。
    """
    from sqlalchemy import select

    from ...db.workbench_models import HubConnection

    if not owner_id:
        return []
    rows = session.execute(
        select(HubConnection)
        .where(HubConnection.owner_id == owner_id,
               HubConnection.kind == "knowledge_source")
        .order_by(HubConnection.created_at)
    ).scalars().all()
    out: list[dict[str, Any]] = []
    for conn in rows:
        try:
            out.append(hub_source_card(session, owner_id, conn, store=store))
        except Exception as exc:  # noqa: BLE001 — 单条坏数据不能拖垮整个列表
            out.append({
                "source_id": hub_source_id(conn.id),
                "source": hub_source_id(conn.id),
                "display_name": getattr(conn, "name", "未知连接"),
                "available": False,
                "configured": False,
                "degraded": True,
                "latency_ms": None,
                "detail": f"中台连接解析失败：{type(exc).__name__}",
                "hint": "到「超级中台」页检查该连接的配置",
                "credential_fields": [],
                "credentials_present": {},
                "storage": "hub",
                "persist_restart": True,
                "origin": "error",
                "connection_id": conn.id,
                "connection_state": getattr(conn, "state", ""),
                "connection_state_text": _hub_state_text(getattr(conn, "state", "")),
            })
    return out


__all__ = [
    "HUB_SOURCE_PREFIX",
    "build_hub_source",
    "credential_origin",
    "hub_connection_id",
    "hub_source_card",
    "hub_source_id",
    "is_hub_source",
    "list_hub_source_cards",
    "resolve_hub_credentials",
]
