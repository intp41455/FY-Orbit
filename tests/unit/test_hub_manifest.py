"""W6 · 声明式 manifest（增补 B）+ 内置预置库（增补 C）。"""

from __future__ import annotations

import pytest

from find_yourself.services.errors import ValidationFailed
from find_yourself.services.hub import manifest as M
from find_yourself.services.hub.connections import HubService
from find_yourself.services.hub.presets import get_preset, public_presets

WEBHOOK_MANIFEST = """
schema: fy-hub-adapter/v1
id: my-search
name: 我的企业搜索
kind: http_webhook
icon: "\U0001f50d"
description: 把公司内部搜索接口接进中台
endpoint:
  url: https://api.example.com/v1/search
  method: POST
  headers:
    Authorization: "Bearer {credential.api_token}"
  body_template: '{"q": "{param.query}"}'
  response_path: data.results
auth:
  fields:
    - key: api_token
      label: API Token
      secret: true
      required: true
capabilities:
  - name: search
    tags: [search, enterprise]
params:
  type: object
  properties:
    query: {type: string, minLength: 1}
  required: [query]
"""


# --- 解析 -------------------------------------------------------------------- #

def test_parses_yaml_manifest():
    raw = M.parse_manifest_text(WEBHOOK_MANIFEST, "my-search.yaml")
    assert raw["id"] == "my-search"
    assert raw["kind"] == "http_webhook"


def test_parses_json_manifest():
    raw = M.parse_manifest_text('{"schema":"fy-hub-adapter/v1","id":"x","name":"X","kind":"mcp_server"}')
    assert raw["id"] == "x"


def test_empty_manifest_is_an_error():
    with pytest.raises(ValidationFailed) as err:
        M.parse_manifest_text("   ")
    assert err.value.code == "hub_manifest_empty"


def test_unparsable_manifest_is_an_error():
    with pytest.raises(ValidationFailed) as err:
        M.parse_manifest_text("{{{ not really yaml")
    assert err.value.code == "hub_manifest_unparsable"


# --- 校验 -------------------------------------------------------------------- #

def test_valid_manifest_normalises_into_a_connection():
    normalized, warnings = M.import_manifest(WEBHOOK_MANIFEST, "my-search.yaml")
    assert warnings == []
    assert normalized["kind"] == "http_webhook"
    assert normalized["group"] == "tool"
    assert normalized["secret_fields"] == ["api_token"]
    assert normalized["config"]["url"] == "https://api.example.com/v1/search"
    assert normalized["capabilities"][0]["tags"] == ["search", "enterprise"]
    assert normalized["params"]["type"] == "object"


def test_wrong_schema_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({"schema": "other/v9", "id": "a", "name": "A", "kind": "http_webhook"})
    assert err.value.code == "hub_manifest_schema"


def test_unknown_kind_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({"schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A", "kind": "carrier"})
    assert err.value.code == "hub_manifest_kind"


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({"schema": M.MANIFEST_SCHEMA, "id": "a"})
    assert err.value.code == "hub_manifest_missing_field"


def test_mcp_manifest_requires_command():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({"schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A",
                             "kind": "mcp_server", "endpoint": {}})
    assert err.value.code == "hub_manifest_mcp_command"


def test_knowledge_manifest_requires_known_source():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({"schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A",
                             "kind": "knowledge_source", "endpoint": {"source_id": "notion"}})
    assert err.value.code == "hub_manifest_knowledge_source"


def test_bad_credential_key_is_rejected():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({
            "schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A", "kind": "http_webhook",
            "auth": {"fields": [{"key": "9bad key"}]},
        })
    assert err.value.code == "hub_manifest_auth_key"


def test_params_must_be_an_object_schema():
    with pytest.raises(ValidationFailed) as err:
        M.validate_manifest({
            "schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A", "kind": "tool_plugin",
            "params": {"type": "array"},
        })
    assert err.value.code == "hub_manifest_params"


def test_missing_url_only_warns():
    normalized, warnings = M.validate_manifest({
        "schema": M.MANIFEST_SCHEMA, "id": "a", "name": "A", "kind": "http_webhook",
    })
    assert any("url" in w for w in warnings)
    assert normalized["config"]["url"] == ""


def test_bundled_example_manifest_validates():
    normalized, _warnings = M.import_manifest(M.EXAMPLE_MANIFEST, "example.yaml")
    assert normalized["id"] == "my-search"


# --- 导入成连接（零代码）------------------------------------------------------- #

def test_import_creates_connection_and_flags_missing_credentials(session, owner):
    hub = HubService(session)
    created = hub.import_manifest_connection(owner, text=WEBHOOK_MANIFEST,
                                             filename="my-search.yaml")
    assert created["state"] == "needs_credentials"
    assert created["kind"] == "http_webhook"
    assert created["has_manifest"] is True


def test_import_with_credentials_is_active_and_masked(session, owner):
    hub = HubService(session)
    created = hub.import_manifest_connection(
        owner, text=WEBHOOK_MANIFEST, filename="my-search.yaml",
        credentials={"api_token": "tok-supersecret-9999"},
    )
    assert created["state"] == "active"
    assert "supersecret" not in str(created)
    assert "****" in created["config"]["api_token"]
    # 能力清单直接来自 manifest 声明
    names = [c["name"] for c in created["capabilities"]]
    assert names == ["search"]


def test_duplicate_connection_name_is_rejected(session, owner):
    hub = HubService(session)
    hub.import_manifest_connection(owner, text=WEBHOOK_MANIFEST, filename="a.yaml")
    from find_yourself.services.errors import Conflict

    with pytest.raises(Conflict) as err:
        hub.import_manifest_connection(owner, text=WEBHOOK_MANIFEST, filename="b.yaml")
    assert err.value.code == "hub_name_taken"


# --- 内置预置库 ---------------------------------------------------------------- #

def test_presets_cover_every_kind_group_and_number_at_least_seven():
    presets = public_presets()
    assert len(presets) >= 7
    groups = {p["group"] for p in presets}
    assert groups == {"ai", "knowledge", "tool"}
    assert all(p["kind"] for p in presets)


def test_preset_lookup_and_unknown():
    assert get_preset("ollama")["kind"] == "openai_chat"
    assert get_preset("nope") is None


def test_preset_creating_a_connection_fills_defaults(session, owner):
    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "本地 Ollama", "kind": "openai_chat", "preset_id": "ollama",
        "credentials": {},
    })
    assert created["config"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert created["preset_id"] == "ollama"


def test_preset_kind_mismatch_is_rejected(session, owner):
    hub = HubService(session)
    with pytest.raises(ValidationFailed) as err:
        hub.create_connection(owner, {"name": "x", "kind": "mcp_server", "preset_id": "ollama"})
    assert err.value.code == "hub_preset_kind_mismatch"


def test_not_implemented_preset_lands_in_error_state(session, owner):
    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "网盘", "kind": "knowledge_source", "preset_id": "baidu_pan",
    })
    # 骨架未接入 = 诚实标红，不允许被路由当可用
    assert created["state"] == "error"
