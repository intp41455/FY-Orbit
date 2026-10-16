"""Hub kind/state vocabulary — L1 single source (N7 R5-2 解环).

``HUB_KINDS`` / ``CONNECTION_STATES`` 原先住在 ``services/hub/__init__.py``
（L2.1），``db/workbench_models.py``（L1）为 DB CHECK 约束反向导入 L2，
构成 R5 违规。下沉到 L1 后：

* ``db/workbench_models.py`` 从本模块取（L1 内部边）；
* ``services/hub/__init__.py`` 从本模块重导出（L2→L1 合法方向，
  ``from services.hub import HUB_KINDS`` 全网零改动；该包「只放轻量常量、
  不 import 子模块」的防环设计不受影响）；
* DB CHECK 与 hub 校验仍是同一份元组，不会漂移。
"""

from __future__ import annotations

KIND_OPENAI_CHAT = "openai_chat"
KIND_ANTHROPIC = "anthropic"
KIND_MCP_SERVER = "mcp_server"
KIND_HTTP_WEBHOOK = "http_webhook"
KIND_KNOWLEDGE_SOURCE = "knowledge_source"
KIND_TOOL_PLUGIN = "tool_plugin"
#: 市场远程源（I-12）：用户登记的外部包来源（URL/Git/本地目录）。
#: 只做“运输通道”——包仍须过本地签名+扫描门禁才能上架。
KIND_MARKET_SOURCE = "market_source"

#: market_source 的**来源形态**（显式字段 ``endpoint_config.source_type``），不是第 8 个 kind。
#:
#: 取舍（为什么不新开 ``git_source`` kind）：kind 回答“这个连接是什么对象”，
#: source_type 回答“用哪条通道把包搬过来”。同一个市场源走 git clone 还是走
#: HTTP GET，对上层（市场陈列 / 签名+扫描门禁 / 安装授权）完全同构；单开 kind 会
#: 让用户在 Hub 里看到两种同物异名的市场源，还要连带改 DB CHECK 约束、kind 分组、
#: 密钥字段表，以及导入路由的 kind 校验——换来的只是「多一个可枚举值」。
#: 显式字段把差异压在适配器内部：旧连接没有该字段 → 落回 http，行为逐字不变。
MARKET_SOURCE_TYPES: tuple[str, ...] = ("http", "git")
DEFAULT_MARKET_SOURCE_TYPE = "http"

#: kind 白名单——DB CHECK 约束、API 校验、manifest 校验共用这一份。
HUB_KINDS: tuple[str, ...] = (
    KIND_OPENAI_CHAT,
    KIND_ANTHROPIC,
    KIND_MCP_SERVER,
    KIND_HTTP_WEBHOOK,
    KIND_KNOWLEDGE_SOURCE,
    KIND_TOOL_PLUGIN,
    KIND_MARKET_SOURCE,
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
    KIND_MARKET_SOURCE: GROUP_TOOL,
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
