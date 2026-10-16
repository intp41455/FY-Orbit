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
market_source      市场远程源（HTTP 索引 / Git 仓库 / 本地目录）       工具
=================  ==============================================  ==========

诚实原则：本包不提供任何「假成功」。探测不到的连接就是 ``ok=False``，
拿不到的能力就是抛 :class:`find_yourself.services.errors.DomainError`，
绝不返回构造出来的占位数据。
"""

from __future__ import annotations

# N7 R5-2：常量本体下沉 L1（db/hub_constants.py），此处重导出——
# ``from services.hub import HUB_KINDS`` 全网零改动；本包「只放轻量常量、
# 不 import 任何子模块」的防环设计不受影响（db 是 L1 底座，非子模块）。
from ...db.hub_constants import (  # noqa: F401 (re-export)
    CONNECTION_STATES,
    DEFAULT_MARKET_SOURCE_TYPE,
    GROUP_AI,
    GROUP_KNOWLEDGE,
    GROUP_LABELS,
    GROUP_TOOL,
    HUB_KINDS,
    KIND_ANTHROPIC,
    KIND_GROUP,
    KIND_HTTP_WEBHOOK,
    KIND_KNOWLEDGE_SOURCE,
    KIND_MARKET_SOURCE,
    KIND_MCP_SERVER,
    KIND_OPENAI_CHAT,
    KIND_TOOL_PLUGIN,
    MARKET_SOURCE_TYPES,
)

__all__ = [
    "CONNECTION_STATES",
    "DEFAULT_MARKET_SOURCE_TYPE",
    "GROUP_AI",
    "GROUP_KNOWLEDGE",
    "GROUP_LABELS",
    "GROUP_TOOL",
    "HUB_KINDS",
    "KIND_ANTHROPIC",
    "KIND_GROUP",
    "KIND_HTTP_WEBHOOK",
    "KIND_KNOWLEDGE_SOURCE",
    "KIND_MARKET_SOURCE",
    "KIND_MCP_SERVER",
    "KIND_OPENAI_CHAT",
    "KIND_TOOL_PLUGIN",
    "MARKET_SOURCE_TYPES",
]
