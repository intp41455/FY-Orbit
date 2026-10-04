"""W6 超级中台 · 统一万能适配层（One Interface To Rule Them All，增补 v1.1）。

本 ``__init__`` **只放轻量常量**，不 import 任何子模块。原因：
``services.knowledge.sources`` 需要 import 本包的凭证存储 ``hub.secrets``，
而 ``hub.adapters`` 又要（惰性）import ``knowledge.sources`` 来实现
``knowledge_source`` 适配器——如果在这里 import 适配器，就会形成真正的导入环。
因此常量在此、实现在子模块，按需加载。

四类被接对象（增补 A）归一到一个 :class:`hub.adapters.HubAdapter` 协议：

=================  ==============================================  ==========
kind               接什么                                          管理分组
=================  ==============================================  ==========
openai_chat        任意 OpenAI 兼容对话端点（复用 W4 provider）       AI 服务
anthropic          Anthropic Messages API（复用 W4 provider）        AI 服务
mcp_server         MCP 生态工具 / 技能（全行业开放标准）              工具
http_webhook       万能 HTTP 调用（企业系统 / 无 API 的软件）          工具
knowledge_source   知识源（ima / 百度网盘 / 任意文档源，桥接 W3）      知识源
tool_plugin        用户自定义工具（manifest 声明式，零代码）           工具
=================  ==============================================  ==========

诚实原则：本包不提供任何「假成功」。探测不到的连接就是 ``ok=False``，
拿不到的能力就是抛 :class:`find_yourself.services.errors.DomainError`，
绝不返回构造出来的占位数据。
"""

from __future__ import annotations

KIND_OPENAI_CHAT = "openai_chat"
KIND_ANTHROPIC = "anthropic"
KIND_MCP_SERVER = "mcp_server"
KIND_HTTP_WEBHOOK = "http_webhook"
KIND_KNOWLEDGE_SOURCE = "knowledge_source"
KIND_TOOL_PLUGIN = "tool_plugin"

#: kind 白名单——DB CHECK 约束、API 校验、manifest 校验共用这一份。
HUB_KINDS: tuple[str, ...] = (
    KIND_OPENAI_CHAT,
    KIND_ANTHROPIC,
    KIND_MCP_SERVER,
    KIND_HTTP_WEBHOOK,
    KIND_KNOWLEDGE_SOURCE,
    KIND_TOOL_PLUGIN,
)

GROUP_AI = "ai"
GROUP_KNOWLEDGE = "knowledge"
GROUP_TOOL = "tool"

#: kind -> HubPage 分组视图（增补 D：AI 服务 / 知识源 / 工具）。
KIND_GROUP: dict[str, str] = {
    KIND_OPENAI_CHAT: GROUP_AI,
    KIND_ANTHROPIC: GROUP_AI,
    KIND_MCP_SERVER: GROUP_TOOL,
    KIND_HTTP_WEBHOOK: GROUP_TOOL,
    KIND_KNOWLEDGE_SOURCE: GROUP_KNOWLEDGE,
    KIND_TOOL_PLUGIN: GROUP_TOOL,
}

GROUP_LABELS: dict[str, str] = {
    GROUP_AI: "AI 服务",
    GROUP_KNOWLEDGE: "知识源",
    GROUP_TOOL: "工具",
}

CONNECTION_STATES: tuple[str, ...] = (
    "active",        # 正常可用
    "disabled",      # 用户手动停用
    "needs_credentials",  # manifest 导入后还缺必填凭证
    "error",         # 最近一次探测失败
)

__all__ = [
    "CONNECTION_STATES",
    "GROUP_AI",
    "GROUP_KNOWLEDGE",
    "GROUP_LABELS",
    "GROUP_TOOL",
    "HUB_KINDS",
    "KIND_ANTHROPIC",
    "KIND_GROUP",
    "KIND_HTTP_WEBHOOK",
    "KIND_KNOWLEDGE_SOURCE",
    "KIND_MCP_SERVER",
    "KIND_OPENAI_CHAT",
    "KIND_TOOL_PLUGIN",
]
