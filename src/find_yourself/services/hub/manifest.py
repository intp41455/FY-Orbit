"""用户自定义适配器 = 声明式 manifest（W6 增补 B）。

一个 YAML/JSON 文件就是一个可分享的适配器。用户两条路：

1. HubPage 表单（同连接向导）；
2. **导入 manifest 文件**——拖进来即注册，社区分享的最小单位就是这一个文件。

完整字段说明见 ``docs/hub-adapter-manifest.md``（本模块校验的就是那份文档）。

校验是**白名单式**的：schema / kind / 端点逐项检查，不认识的 kind 直接报错，
缺必填凭证只标 ``needs_credentials`` 而不伪造「已就绪」。
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ValidationFailed
from . import HUB_KINDS, KIND_GROUP

MANIFEST_SCHEMA = "fy-hub-adapter/v1"
_REQUIRED_TOP = ("schema", "id", "name", "kind")
_ID_RE = __import__("re").compile(r"^[a-z][a-z0-9_-]{0,47}$")

_CREDENTIAL_KEY_RE = __import__("re").compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,39}$")


class ManifestError(ValidationFailed):
    """A manifest that does not validate. Never silently coerced."""

    http_status = 422
    default_code = "hub_manifest_invalid"


def parse_manifest_text(text: str, filename: str = "") -> dict[str, Any]:
    """Parse YAML or JSON manifest text.

    YAML needs PyYAML (already a project dependency). A ``.yaml``/``.yml`` file
    without PyYAML installed raises a plain, honest error — we never try to
    hand-roll a YAML parser.
    """
    raw = (text or "").strip()
    if not raw:
        raise ManifestError("hub_manifest_empty", "manifest 内容为空")
    lowered = (filename or "").lower()
    if lowered.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover — depends on env
            raise ManifestError(
                "hub_yaml_unavailable",
                "当前环境缺少 PyYAML，无法解析 .yaml manifest；请改用 JSON 格式",
            ) from exc
        data = yaml.safe_load(raw)
    else:
        try:
            data = json.loads(raw)
        except ValueError:
            # JSON 失败再试 YAML：YAML 是 JSON 的超集，多数手写 manifest 是 YAML。
            try:
                import yaml

                data = yaml.safe_load(raw)
            except Exception as exc:  # noqa: BLE001
                raise ManifestError(
                    "hub_manifest_unparsable", f"manifest 既不是合法 JSON 也不是合法 YAML：{exc}"
                ) from None
    if not isinstance(data, dict):
        raise ManifestError("hub_manifest_not_object", "manifest 顶层必须是对象（键值映射）")
    return data


def _validate_auth(auth: Any) -> list[dict[str, Any]]:
    if auth is None:
        return []
    if not isinstance(auth, dict):
        raise ManifestError("hub_manifest_auth", "auth 必须是对象")
    fields = auth.get("fields") or []
    if not isinstance(fields, list):
        raise ManifestError("hub_manifest_auth_fields", "auth.fields 必须是数组")
    out: list[dict[str, Any]] = []
    for item in fields:
        if not isinstance(item, dict) or not item.get("key"):
            raise ManifestError("hub_manifest_auth_field", "auth.fields 每项都要有 key")
        key = str(item["key"]).strip()
        if not _CREDENTIAL_KEY_RE.match(key):
            raise ManifestError(
                "hub_manifest_auth_key", f"凭证字段名 '{key}' 不合规（字母数字下划线，首字符非数字）"
            )
        out.append({
            "key": key,
            "label": str(item.get("label") or key),
            "secret": bool(item.get("secret", True)),
            "required": bool(item.get("required", False)),
        })
    return out


def validate_manifest(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a parsed manifest. Returns ``(normalized, warnings)``.

    Raises :class:`ManifestError` on hard errors; ``warnings`` carries honest
    notes that do not block registration (e.g. missing credential values).
    """
    warnings: list[str] = []
    if not isinstance(raw, dict):
        raise ManifestError("hub_manifest_not_object", "manifest 顶层必须是对象")
    for key in _REQUIRED_TOP:
        if not raw.get(key):
            raise ManifestError("hub_manifest_missing_field", f"manifest 缺少必填字段：{key}")
    schema = str(raw["schema"]).strip()
    if schema != MANIFEST_SCHEMA:
        raise ManifestError(
            "hub_manifest_schema",
            f"schema 必须是 '{MANIFEST_SCHEMA}'，收到 '{schema}'",
        )
    kind = str(raw["kind"]).strip()
    if kind not in HUB_KINDS:
        raise ManifestError("hub_manifest_kind", f"未知 kind '{kind}'；支持：{', '.join(HUB_KINDS)}")
    adapter_id = str(raw["id"]).strip()
    if not _ID_RE.match(adapter_id):
        raise ManifestError(
            "hub_manifest_id", f"id '{adapter_id}' 不合规（小写字母开头，仅字母数字_-）"
        )

    credential_fields = _validate_auth(raw.get("auth"))
    endpoint = raw.get("endpoint")
    if not isinstance(endpoint, dict):
        endpoint = {}

    config: dict[str, Any] = {}
    if kind in {"openai_chat", "anthropic"}:
        config = {
            "provider_id": str(endpoint.get("provider_id")
                               or ("anthropic" if kind == "anthropic" else "openai_compat")),
            "base_url": str(endpoint.get("base_url") or ""),
            "model": str(endpoint.get("model") or ""),
        }
        if not config["base_url"]:
            warnings.append("未声明 endpoint.base_url，需要在使用时补全")
    elif kind == "mcp_server":
        command = endpoint.get("command")
        if not (isinstance(command, list) and command):
            raise ManifestError("hub_manifest_mcp_command", "mcp_server 必须声明 endpoint.command 数组")
        config = {"server": str(endpoint.get("server") or adapter_id), "command": command}
        if isinstance(endpoint.get("env"), dict):
            config["env"] = endpoint["env"]
    elif kind == "knowledge_source":
        source_id = str(endpoint.get("source_id") or "")
        if source_id not in {"ima", "baidu_pan"}:
            raise ManifestError(
                "hub_manifest_knowledge_source",
                f"knowledge_source 的 endpoint.source_id 必须是 ima 或 baidu_pan，收到 '{source_id}'",
            )
        config = {"source_id": source_id}
    else:  # http_webhook / tool_plugin
        url = str(endpoint.get("url") or "")
        if not url:
            warnings.append("未声明 endpoint.url，注册后需补全才能探活")
        config = {
            "url": url,
            "method": str(endpoint.get("method") or "POST").upper(),
            "headers": endpoint.get("headers") if isinstance(endpoint.get("headers"), dict) else {},
            "body_template": endpoint.get("body_template"),
            "timeout_seconds": float(endpoint.get("timeout_seconds") or 10),
            "retries": int(endpoint.get("retries") or 0),
            "response_path": str(endpoint.get("response_path") or ""),
        }
        if isinstance(endpoint.get("health"), dict):
            config["health"] = endpoint["health"]

    capabilities_raw = raw.get("capabilities") or []
    if not isinstance(capabilities_raw, list):
        raise ManifestError("hub_manifest_capabilities", "capabilities 必须是数组")
    capabilities: list[dict[str, Any]] = []
    for item in capabilities_raw:
        if isinstance(item, str):
            capabilities.append({"name": item, "tags": [], "description": ""})
            continue
        if not isinstance(item, dict) or not item.get("name"):
            raise ManifestError("hub_manifest_capability", "capabilities 每项都要有 name")
        tags = item.get("tags") or []
        capabilities.append({
            "name": str(item["name"]),
            "tags": [str(t) for t in tags if str(t).strip()],
            "description": str(item.get("description") or ""),
        })

    params = raw.get("params")
    if params is not None and (not isinstance(params, dict) or params.get("type") != "object"):
        raise ManifestError("hub_manifest_params", "params 必须是 {\"type\": \"object\"} 的 JSON Schema")
    if kind == "tool_plugin" and params is None:
        warnings.append("tool_plugin 建议声明 params（参数 schema），否则调用方无法校验入参")

    secret_fields = [f["key"] for f in credential_fields if f["secret"]]
    normalized = {
        "schema": schema,
        "id": adapter_id,
        "name": str(raw["name"]).strip(),
        "kind": kind,
        "group": KIND_GROUP.get(kind, "tool"),
        "icon": str(raw.get("icon") or "🔌"),
        "description": str(raw.get("description") or ""),
        "config": config,
        "credential_fields": credential_fields,
        "secret_fields": secret_fields,
        "capabilities": capabilities,
        "params": params,
    }
    return normalized, warnings


def import_manifest(text: str, filename: str = "") -> tuple[dict[str, Any], list[str]]:
    """Parse + validate. Convenience wrapper for the API layer."""
    return validate_manifest(parse_manifest_text(text, filename))


EXAMPLE_MANIFEST = """\
schema: fy-hub-adapter/v1
id: my-search
name: 我的企业搜索
kind: http_webhook
icon: 🔍
description: 把公司内部搜索接口接进中台
endpoint:
  url: https://api.example.com/v1/search
  method: POST
  headers:
    Authorization: "Bearer {credential.api_token}"
  body_template: '{"q": "{param.query}", "limit": 5}'
  timeout_seconds: 10
  retries: 2
  response_path: data.results
  health:
    method: GET
    expect_status: [200]
auth:
  fields:
    - key: api_token
      label: API Token
      secret: true
      required: true
capabilities:
  - name: search
    tags: [search, enterprise]
    description: 企业内全文检索
params:
  type: object
  properties:
    query: {type: string, minLength: 1}
  required: [query]
"""
