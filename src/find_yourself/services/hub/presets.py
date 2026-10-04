"""内置预置库（W6 增补 C）：开箱即用的一键模板。

用户在 HubPage 点「使用预置」，只需填 Key / 地址即可生成一条连接——不需要
理解 manifest 长什么样。每个预置只是**默认值 + 凭证字段 + 能力标签**的声明，
不含任何真实凭证。

诚实标注：``implemented=False`` 的预置（如百度网盘骨架）会在 UI 上显示
「内置骨架 · 未接入」，探活也如实报不可用——绝不因为「有预置」就假装可用。
"""

from __future__ import annotations

from typing import Any

from . import KIND_ANTHROPIC, KIND_HTTP_WEBHOOK, KIND_KNOWLEDGE_SOURCE, KIND_MCP_SERVER, KIND_OPENAI_CHAT, KIND_TOOL_PLUGIN


def _preset(
    preset_id: str,
    name: str,
    kind: str,
    group: str,
    icon: str,
    description: str,
    *,
    config: dict[str, Any] | None = None,
    credential_fields: list[dict[str, Any]] | None = None,
    capability_tags: list[str] | None = None,
    capabilities: list[dict[str, Any]] | None = None,
    implemented: bool = True,
    note: str = "",
) -> dict[str, Any]:
    return {
        "id": preset_id,
        "name": name,
        "kind": kind,
        "group": group,
        "icon": icon,
        "description": description,
        "config": dict(config or {}),
        "credential_fields": list(credential_fields or []),
        "capability_tags": list(capability_tags or []),
        "capabilities": list(capabilities or []),
        "implemented": implemented,
        "note": note,
    }


PRESETS: list[dict[str, Any]] = [
    _preset(
        "ollama", "Ollama 本地模型", KIND_OPENAI_CHAT, "ai", "🦙",
        "本机 Ollama 守护进程的 OpenAI 兼容端点；推理在本地发生，零厂商费用。",
        config={"provider_id": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen2.5:7b"},
        credential_fields=[],
        capability_tags=["chat", "llm", "local", "free"],
        note="本地推理无需 API Key；确认 Ollama 已在 11434 端口监听。",
    ),
    _preset(
        "openai", "OpenAI", KIND_OPENAI_CHAT, "ai", "🤖",
        "OpenAI 及任何 OpenAI 兼容网关（one-api / vLLM / 中转）。",
        config={"provider_id": "openai_compat", "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini"},
        credential_fields=[{"key": "api_key", "label": "API Key", "secret": True, "required": True}],
        capability_tags=["chat", "llm", "cloud"],
    ),
    _preset(
        "deepseek", "DeepSeek", KIND_OPENAI_CHAT, "ai", "🌊",
        "DeepSeek 官方 OpenAI 兼容端点。",
        config={"provider_id": "openai_compat", "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat"},
        credential_fields=[{"key": "api_key", "label": "API Key", "secret": True, "required": True}],
        capability_tags=["chat", "llm", "cloud"],
    ),
    _preset(
        "anthropic", "Anthropic Claude", KIND_ANTHROPIC, "ai", "🧠",
        "Anthropic Messages API（经 W4 的 anthropic provider）。",
        config={"provider_id": "anthropic", "base_url": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet-latest"},
        credential_fields=[{"key": "api_key", "label": "API Key", "secret": True, "required": True}],
        capability_tags=["chat", "llm", "cloud"],
    ),
    _preset(
        "ima", "ima 知识库", KIND_KNOWLEDGE_SOURCE, "knowledge", "📚",
        "腾讯 ima 个人知识库（W3 适配器），经 hub 统一注册与加密凭证存储。",
        config={"source_id": "ima"},
        credential_fields=[
            {"key": "api_key", "label": "API Key", "secret": True, "required": True},
            {"key": "base_url", "label": "API Base URL", "secret": False, "required": True},
        ],
        capability_tags=["knowledge", "search", "document"],
        note="端点路径以 ima 开放平台文档为准，默认值为待核对占位。",
    ),
    _preset(
        "baidu_pan", "百度网盘", KIND_KNOWLEDGE_SOURCE, "knowledge", "☁️",
        "百度网盘知识源（v1 仅骨架，未接入）。",
        config={"source_id": "baidu_pan"},
        credential_fields=[
            {"key": "app_key", "label": "AppKey", "secret": True, "required": False},
            {"key": "app_secret", "label": "SecretKey", "secret": True, "required": False},
            {"key": "redirect_uri", "label": "回调地址", "secret": False, "required": False},
        ],
        capability_tags=["knowledge", "document"],
        implemented=False,
        note="骨架：调用一律显式报 baidu_pan_not_implemented，不返回假结果。",
    ),
    _preset(
        "generic_mcp", "通用 MCP 服务器", KIND_MCP_SERVER, "tool", "🧩",
        "任意 MCP 服务器（stdio 子进程），工具以 hub.<server>.<tool> 进入工具注册中心。",
        config={"server": "my-mcp", "command": ["python", "-m", "find_yourself.adapters.mcp"]},
        credential_fields=[],
        capability_tags=["tool", "mcp"],
        note="command 是启动该 MCP 服务器的命令数组；工具由 tools/list 真实发现。",
    ),
    _preset(
        "generic_webhook", "通用 Webhook", KIND_HTTP_WEBHOOK, "tool", "🔌",
        "任何有 HTTP 接口的系统（含没有官方 API 的内部系统）。",
        config={"url": "", "method": "POST", "timeout_seconds": 10, "retries": 2,
                "response_path": ""},
        credential_fields=[{"key": "api_token", "label": "API Token", "secret": True,
                            "required": False}],
        capability_tags=["http", "webhook"],
        note="默认禁止内网地址（SSRF 防护）；本机服务需 FY_HUB_ALLOW_PRIVATE=1。",
    ),
    _preset(
        "tool_plugin", "自定义工具（manifest）", KIND_TOOL_PLUGIN, "tool", "🛠️",
        "用声明式 manifest 描述一个工具：参数 schema + HTTP 端点，零代码接入。",
        config={"url": "", "method": "POST", "timeout_seconds": 10, "retries": 1},
        credential_fields=[{"key": "api_token", "label": "API Token", "secret": True,
                            "required": False}],
        capability_tags=["tool", "custom"],
        note="也可直接在 HubPage 导入 manifest 文件（YAML/JSON），社区分享的最小单位。",
    ),
]


def preset_ids() -> list[str]:
    return [p["id"] for p in PRESETS]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    for preset in PRESETS:
        if preset["id"] == preset_id:
            return preset
    return None


def public_presets() -> list[dict[str, Any]]:
    """Static, secret-free catalog for the UI."""
    return [dict(p) for p in PRESETS]
