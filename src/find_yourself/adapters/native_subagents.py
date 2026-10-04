"""Capability adapter for product-native sub-agent teams (19 §1, §6 ``F``).

``产品原生团队`` maps onto what an external product *actually* exposes: the
sub-agents it really created, their task ids, its event stream and the control
operations it really accepts. This adapter never guesses.

Three states are returned per capability, and the UI must honour them:

``verified``      we probed the real interface and it answered.
``unsupported``   the product documents that it cannot do this; the control
                  must be disabled with the reason shown.
``unknown``       not probed in this environment; the UI shows 待核验 and a
                  start that needs a hard guarantee is blocked.

The built-in registry is deliberately small: Find Yourself itself (full
control) plus the external products the project has actually probed. Products
that only answered a version/help banner are recorded as ``unknown`` with the
reason, not as supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..services.actor import Actor
from ..services.errors import ValidationFailed

#: Every control operation the team service can gate. Keep this in sync with
#: ``CONTROL_OPERATIONS`` in db/team_models.py: a missing key would default to
#: "unknown" and silently block a supported operation.
CAPABILITY_KEYS = (
    "spawn",
    "events",
    "pause",
    "resume",
    "reassign",
    "rework",
    "cancel",
    "switch_model",
    "usage",
    "checkpoint",
)


@dataclass(frozen=True)
class HostCapabilities:
    agent_host: str
    provider_id: str
    capabilities: dict[str, str]
    supports_per_member_model: bool
    usage_metering: str
    probe_source: str
    reason: str

    def to_public(self) -> dict[str, Any]:
        return {
            "agent_host": self.agent_host,
            "provider_id": self.provider_id,
            "capabilities": dict(self.capabilities),
            "supports_per_member_model": self.supports_per_member_model,
            "usage_metering": self.usage_metering,
            "probe_source": self.probe_source,
            "reason": self.reason,
            # The UI keeps a control disabled unless this is 'verified'.
            "disabled_operations": sorted(
                k for k, v in self.capabilities.items() if v != "verified"
            ),
        }

    def require(self, operation: str) -> None:
        state = self.capabilities.get(operation, "unknown")
        if state == "verified":
            return
        if state == "unsupported":
            raise ValidationFailed(
                f"capability_unsupported",
                f"Host '{self.agent_host}' does not support '{operation}'",
            )
        raise ValidationFailed(
            "capability_unverified",
            f"Host '{self.agent_host}' capability '{operation}' is 待核验 (unknown); "
            "a hard-guarantee operation is blocked until it is probed.",
        )


def _all(state: str) -> dict[str, str]:
    return {k: state for k in CAPABILITY_KEYS}


class NativeSubAgentAdapter:
    """Registry of host products and their honestly-probed control surface."""

    #: Find Yourself manages every member itself, so all control is real.
    _SELF = HostCapabilities(
        agent_host="find_yourself",
        provider_id="local-synthetic",
        capabilities=_all("verified"),
        supports_per_member_model=True,
        usage_metering="verified",
        probe_source="self_managed",
        reason="Find Yourself 为每个成员创建独立任务与会话，控制由本服务实现。",
    )

    #: External products. None of these has been verified in this environment;
    #: they are listed so the UI can render 待核验 / 人工交接 rather than
    # pretending an integration exists.
    _CATALOG: dict[str, HostCapabilities] = {
        "find_yourself": _SELF,
        "external_a2a": HostCapabilities(
            agent_host="external_a2a",
            provider_id="",
            capabilities={
                "spawn": "unknown",
                "events": "verified",
                "pause": "unknown",
                "resume": "unknown",
                "cancel": "unknown",
                "reassign": "unknown",
                "rework": "unknown",
                "switch_model": "unsupported",
                "usage": "unsupported",
                "checkpoint": "unknown",
            },
            supports_per_member_model=False,
            usage_metering="unsupported",
            probe_source="static_registry",
            reason=(
                "仅验证了事件流；派生/暂停/改派未在本机核验，逐成员模型覆盖不支持。"
                "无可编程接口时只能人工交接。"
            ),
        ),
        "external_chat_product": HostCapabilities(
            agent_host="external_chat_product",
            provider_id="",
            capabilities={
                "spawn": "unsupported",
                "events": "unsupported",
                "pause": "unsupported",
                "resume": "unsupported",
                "cancel": "unsupported",
                "reassign": "unsupported",
                "rework": "unsupported",
                "switch_model": "unsupported",
                "usage": "unsupported",
                "checkpoint": "unsupported",
            },
            supports_per_member_model=False,
            usage_metering="unsupported",
            probe_source="static_registry",
            reason=(
                "仅有聊天订阅，没有可编程子 Agent 接口：不能承诺自动派生与控制，"
                "只提供明确标注的人工交接。一次聊天订阅不等于 API 使用权。"
            ),
        ),
    }

    def hosts(self) -> list[dict[str, Any]]:
        return [c.to_public() for c in self._CATALOG.values()]

    def get(self, agent_host: str) -> HostCapabilities:
        caps = self._CATALOG.get(agent_host)
        if caps is None:
            raise ValidationFailed(
                f"unknown_agent_host",
                f"No capability record for host '{agent_host}'. It must be probed and "
                "registered before it can be used for a team.",
            )
        return caps

    def probe(self, actor: Actor, agent_host: str) -> dict[str, Any]:
        """Return the recorded capability surface for a host.

        This is a *recorded* probe result, not a live network call: the project
        has not verified any external product's control interface in this
        environment, so claiming a live probe would be false.
        """
        actor.require_authenticated()
        return self.get(agent_host).to_public()

    def can_use_per_member_model(self, agent_host: str) -> bool:
        return self.get(agent_host).supports_per_member_model