"""D06 BYOK 界面持久化：模型凭证服务端加密存储 + 网关叠加。

G-2 后半的收口：模型 BYOK 不再只是「设置页生成 env 片段让用户手贴」——
凭证经 ``secret_store``（hub Fernet 加密落盘，与 ima 凭证同一机制、
``storage_mode`` 同一诚实口径）持久化，网关构造时叠加生效。

安全边界（与 ima 凭证一致）：
* 状态/读取接口**永不返回明文 key**（只有 ``has_api_key`` 布尔）；
* 存储是**机器级**（secret_store 单例，非按 owner 隔离），与本机单所有者
  定位一致；多租户 Route B 下仍以 .env 为准（resolve 只叠加非空值，
  不改变 settings 的其他行为）；
* 写入需认证 + CSRF（路由层），审计留痕（存/删，不记值）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from .actor import Actor
from .audit import AuditService
from .errors import ValidationFailed
from .knowledge.sources.store import secret_store

#: secret_store 中的作用域（与知识源凭证键空间隔离）。
MODEL_CREDENTIALS_SCOPE = "model:default"


class ModelCredentialService:
    """模型 BYOK 凭证的存/读（脱敏）/删/叠加。"""

    def __init__(self, session: Session, audit: AuditService):
        self.s = session
        self.audit = audit

    # -- 写 --------------------------------------------------------------- #
    def save(
        self,
        actor: Actor,
        *,
        provider: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        actor.require_authenticated()
        provider = (provider or "").strip()
        base_url = (base_url or "").strip()
        api_key = api_key or ""
        model = (model or "").strip()
        if not provider and not base_url:
            raise ValidationFailed(
                "model_endpoint_required",
                "至少给出 provider 或 base_url 其中之一（本地 Ollama 可只填 base_url）",
            )
        secret_store.set(MODEL_CREDENTIALS_SCOPE, {
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
        })
        self.audit.append(actor, "model.credentials_saved", MODEL_CREDENTIALS_SCOPE, {
            "provider": provider, "base_url": base_url, "model": model,
            "has_api_key": bool(api_key),
        })
        self.s.flush()
        return self.status(actor)

    def forget(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        removed = secret_store.forget(MODEL_CREDENTIALS_SCOPE)
        self.audit.append(actor, "model.credentials_forgotten",
                          MODEL_CREDENTIALS_SCOPE, {"forgotten": removed})
        self.s.flush()
        return {"forgotten": removed}

    # -- 读（脱敏） -------------------------------------------------------- #
    def status(self, actor: Actor) -> dict[str, Any]:
        actor.require_authenticated()
        stored = secret_store.get(MODEL_CREDENTIALS_SCOPE)
        storage, persist = secret_store.storage_mode()
        provider = str(stored.get("provider") or "")
        base_url = str(stored.get("base_url") or "")
        return {
            "configured": bool(provider or base_url),
            "provider": provider,
            "base_url": base_url,
            "model": str(stored.get("model") or ""),
            "has_api_key": bool(stored.get("api_key")),
            "storage": storage,
            "persist_restart": persist,
        }

    # -- 网关叠加 ----------------------------------------------------------- #
    def resolve(self, settings: Settings) -> Settings:
        """把已存凭证叠加到 settings **副本**上；未存则原样返回 base。

        网关构造方（companion / inference / catalog探针）调本函数而不是直读
        settings——.env 仍是底线默认值，已存凭证优先覆盖非空项。
        不需要 actor：读的是机器级存储（与 status 的脱敏读不同，这里返回的是
        生效中的完整 settings 副本，调用方是服务端内部）。
        """
        return resolve_model_settings(settings)


def resolve_model_settings(settings: Settings) -> Settings:
    """模块级叠加（无 session 依赖，供网关构造方直接调用）。"""
    stored = secret_store.get(MODEL_CREDENTIALS_SCOPE)
    overlay: dict[str, Any] = {}
    if stored.get("provider"):
        overlay["model_provider"] = stored["provider"]
    if stored.get("base_url"):
        overlay["model_base_url"] = stored["base_url"]
    if stored.get("api_key"):
        overlay["model_api_key"] = stored["api_key"]
    if stored.get("model"):
        overlay["model_name"] = stored["model"]
    if not overlay:
        return settings
    return settings.model_copy(update=overlay)
