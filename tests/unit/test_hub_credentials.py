"""W6 · 凭证加密与存储单测（主控裁决 2026-10-04 要求的三个新测试 + 基础加密）。

三条硬要求：
① 模拟重启（新实例读回已存凭证）
② 回退模式如实标注（storage=memory / persist_restart=False）
③ 凭证掩码不回显（任何对外结构里都不得出现明文）
"""

from __future__ import annotations

import pytest

from find_yourself.services.hub import crypto
from find_yourself.services.hub.connections import HubService, public_connection, slugify
from find_yourself.services.hub.secrets import HubSecretStore
from find_yourself.services.knowledge.sources import list_source_status, secret_store

PLAIN = "sk-live-key-abcdef123456"


# --- 基础加密 ---------------------------------------------------------------- #

def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt_secret(PLAIN)
    assert token.startswith("enc:v1:")
    assert PLAIN not in token
    assert crypto.decrypt_secret(token) == PLAIN


def test_mask_never_reveals_the_value():
    masked = crypto.mask_secret(PLAIN)
    assert masked != PLAIN
    assert "abcdef123456" not in masked
    assert crypto.mask_secret("") == ""
    assert crypto.mask_secret("short") == "*****"


def test_decrypting_non_ciphertext_refuses_to_guess():
    from find_yourself.services.errors import DomainError

    with pytest.raises(DomainError) as err:
        crypto.decrypt_secret("plain-text-secret")
    assert err.value.code == "hub_crypto_not_ciphertext"


# --- ① 模拟重启 -------------------------------------------------------------- #

def test_credentials_survive_a_restart(tmp_path):
    path = tmp_path / "secrets.enc.json"
    store = HubSecretStore(path)
    store.set("knowledge:ima", {"api_key": PLAIN, "base_url": "https://ima.example"})
    assert store.storage_mode() == ("hub_fernet", True)

    # 新实例 = 模拟进程重启
    revived = HubSecretStore(path)
    assert revived.get("knowledge:ima")["api_key"] == PLAIN
    assert revived.masked("knowledge:ima") == {"api_key": True, "base_url": True}


def test_plaintext_never_lands_on_disk(tmp_path):
    path = tmp_path / "secrets.enc.json"
    HubSecretStore(path).set("knowledge:ima", {"api_key": PLAIN})
    raw = path.read_text(encoding="utf-8")
    assert PLAIN not in raw
    assert "enc:v1:" in raw


# --- ② 回退模式如实标注 -------------------------------------------------------- #

def test_fallback_reports_memory_honestly(monkeypatch, tmp_path):
    store = HubSecretStore(tmp_path / "secrets.enc.json")

    def boom(*_args, **_kwargs):
        raise crypto.HubCryptoUnavailable("hub_crypto_unavailable", "主密钥不可用")

    # 必须打在 secrets 模块*导入进来*的那个名字上（from .crypto import ...）
    monkeypatch.setattr("find_yourself.services.hub.secrets.encrypt_secret", boom)
    store.set("knowledge:ima", {"api_key": PLAIN})
    # 内存里还在（功能不丢），但标注必须诚实
    assert store.get("knowledge:ima")["api_key"] == PLAIN
    assert store.storage_mode() == ("memory", False)
    assert "回退" in (store.degraded_reason() or "") or "主密钥" in (store.degraded_reason() or "")


def test_list_source_status_label_matches_real_storage():
    """knowledge 适配器卡的 storage 标注 = 真实存储位置，不是写死的常量。"""
    secret_store.forget("ima")
    statuses = {s["source_id"]: s for s in list_source_status()}
    ima = statuses["ima"]
    mode, persist = secret_store.storage_mode()
    assert ima["storage"] == mode
    assert ima["persist_restart"] is persist


def test_unwritable_path_falls_back_to_memory(tmp_path):
    # 目录被占成文件 → 落盘必然失败
    blocked = tmp_path / "blocked"
    blocked.write_text("not a dir", encoding="utf-8")
    store = HubSecretStore(blocked / "secrets.enc.json")
    store.set("knowledge:ima", {"api_key": PLAIN})
    assert store.storage_mode() == ("memory", False)
    assert store.get("knowledge:ima")["api_key"] == PLAIN


# --- ③ 掩码不回显 -------------------------------------------------------------- #

def test_connection_public_view_never_contains_plaintext(session, owner):
    from find_yourself.db.workbench_models import HubConnection

    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "我的 OpenAI",
        "kind": "openai_chat",
        "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
        "credentials": {"api_key": PLAIN},
    })
    blob = str(created)
    assert PLAIN not in blob
    assert "abcdef123456" not in blob
    assert created["config"]["api_key"].startswith("sk-") and "****" in created["config"]["api_key"]

    # 库里的密文列也不会吐明文
    row = session.get(HubConnection, created["id"])
    assert PLAIN not in str(row.secret_config)
    assert public_connection(row)["config"]["api_key"] != PLAIN


def test_secret_columns_hold_ciphertext_not_plaintext(session, owner):
    from find_yourself.db.workbench_models import HubConnection

    hub = HubService(session)
    created = hub.create_connection(owner, {
        "name": "webhook-1",
        "kind": "http_webhook",
        "config": {"url": "https://api.example.com/hook"},
        "secret_fields": ["api_token"],
        "credentials": {"api_token": PLAIN},
    })
    row = session.get(HubConnection, created["id"])
    assert row.secret_config["api_token"]["enc"].startswith("enc:v1:")
    assert "mask" in row.secret_config["api_token"]
    assert PLAIN not in str(row.endpoint_config)


# --- 其他 -------------------------------------------------------------------- #

def test_unknown_secret_field_is_rejected(session, owner):
    from find_yourself.services.errors import ValidationFailed

    hub = HubService(session)
    with pytest.raises(ValidationFailed) as err:
        hub.create_connection(owner, {
            "name": "webhook-2",
            "kind": "http_webhook",
            "config": {"url": "https://api.example.com/hook"},
            "credentials": {"password": "x"},
        })
    assert err.value.code == "hub_unknown_secret_field"


def test_slugify_produces_registry_safe_names():
    assert slugify("我的 企业 Search!") == "search" or slugify("我的 企业 Search!").startswith("x-")
    assert slugify("abc") == "abc"
    assert slugify("") != ""
