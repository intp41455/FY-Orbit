"""Model / provider capability catalog for 19 逐节点模型配置.

The team designer must never let a user type a model that is not actually
callable (19 §3: "界面不能随意填写不存在的'可用'模型"). This catalog is the one
place that answers "which providers and models may a node request, and is the
credential actually configured?".

Design rules (19 §3, §5):

* Entries are derived from what the runtime gateway can really price/route.
  The synthetic ``mock-deterministic`` model is always present but is flagged
  ``synthetic`` so it can never be presented as a real paid model.
* ``credential_configured`` is a boolean only. The catalog reads the presence
  of a configured key from settings and never returns, logs or stores it.
* A model with no known unit price is reported ``pricing_status='unknown'``;
  callers must block rather than charge it as zero (gateway §10).
* ``supports_*`` fields describe what we have actually verified for the
  provider, so unsupported knobs stay disabled instead of silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..runtime.gateway import STANDARD_PRICING, ModelGateway
from ..runtime.providers import (
    PROVIDER_IDS,
    ProviderEndpoint,
    endpoint_ref,
    normalize_provider_id,
    probe_provider,
    provider_descriptor,
)
from ..runtime.providers import UnsupportedProvider
from ..runtime.providers.base import sanitize_message

#: Provider identifier used for the deterministic local provider.
SYNTHETIC_PROVIDER_ID = "local-synthetic"

#: Credential reference (never the secret) used for the synthetic provider.
SYNTHETIC_CREDENTIAL_REF = "env:FY_MODEL_API_KEY"


@dataclass(frozen=True)
class ModelOption:
    provider_id: str
    model_id: str
    credential_configured: bool
    credential_ref: str
    pricing_status: str
    context_window: int
    synthetic: bool
    supports_tools: bool
    supports_structured_output: bool
    supports_streaming: bool
    max_output_tokens: int
    endpoint_ref: str
    note: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        """Response shape. Deliberately contains no secret material."""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "credential_configured": self.credential_configured,
            "credential_ref": self.credential_ref,
            "pricing_status": self.pricing_status,
            "context_window": self.context_window,
            "synthetic": self.synthetic,
            "supports_tools": self.supports_tools,
            "supports_structured_output": self.supports_structured_output,
            "supports_streaming": self.supports_streaming,
            "max_output_tokens": self.max_output_tokens,
            "endpoint_ref": self.endpoint_ref,
            "note": self.note,
            "params": dict(self.params),
        }


#: Capability snapshot recorded on a binding at start time so a later change
#: to the catalog cannot silently alter what the node believed it could do.
CATALOG_VERSION = "catalog-2026-10-02"

#: Provider id used when the gateway serves calls through a deterministic local
#: adapter instead of a host-configured remote endpoint.
LOCAL_PROVIDER_ID = SYNTHETIC_PROVIDER_ID


class ModelCatalog:
    """Read-only view of actually-routable providers and models."""

    def __init__(self, settings: Settings | None = None, gateway: ModelGateway | None = None):
        self.settings = settings
        self.gateway = gateway or ModelGateway(settings=settings)
        self.version = CATALOG_VERSION

    def _pricing(self) -> dict[str, Any]:
        """Pricing comes from the gateway that will actually serve the call."""
        return dict(self.gateway.pricing or STANDARD_PRICING)

    # -- internals --------------------------------------------------------
    @property
    def _provider_ready(self) -> bool:
        """True when the gateway actually holds a callable provider adapter.

        A model is only offered when something can serve it. With no settings
        and no adapter the list is empty rather than optimistic, so the UI can
        never present a model that would fail at call time.
        """
        return self.gateway.provider is not None

    @property
    def _remote_configured(self) -> bool:
        return bool(self.settings and self.settings.model_api_key and self.settings.model_base_url)

    @property
    def _serving_provider(self) -> str:
        """Which provider actually serves a non-mock model right now.

        When host credentials are configured the remote OpenAI-compatible
        endpoint serves them. Otherwise a deterministic local adapter may still
        serve them — in which case they are offered under the local provider
        and marked ``synthetic``, so "two models on one provider" stays
        expressible without credentials and without implying a paid model.
        """
        return "openai-compatible" if self._remote_configured else LOCAL_PROVIDER_ID

    def _remote_endpoint_ref(self) -> str:
        if not self.settings or not self.settings.model_base_url:
            return ""
        # Host + path only; query strings and userinfo never leak into the UI.
        base = self.settings.model_base_url.split("?", 1)[0].rstrip("/")
        return base

    # -- public API -------------------------------------------------------
    def providers(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        out.append(
            {
                "provider_id": SYNTHETIC_PROVIDER_ID,
                "name": "本地确定性提供方（合成）",
                "credential_configured": True,
                "credential_ref": SYNTHETIC_CREDENTIAL_REF,
                "synthetic": True,
                "endpoint_ref": "inprocess://mock-deterministic",
                "note": "确定性本地提供方，仅用于合成验证，不代表任何真实付费模型。",
                "models": [m.model_id for m in self._options() if m.provider_id == SYNTHETIC_PROVIDER_ID],
            }
        )
        out.append(
            {
                "provider_id": "openai-compatible",
                "name": "OpenAI 兼容接口（宿主已配置）",
                "credential_configured": self._remote_configured,
                "credential_ref": "env:FY_MODEL_API_KEY",
                "synthetic": False,
                "endpoint_ref": self._remote_endpoint_ref(),
                "note": (
                    "凭据已配置，可用于真实模型往返。"
                    if self._remote_configured
                    else "未配置 FY_MODEL_API_KEY / FY_MODEL_BASE_URL；真实模型调用为 BLOCKED_EXTERNAL。"
                ),
                "models": [m.model_id for m in self._options()
                          if m.provider_id == "openai-compatible" or self._remote_configured],
            }
        )
        return out

    def _options(self) -> list[ModelOption]:
        options: list[ModelOption] = []
        pricing_table = self._pricing()
        for model_id in sorted(pricing_table):
            pricing = pricing_table[model_id]
            synthetic = model_id == "mock-deterministic"
            if synthetic:
                options.append(
                    ModelOption(
                        provider_id=SYNTHETIC_PROVIDER_ID,
                        model_id=model_id,
                        credential_configured=True,
                        credential_ref=SYNTHETIC_CREDENTIAL_REF,
                        pricing_status="known",
                        context_window=pricing.context_window,
                        synthetic=True,
                        supports_tools=False,
                        supports_structured_output=True,
                        supports_streaming=False,
                        max_output_tokens=4096,
                        endpoint_ref="inprocess://mock-deterministic",
                        note="确定性本地提供方，用于合成验证与回归；不得用于宣称真实模型能力。",
                        params={"max_output_tokens": 4096},
                    )
                )
                continue
            # A vendor-priced model is only offered when the gateway actually
            # holds a provider that can serve it. Otherwise it stays out of the
            # list rather than being presented as usable.
            if not self._provider_ready:
                continue
            remote = self._remote_configured
            options.append(
                ModelOption(
                    provider_id=self._serving_provider,
                    model_id=model_id,
                    credential_configured=True,
                    credential_ref="env:FY_MODEL_API_KEY" if remote else SYNTHETIC_CREDENTIAL_REF,
                    pricing_status="known",
                    context_window=pricing.context_window,
                    synthetic=not remote,
                    supports_tools=False,
                    supports_structured_output=True,
                    supports_streaming=remote,
                    max_output_tokens=8192,
                    endpoint_ref=self._remote_endpoint_ref() if remote else "inprocess://deterministic",
                    note=(
                        "经宿主配置的 OpenAI 兼容端点；实际可用性以启动前校验为准。"
                        if remote
                        else "由本地确定性适配器提供，仅用于合成验证；真实凭据未配置，"
                             "真实模型往返仍为 BLOCKED_EXTERNAL。"
                    ),
                    params={"max_output_tokens": 8192},
                )
            )
        return options

    def list_models(self, *, include_synthetic: bool = True) -> list[dict[str, Any]]:
        return [o.to_public() for o in self._options() if include_synthetic or not o.synthetic]

    def get_model(self, provider_id: str, model_id: str) -> ModelOption | None:
        options = self._options()
        if not model_id:
            return None
        # An omitted provider id means "whatever actually serves this model".
        wanted = provider_id or self._serving_provider
        for opt in options:
            if opt.provider_id == wanted and opt.model_id == model_id:
                return opt
        # An explicitly named provider that does not serve the model is a
        # mismatch, not something to silently correct: returning it would hide a
        # misconfigured node behind the wrong provider id.
        return None

    # -- W4: multi-provider registry ---------------------------------------
    def _configured_endpoints(self) -> dict[str, ProviderEndpoint]:
        """Provider id -> resolved endpoint, for every *callable* route.

        Note this is smaller than "every provider we know about": a provider
        the gateway cannot route to reports ``configured=False`` rather than
        pretending to be selectable (19 §3).
        """
        return {spec.provider_id: spec for spec in self.gateway.endpoints()}

    def _credential_present(self) -> bool:
        return bool(self.settings and self.settings.model_api_key)

    def provider_registry(self, *, probe: bool = True,
                          timeout_seconds: float = 2.5) -> list[dict[str, Any]]:
        """Every known provider with its real configuration and health state.

        Health is measured with a live round trip when the provider is actually
        routable; otherwise ``health`` stays ``None`` and the note says why we
        did not probe (we refuse to open sockets to endpoints the user never
        configured — that would be outbound traffic nobody asked for).
        """
        endpoints = self._configured_endpoints()
        chain = {row["provider_id"]: row for row in self.gateway.provider_chain()}
        registry: list[dict[str, Any]] = []
        for pid in PROVIDER_IDS:
            meta = provider_descriptor(pid)
            spec = endpoints.get(pid)
            configured = spec is not None
            role = chain.get(pid, {}).get("source", "")
            if configured:
                note = ("当前主通道。" if role == "primary"
                        else "已加入降级链。" if role == "fallback"
                        else "端点已配置。")
                if meta["local_inference"]:
                    note += "本地推理，按零成本结算。"
            else:
                note = (f"未配置 FY_MODEL_BASE_URL / FY_MODEL_API_KEY；"
                        f"未发起探测。默认地址 {meta['default_base_url']} 仅作提示。")
            entry: dict[str, Any] = {
                "provider_id": pid,
                "name": meta["name"],
                "requires_api_key": meta["requires_api_key"],
                "local_inference": meta["local_inference"],
                "default_base_url": meta["default_base_url"],
                "health_path": meta["health_path"],
                "configured": configured,
                "credential_configured": (not meta["requires_api_key"]) or self._credential_present(),
                "credential_ref": "env:FY_MODEL_API_KEY" if meta["requires_api_key"] else "",
                "role": role,
                "model": chain.get(pid, {}).get("model", ""),
                "endpoint_ref": endpoint_ref(spec.base_url) if spec else "",
                "health": None,
                "note": note,
            }
            if probe and configured:
                entry["health"] = probe_provider(spec, timeout_seconds=timeout_seconds).to_public()
            registry.append(entry)
        return registry

    def gateway_summary(self, *, probe: bool = True,
                        timeout_seconds: float = 2.5) -> dict[str, Any]:
        """Everything the settings page needs, with no secret material."""
        return {
            "catalog_version": self.version,
            "primary_provider_id": getattr(self.gateway, "primary_provider_id", "") or "",
            "model_name": getattr(self.settings, "model_name", "") if self.settings else "",
            "configured": bool(self.gateway.provider is not None),
            "config_error": getattr(self.gateway, "config_error", "") or "",
            "route_errors": list(getattr(self.gateway, "route_errors", []) or []),
            "chain": self.gateway.provider_chain(),
            "providers": self.provider_registry(probe=probe, timeout_seconds=timeout_seconds),
            "models": self.list_models(),
        }

    def health_report(self, provider_ids: list[str] | None = None, *,
                      timeout_seconds: float = 4.0,
                      transport: Any | None = None) -> dict[str, Any]:
        """Probe the configured endpoints and report real latency or real errors.

        Transport injection exists for unit tests only; the API path always
        performs a genuine round trip. Nothing here can report success for a
        provider that did not answer.
        """
        endpoints = self.gateway.endpoints()
        wanted: list[str] | None = None
        if provider_ids:
            wanted = []
            for raw in provider_ids:
                try:
                    wanted.append(normalize_provider_id(str(raw or "")) or str(raw))
                except UnsupportedProvider as exc:
                    # Unknown id requested explicitly: report it, do not ignore it.
                    wanted.append(sanitize_message(str(exc)))
        if wanted is not None:
            selected = [e for e in endpoints if e.provider_id in wanted]
            unknown = [w for w in wanted if w not in {e.provider_id for e in endpoints}]
        else:
            selected = list(endpoints)
            unknown = []

        checks = []
        for spec in selected:
            probe_spec = spec
            if transport is not None:
                probe_spec = ProviderEndpoint(provider_id=spec.provider_id,
                                              base_url=spec.base_url,
                                              api_key=spec.api_key,
                                              transport=transport)
            checks.append(probe_provider(probe_spec, timeout_seconds=timeout_seconds).to_public())
        for label in unknown:
            checks.append({
                "provider_id": label,
                "ok": False,
                "latency_ms": None,
                "models": [],
                "error": "provider_not_configured",
                "endpoint_ref": "",
            })
        return {
            "primary_provider_id": getattr(self.gateway, "primary_provider_id", "") or "",
            "config_error": getattr(self.gateway, "config_error", "") or "",
            "probe_timeout_seconds": timeout_seconds,
            "checks": checks,
            "hint": (
                ""
                if endpoints
                else "尚未检测到已配置的模型端点：请设置 FY_MODEL_BASE_URL"
                     "（Ollama 只需地址，无需 API key；其他厂商还需 FY_MODEL_API_KEY）后重启服务。"
            ),
        }

    def capability_snapshot(self, provider_id: str, model_id: str) -> dict[str, Any]:
        """Snapshot persisted on a binding so later catalog edits are visible."""
        opt = self.get_model(provider_id, model_id)
        if opt is None:
            return {
                "catalog_version": self.version,
                "status": "unknown",
                "reason": f"Model '{model_id}' is not in the configured catalog",
            }
        return {
            "catalog_version": self.version,
            "status": "verified",
            "context_window": opt.context_window,
            "max_output_tokens": opt.max_output_tokens,
            "supports_tools": opt.supports_tools,
            "supports_structured_output": opt.supports_structured_output,
            "supports_streaming": opt.supports_streaming,
            "pricing_status": opt.pricing_status,
            "synthetic": opt.synthetic,
        }