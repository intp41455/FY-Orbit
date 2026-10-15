"""D06 BYOK 界面持久化：模型凭证服务端加密存储（hub Fernet）+ 网关叠加。

* 保存后状态只给脱敏信息（明文 key 永不外泄）；
* ``resolve`` 把已存凭证叠加到 settings 副本上（原对象不动），网关构造方调用；
* 未存时 ``resolve`` 原样返回 base；
* forget 清除后 status 即未配置。
"""

from __future__ import annotations

import json

import pytest

from find_yourself.config import Settings
from find_yourself.services.audit import AuditService
from find_yourself.services.errors import ValidationFailed
from find_yourself.services.knowledge.sources.store import secret_store
from find_yourself.services.model_credentials import (
    MODEL_CREDENTIALS_SCOPE,
    ModelCredentialService,
)


@pytest.fixture(autouse=True)
def _isolated_credential_store():
    """secret_store 是进程级单例（非测试 DB）：每个用例前后清场防串味。"""
    secret_store.forget(MODEL_CREDENTIALS_SCOPE)
    yield
    secret_store.forget(MODEL_CREDENTIALS_SCOPE)


def _settings(**kw) -> Settings:
    base = {"session_secret": "x" * 32}
    base.update(kw)
    return Settings(**base)


def _svc(session):
    return ModelCredentialService(session, AuditService(session))


def test_save_and_masked_status_never_leaks_plaintext(session, owner):
    svc = _svc(session)
    svc.save(owner, provider="openai_compat", base_url="https://api.example.com/v1",
             api_key="sk-secret-123", model="gpt-4o-mini")
    st = svc.status(owner)
    assert st["configured"] is True
    assert st["provider"] == "openai_compat"
    assert st["base_url"] == "https://api.example.com/v1"
    assert st["model"] == "gpt-4o-mini"
    assert st["has_api_key"] is True
    assert "sk-secret-123" not in json.dumps(st)


def test_resolve_overlays_stored_onto_a_copy(session, owner):
    svc = _svc(session)
    svc.save(owner, provider="openai_compat", base_url="https://api.example.com/v1",
             api_key="sk-secret-123", model="gpt-4o-mini")
    base = _settings()
    assert base.model_api_key == ""
    resolved = svc.resolve(base)
    assert resolved is not base
    assert resolved.model_api_key == "sk-secret-123"
    assert resolved.model_base_url == "https://api.example.com/v1"
    assert resolved.model_name == "gpt-4o-mini"
    assert base.model_api_key == ""  # 原对象不动


def test_resolve_without_stored_returns_base(session, owner):
    svc = _svc(session)
    base = _settings()
    assert svc.resolve(base) is base


def test_forget_clears_status(session, owner):
    svc = _svc(session)
    svc.save(owner, provider="openai_compat", base_url="https://x.example/v1",
             api_key="k", model="m")
    assert svc.status(owner)["configured"] is True
    out = svc.forget(owner)
    assert out["forgotten"] is True
    assert svc.status(owner)["configured"] is False


def test_save_rejects_empty_endpoint(session, owner):
    svc = _svc(session)
    with pytest.raises(ValidationFailed):
        svc.save(owner, provider="", base_url="", api_key="k", model="m")
