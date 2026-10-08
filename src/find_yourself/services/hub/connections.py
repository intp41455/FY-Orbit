"""中台连接管理（W6 §1.2）：CRUD + 探活 + 真实调用 + 越权隔离。

凭证边界（任务书 §3 / FROZEN_CONTRACT §1）
-----------------------------------------
* 明文凭证只在**创建/更新请求的生命周期内**存在于内存，随即被 Fernet 加密进
  ``secret_config``；此后任何对外结构只出现掩码。
* :func:`public_connection` 是唯一的对外序列化入口，它**不可能**吐出明文：
  私密字段只从 ``secret_config[field]["mask"]`` 取值。
* 越权（别人的连接）一律 403 ``permission_denied``（任务书 §1.2 明文要求），
  错误体不含对方任何字段。

诚实原则：探活失败就是 ``ok=False`` + 真实原因；能力拿不到就是空列表；
manifest 缺必填凭证就停在 ``needs_credentials``，不允许被路由命中。
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from typing import Any

from sqlalchemy import select as sa_select

from ...db.types import utcnow
from ...db.workbench_models import HubConnection
from ..actor import Actor
from ..errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from . import CONNECTION_STATES, HUB_KINDS, KIND_GROUP
from .adapters import (
    Capability,
    HealthReport,
    InvokeCall,
    InvokeResult,
    build_adapter,
    guard_endpoint,
)
from .crypto import decrypt_secret, encrypt_secret, mask_secret
from .manifest import import_manifest
from .presets import get_preset

#: kind -> 该 kind 需要加密保存的凭证字段（表单/manifest 之外的兜底白名单）。
DEFAULT_SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "openai_chat": ("api_key",),
    "anthropic": ("api_key",),
    # 远程 MCP（http/sse/ws）需要认证头；stdio 不需要。故「允许但不强制」——
    # 是否必填由 credential_fields 的 required 决定。
    # 实际注入路径：config.headers 里写 {credential.api_token}，由
    # McpServerAdapter._render_headers() 渲染后透传给 McpClient.from_url。
    "mcp_server": ("api_token", "authorization", "headers"),
    "http_webhook": ("api_token",),
    "knowledge_source": ("api_key", "app_key", "app_secret"),
    "tool_plugin": ("api_token",),
}

_SLUG_RE = re.compile(r"[^a-z0-9_.-]+")

# 进程内缓存 MCP 客户端（连接 id -> client）。MCP 是长连接协议，每次调用都重新
# 起子进程既慢又会丢会话；进程重启后缓存自然失效，首次调用会重新装配。
_MCP_CLIENTS: dict[str, Any] = {}
_MCP_LOCK = threading.RLock()


def new_id(prefix: str = "hub") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def slugify(name: str, fallback: str = "adapter") -> str:
    """Build a tool-registry-safe slug: ``^[a-z][a-z0-9_.-]{1,63}$``."""
    raw = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    raw = re.sub(r"-+", "-", raw)
    if not raw or not raw[0].isalpha():
        raw = f"x-{raw}" if raw else fallback
    return raw[:40]


def public_connection(conn: HubConnection) -> dict[str, Any]:
    """The only outward serialisation. Plaintext secrets are unreachable here."""
    secrets = conn.secret_config if isinstance(conn.secret_config, dict) else {}
    config = dict(conn.endpoint_config or {})
    for field, blob in secrets.items():
        if isinstance(blob, dict):
            config[field] = blob.get("mask") or "****"
        else:  # 历史/异常数据：宁可全遮，也不冒回显明文的风险
            config[field] = "****"
    return {
        "id": conn.id,
        "name": conn.name,
        "kind": conn.kind,
        "group": conn.group or KIND_GROUP.get(conn.kind, "tool"),
        "preset_id": conn.preset_id,
        "icon": conn.icon,
        "description": conn.description,
        "state": conn.state,
        "config": config,
        "secret_fields": list(conn.secret_fields or []),
        "capabilities": [c for c in (conn.capabilities or []) if isinstance(c, dict)],
        "has_manifest": conn.manifest is not None,
        "params": conn.params,
        "preference": conn.preference,
        "health": {
            "ok": conn.last_health_ok,
            "checked_at": conn.last_health_at.isoformat() if conn.last_health_at else None,
            "latency_ms": conn.last_health_latency_ms,
            "detail": conn.last_health_detail,
        },
        "version": conn.version,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
        "updated_at": conn.updated_at.isoformat() if conn.updated_at else None,
    }


class HubService:
    """Connection CRUD + health + invoke, strictly owner-scoped."""

    def __init__(self, session) -> None:
        self.s = session

    # -- ownership ---------------------------------------------------------- #
    def _get(self, actor: Actor, conn_id: str) -> HubConnection:
        row = self.s.get(HubConnection, conn_id)
        if row is None:
            raise NotFound("hub_connection_not_found", "连接不存在")
        if row.owner_id != actor.owner_id:
            # 任务书 §1.2：越权 403 强制。错误体不泄露对方任何字段。
            raise PermissionDenied("hub_connection_forbidden", "无权访问该连接", 403)
        return row

    def _query(self, actor: Actor, kind: str | None = None, group: str | None = None):
        stmt = sa_select(HubConnection).where(HubConnection.owner_id == actor.owner_id)
        if kind:
            stmt = stmt.where(HubConnection.kind == kind)
        if group:
            stmt = stmt.where(HubConnection.group == group)
        return stmt.order_by(HubConnection.created_at)

    # -- secrets ------------------------------------------------------------ #
    @staticmethod
    def _encrypt_secrets(fields: list[str], credentials: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in fields:
            raw = credentials.get(field)
            if raw in (None, ""):
                continue
            plain = str(raw)
            try:
                out[field] = {"enc": encrypt_secret(plain), "mask": mask_secret(plain)}
            except Exception as exc:  # noqa: BLE001 — 加密不可用必须报错，不能明文落库
                raise ValidationFailed(
                    "hub_credential_encryption_failed",
                    f"凭证 '{field}' 无法加密存储：{exc.__class__.__name__}；"
                    "已拒绝保存（绝不落明文）",
                ) from None
        return out

    @staticmethod
    def _decrypt_secrets(conn: HubConnection) -> dict[str, str]:
        secrets = conn.secret_config if isinstance(conn.secret_config, dict) else {}
        out: dict[str, str] = {}
        for field, blob in secrets.items():
            if isinstance(blob, dict) and blob.get("enc"):
                try:
                    out[field] = decrypt_secret(str(blob["enc"]))
                except Exception:  # noqa: BLE001 — 解不开就当没有，调用会显式报未配置
                    continue
        return out

    # -- adapter plumbing ---------------------------------------------------- #
    def adapter_config(self, conn: HubConnection) -> dict[str, Any]:
        cfg = dict(conn.endpoint_config or {})
        cfg.update(self._decrypt_secrets(conn))
        cfg["kind"] = conn.kind
        cfg["capabilities"] = list(conn.capabilities or [])
        return cfg

    def build_adapter(self, conn: HubConnection, *, transport: Any = None, sleep: Any = None):
        mcp_client = None
        if conn.kind == "mcp_server":
            with _MCP_LOCK:
                mcp_client = _MCP_CLIENTS.get(conn.id)
        return build_adapter(
            conn.kind,
            self.adapter_config(conn),
            transport=transport,
            mcp_client=mcp_client,
            sleep=sleep,
        )

    def _remember_mcp(self, conn: HubConnection, adapter: Any) -> None:
        if conn.kind == "mcp_server" and getattr(adapter, "_client", None) is not None:
            with _MCP_LOCK:
                _MCP_CLIENTS[conn.id] = adapter._client

    @staticmethod
    def _forget_mcp(conn_id: str) -> None:
        with _MCP_LOCK:
            client = _MCP_CLIENTS.pop(conn_id, None)
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    # -- CRUD ---------------------------------------------------------------- #
    def list_connections(
        self, actor: Actor, *, kind: str | None = None, group: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.s.execute(self._query(actor, kind, group)).scalars().all()
        return [public_connection(r) for r in rows]

    def get_connection(self, actor: Actor, conn_id: str) -> dict[str, Any]:
        return public_connection(self._get(actor, conn_id))

    def create_connection(self, actor: Actor, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        kind = str(payload.get("kind") or "").strip()
        if not name:
            raise ValidationFailed("hub_name_required", "连接名称不能为空")
        if kind not in HUB_KINDS:
            raise ValidationFailed(
                "hub_unknown_kind", f"未知适配器类型 '{kind}'；支持：{', '.join(HUB_KINDS)}"
            )
        exists = self.s.execute(
            sa_select(HubConnection).where(
                HubConnection.owner_id == actor.owner_id, HubConnection.name == name
            )
        ).scalars().first()
        if exists is not None:
            raise Conflict("hub_name_taken", f"已存在同名连接 '{name}'")

        preset_id = str(payload.get("preset_id") or "")
        preset = get_preset(preset_id) if preset_id else None
        if preset_id and preset is None:
            raise NotFound("hub_preset_not_found", f"未知预置 '{preset_id}'")
        if preset is not None and preset["kind"] != kind:
            raise ValidationFailed(
                "hub_preset_kind_mismatch",
                f"预置 '{preset_id}' 的类型是 {preset['kind']}，与提交的 {kind} 不一致",
            )

        config = dict(payload.get("config") or {})
        if preset is not None:
            merged = dict(preset["config"])
            merged.update({k: v for k, v in config.items() if v not in (None, "")})
            config = merged
        credentials = dict(payload.get("credentials") or {})
        fields = list(payload.get("secret_fields") or DEFAULT_SECRET_FIELDS.get(kind, ()))
        unknown = [f for f in credentials if f not in fields]
        if unknown:
            raise ValidationFailed(
                "hub_unknown_secret_field",
                f"凭证字段 {unknown} 不在该类型允许的字段内：{fields}",
            )
        self._validate_endpoint(kind, config)

        secret_config = self._encrypt_secrets(fields, credentials)
        state = "active"
        if preset is not None and not preset.get("implemented", True):
            state = "error"
        required_missing = self._missing_required(payload.get("credential_fields"), credentials, config)
        if required_missing:
            state = "needs_credentials"

        conn = HubConnection(
            id=new_id(),
            owner_id=actor.owner_id,
            name=name,
            kind=kind,
            group=KIND_GROUP.get(kind, "tool"),
            preset_id=preset_id,
            icon=str(payload.get("icon") or (preset["icon"] if preset else "🔌")),
            description=str(payload.get("description") or (preset["description"] if preset else "")),
            endpoint_config=config,
            secret_config=secret_config,
            secret_fields=fields,
            capabilities=[Capability.from_public(c).to_public()
                          for c in (payload.get("capabilities") or [])],
            manifest=payload.get("manifest"),
            params=payload.get("params"),
            state=state,
            preference=int(payload.get("preference") or 0),
        )
        self.s.add(conn)
        self.s.flush()
        if kind == "tool_plugin":
            self._register_tool_plugin(conn)
        return public_connection(conn)

    def update_connection(self, actor: Actor, conn_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._get(actor, conn_id)
        if "name" in payload and payload["name"]:
            new_name = str(payload["name"]).strip()
            if new_name != conn.name:
                clash = self.s.execute(
                    sa_select(HubConnection).where(
                        HubConnection.owner_id == actor.owner_id,
                        HubConnection.name == new_name,
                    )
                ).scalars().first()
                if clash is not None:
                    raise Conflict("hub_name_taken", f"已存在同名连接 '{new_name}'")
            conn.name = new_name
        if "config" in payload and isinstance(payload["config"], dict):
            conn.endpoint_config = {**(conn.endpoint_config or {}), **payload["config"]}
        self._validate_endpoint(conn.kind, dict(conn.endpoint_config or {}))
        if "credentials" in payload and isinstance(payload["credentials"], dict):
            fields = list(conn.secret_fields or ())
            unknown = [f for f in payload["credentials"] if f not in fields]
            if unknown:
                raise ValidationFailed(
                    "hub_unknown_secret_field",
                    f"凭证字段 {unknown} 不在该连接允许的字段内：{fields}",
                )
            conn.secret_config = {
                **(conn.secret_config or {}),
                **self._encrypt_secrets(fields, payload["credentials"]),
            }
        if "capabilities" in payload and payload["capabilities"] is not None:
            conn.capabilities = [Capability.from_public(c).to_public()
                                 for c in payload["capabilities"]]
        if "preference" in payload and payload["preference"] is not None:
            conn.preference = max(0, min(10, int(payload["preference"])))
        if "state" in payload and payload["state"]:
            state = str(payload["state"])
            if state not in CONNECTION_STATES:
                raise ValidationFailed("hub_invalid_state", f"非法状态 '{state}'")
            conn.state = state
        if "icon" in payload and payload["icon"]:
            conn.icon = str(payload["icon"])
        if "description" in payload and payload["description"] is not None:
            conn.description = str(payload["description"])
        conn.updated_at = utcnow()
        conn.version += 1
        self.s.flush()
        if conn.kind == "tool_plugin":
            self._register_tool_plugin(conn)
        return public_connection(conn)

    def delete_connection(self, actor: Actor, conn_id: str) -> dict[str, Any]:
        conn = self._get(actor, conn_id)
        self._forget_mcp(conn.id)
        if conn.kind == "tool_plugin":
            self._unregister_tool_plugin(conn)
        self.s.delete(conn)
        self.s.flush()
        return {"id": conn_id, "deleted": True}

    # -- validation ---------------------------------------------------------- #
    @staticmethod
    def _missing_required(credential_fields: Any, credentials: dict[str, Any],
                          config: dict[str, Any]) -> list[str]:
        if not isinstance(credential_fields, list):
            return []
        missing = []
        for field in credential_fields:
            if not isinstance(field, dict) or not field.get("required"):
                continue
            key = str(field.get("key") or "")
            if not credentials.get(key) and not config.get(key):
                missing.append(key)
        return missing

    @staticmethod
    def _validate_endpoint(kind: str, config: dict[str, Any]) -> None:
        """Generic HTTP targets must pass the SSRF guard at registration time too.

        Failing fast here is better than discovering it mid-invoke: the user sees
        the reason while still filling the form.
        """
        if kind in {"http_webhook", "tool_plugin"} and config.get("url"):
            guard_endpoint(str(config["url"]))

    # -- tool_registry bridge ------------------------------------------------ #
    def _register_tool_plugin(self, conn: HubConnection) -> str | None:
        """Expose a tool_plugin connection as a real tool entry (``hub.<slug>``).

        Only ``tool_registry.register`` (public API) is used, so its whitelist
        and schema checks still apply. Returns the registered name, or None when
        the connection has no URL yet (nothing to call = nothing to register).
        """
        from ..tool_registry import tool_registry

        url = str((conn.endpoint_config or {}).get("url") or "")
        if not url:
            return None
        name = f"hub.{slugify(conn.name, conn.id[:8])}"
        params = conn.params if isinstance(conn.params, dict) else {"type": "object"}
        try:
            tool_registry.register(
                name=name,
                description=conn.description or f"中台自定义工具：{conn.name}",
                parameters=params,
                entry={"type": "http", "url": url},
            )
        except Exception:  # noqa: BLE001 — 注册失败不该让连接创建失败
            return None
        return name

    @staticmethod
    def _unregister_tool_plugin(conn: HubConnection) -> None:
        """Best-effort cleanup. The registry has no delete API, so we drop the
        persisted metadata directly and note the limitation honestly."""
        from ..tool_registry import tool_registry

        name = f"hub.{slugify(conn.name, conn.id[:8])}"
        try:
            if hasattr(tool_registry, "_tools"):
                tool_registry._tools.pop(name, None)  # noqa: SLF001 — 无公开删除 API
                tool_registry._save()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass

    # -- health -------------------------------------------------------------- #
    def health_check(self, actor: Actor, conn_id: str, *, timeout_seconds: float = 3.0,
                     transport: Any = None, sleep: Any = None) -> dict[str, Any]:
        conn = self._get(actor, conn_id)
        adapter = self.build_adapter(conn, transport=transport, sleep=sleep)
        report = adapter.health(timeout_seconds=timeout_seconds)
        self._apply_health(conn, adapter, report)
        self.s.flush()
        return {
            "connection": public_connection(conn),
            "report": report.to_public(),
        }

    def _apply_health(self, conn: HubConnection, adapter: Any, report: HealthReport) -> None:
        conn.last_health_at = utcnow()
        conn.last_health_ok = bool(report.ok)
        conn.last_health_detail = report.detail
        conn.last_health_latency_ms = report.latency_ms
        conn.updated_at = utcnow()
        if conn.state != "needs_credentials":
            conn.state = "active" if report.ok else "error"
        if report.ok:
            discovered: list[dict[str, Any]] = []
            if conn.kind == "mcp_server":
                self._remember_mcp(conn, adapter)
                try:
                    registered = adapter.register_tools()
                except Exception:  # noqa: BLE001
                    registered = []
                discovered = [c.to_public() for c in adapter.capabilities()]
                conn.capabilities = discovered
                conn.last_health_detail = (
                    report.detail + f"（已注册 {len(registered)} 个工具到注册中心）"
                )
            elif report.capabilities:
                conn.capabilities = [c.to_public() for c in report.capabilities]

    async def health_check_all(self, actor: Actor, *, timeout_seconds: float = 3.0,
                               limit: int = 50) -> list[dict[str, Any]]:
        """Concurrent probes (asyncio.gather), 3s budget each.

        One unhealthy connection never drags the list down: a probe that raises
        or times out becomes an ``ok=False`` report with the real reason.
        """
        conns = self.s.execute(self._query(actor).limit(limit)).scalars().all()
        adapters = {c.id: self.build_adapter(c) for c in conns}

        async def probe(conn: HubConnection) -> tuple[str, HealthReport]:
            try:
                return conn.id, await asyncio.wait_for(
                    asyncio.to_thread(adapters[conn.id].health, timeout_seconds=timeout_seconds),
                    timeout=timeout_seconds + 2.0,
                )
            except asyncio.TimeoutError:
                return conn.id, HealthReport(ok=False, detail=f"探测超时（>{timeout_seconds}s）")
            except Exception as exc:  # noqa: BLE001
                return conn.id, HealthReport(ok=False, detail=f"{type(exc).__name__}: {exc}")

        gathered = await asyncio.gather(*(probe(c) for c in conns))
        by_id: dict[str, HealthReport] = dict(gathered)
        out: list[dict[str, Any]] = []
        for conn in conns:
            report = by_id.get(conn.id) or HealthReport(ok=False, detail="未获得探测结果")
            self._apply_health(conn, adapters.get(conn.id), report)
            out.append({"connection": public_connection(conn), "report": report.to_public()})
        self.s.flush()
        return out

    # -- invoke -------------------------------------------------------------- #
    def invoke(self, actor: Actor, conn_id: str, *, action: str,
               params: dict[str, Any] | None = None, timeout_seconds: float = 15.0,
               transport: Any = None, sleep: Any = None) -> dict[str, Any]:
        conn = self._get(actor, conn_id)
        if conn.state == "disabled":
            raise ValidationFailed("hub_connection_disabled", "连接已停用，拒绝调用")
        if conn.state == "needs_credentials":
            raise ValidationFailed(
                "hub_connection_needs_credentials", "连接缺少必填凭证，请先补全再调用"
            )
        adapter = self.build_adapter(conn, transport=transport, sleep=sleep)
        result = adapter.invoke(
            InvokeCall(action=action, params=dict(params or {}), timeout_seconds=timeout_seconds)
        )
        if conn.kind == "mcp_server":
            self._remember_mcp(conn, adapter)
        return {"connection_id": conn.id, "action": action, "result": result.to_public()}

    # -- manifest ------------------------------------------------------------ #
    def import_manifest_connection(self, actor: Actor, *, text: str, filename: str = "",
                                   credentials: dict[str, Any] | None = None,
                                   name: str | None = None) -> dict[str, Any]:
        """Register a connection from a declarative manifest (zero code)."""
        normalized, warnings = import_manifest(text, filename)
        payload = {
            "name": name or normalized["name"] or normalized["id"],
            "kind": normalized["kind"],
            "icon": normalized["icon"],
            "description": normalized["description"],
            "config": normalized["config"],
            "capabilities": normalized["capabilities"],
            "credentials": credentials or {},
            "credential_fields": normalized["credential_fields"],
            "secret_fields": normalized["secret_fields"],
            "manifest": normalized,
            "params": normalized["params"],
        }
        created = self.create_connection(actor, payload)
        created["warnings"] = warnings
        created["manifest_id"] = normalized["id"]
        return created

    # -- aggregate ----------------------------------------------------------- #
    def capabilities(self, actor: Actor, *, kind: str | None = None) -> list[dict[str, Any]]:
        """Flat capability inventory across the owner's connections."""
        out: list[dict[str, Any]] = []
        for conn in self.s.execute(self._query(actor, kind)).scalars().all():
            for cap in (conn.capabilities or []):
                if not isinstance(cap, dict) or not cap.get("name"):
                    continue
                out.append({
                    "connection_id": conn.id,
                    "connection_name": conn.name,
                    "kind": conn.kind,
                    "group": conn.group,
                    "icon": conn.icon,
                    "healthy": conn.last_health_ok,
                    "state": conn.state,
                    "capability": cap,
                })
        return out


__all__ = [
    "DEFAULT_SECRET_FIELDS",
    "HubService",
    "InvokeResult",
    "new_id",
    "public_connection",
    "slugify",
]
