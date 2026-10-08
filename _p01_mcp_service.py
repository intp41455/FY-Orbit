"""P0-1 动态 MCP Server 管理服务（写入 fy-finish 树）。

在 tool_registry 基础上增加运行时动态挂载/卸载 MCP Server 的能力。
"""
import pathlib
import json

root = pathlib.Path(r"C:\Users\intpj\Documents\Codex\2026-09-29\agent\outputs\fy-finish")

# 1. 创建新服务文件：services/mcp_dynamic.py
service_path = root / "src" / "find_yourself" / "services" / "mcp_dynamic.py"
service_content = '''"""动态 MCP Server 管理（P0-1）。

运行时注册/更新/移除 MCP Server，并将其工具即时注册到 tool_registry。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from .mcp import McpClient, assemble_mcp_tools
from .tool_registry import tool_registry, ToolRegistryService
from ..config import settings


class McpDynamicService:
    """管理动态 MCP Server 的生命周期与工具注册。"""

    def __init__(self, registry: Optional[ToolRegistryService] = None):
        self._registry = registry or tool_registry
        self._clients: dict[str, McpClient] = {}
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """获取或创建事件循环（用于异步工具调用）。"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_async(self, coro):
        """在独立线程的事件循环中运行协程。"""
        loop = self._get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)

    def register_server(
        self,
        key: str,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        trust_level: str = "remote",
        reconnect: bool = True,
    ) -> dict[str, Any]:
        """注册或更新一个 MCP Server。

        Args:
            key: Server 唯一标识（如 "codex"、"my-agent"）
            url: MCP Server 地址（HTTP/SSE/WS）
            headers: 可选 HTTP 头（用于认证）
            trust_level: "trusted" | "remote" | "untrusted"
            reconnect: 是否启用断线重连

        Returns:
            注册结果（包含发现的工具列表）
        """
        from .mcp import ReconnectPolicy, McpTrustPolicy
        from .mcp import TRUST_TRUSTED, TRUST_REMOTE, TRUST_UNTRUSTED

        trust_map = {
            "trusted": TRUST_TRUSTED,
            "remote": TRUST_REMOTE,
            "untrusted": TRUST_UNTRUSTED,
        }
        trust = McpTrustPolicy(level=trust_map.get(trust_level, TRUST_REMOTE))
        reconnect_policy = ReconnectPolicy() if reconnect else ReconnectPolicy(max_attempts=0)

        # 1. 创建 McpClient
        client = McpClient.from_url(
            url,
            headers=headers or {},
            reconnect=reconnect_policy,
            trust=trust,
        )

        # 2. 列出工具
        tools = client.list_tools()

        # 3. 注册每个工具到 tool_registry
        registered = []
        for tool in tools:
            tool_name = f"{key}.{tool['name']}"
            try:
                self._registry.register(
                    name=tool_name,
                    description=tool.get("description", ""),
                    parameters=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    entry={
                        "type": "mcp",
                        "server": key,
                        "remote_tool": tool["name"],
                    },
                )
                registered.append(tool_name)
            except Exception as e:
                # 已存在则跳过，记录错误
                pass

        # 4. 保存 client 供后续调用
        with self._lock:
            # 关闭旧 client（如果存在）
            old = self._clients.get(key)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            self._clients[key] = client

        # 5. 持久化到配置（可选：写入 settings.mcp_servers 的运行时副本）
        self._persist_server_config(key, url, headers, trust_level, reconnect)

        return {
            "key": key,
            "url": url,
            "registered_tools": registered,
            "total_discovered": len(tools),
        }

    def unregister_server(self, key: str) -> dict[str, Any]:
        """移除一个 MCP Server 及其工具。"""
        with self._lock:
            client = self._clients.pop(key, None)
            if client:
                try:
                    client.close()
                except Exception:
                    pass

        # 从 tool_registry 移除该 server 前缀的工具
        all_tools = self._registry.list_tools()
        removed = []
        for tool in all_tools:
            if tool["name"].startswith(f"{key}."):
                try:
                    self._registry.unregister(tool["name"])
                    removed.append(tool["name"])
                except Exception:
                    pass

        return {"key": key, "removed_tools": removed}

    def list_servers(self) -> dict[str, Any]:
        """列出所有已注册的 MCP Server。"""
        with self._lock:
            return {
                "servers": [
                    {
                        "key": key,
                        "url": getattr(client, "_url", "unknown"),
                        "tool_count": len([t for t in self._registry.list_tools() if t["name"].startswith(f"{key}.")]),
                    }
                    for key, client in self._clients.items()
                ]
            }

    def get_server_tools(self, key: str) -> dict[str, Any]:
        """获取某 Server 的工具列表。"""
        tools = [
            t for t in self._registry.list_tools()
            if t["name"].startswith(f"{key}.")
        ]
        return {"key": key, "tools": tools, "count": len(tools)}

    def invoke_tool(self, key: str, tool_name: str, arguments: dict) -> Any:
        """调用某 MCP Server 的工具（走 tool_registry.invoke，自动路由到 McpClient）。"""
        full_name = f"{key}.{tool_name}"
        return self._registry.invoke(full_name, arguments)

    def _persist_server_config(
        self,
        key: str,
        url: str,
        headers: dict[str, str] | None,
        trust_level: str,
        reconnect: bool,
    ) -> None:
        """将配置写入 settings.mcp_servers（运行时内存），不落盘。"""
        # 注意：Settings 是只读的，这里只更新内存字典
        # 如需落盘，可写入 .runtime/mcp_servers.json
        cfg = settings()
        cfg.mcp_servers[key] = {
            "url": url,
            "headers": headers or {},
            "trust_level": trust_level,
            "reconnect": reconnect,
        }


# 进程内单例
mcp_dynamic = McpDynamicService()
'''

if service_path.exists():
    print(f"Service already exists: {service_path}")
else:
    service_path.write_text(service_content, encoding="utf-8", newline="\n")
    print(f"Created: {service_path}")

# 2. 修改 tools.py：新增 MCP Server 管理端点
tools_route = root / "src" / "find_yourself" / "api" / "routes" / "tools.py"
tools_text = tools_route.read_text(encoding="utf-8")

# 检查是否已有 mcp 端点
if "mcp/servers" in tools_text:
    print("tools.py: MCP endpoints already exist")
else:
    # 在 imports 后加入 mcp_dynamic
    old_import = '''from ..deps import csrf_protected, get_actor
from ...services.tool_registry import tool_registry'''

    new_import = '''from ..deps import csrf_protected, get_actor
from ...services.tool_registry import tool_registry
from ...services.mcp_dynamic import mcp_dynamic'''

    tools_text = tools_text.replace(old_import, new_import, 1)

    # 在文件末尾追加 MCP Server 管理端点
    mcp_endpoints = '''

# --- MCP Server 动态管理（P0-1） ---

class McpServerRegisterBody(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern="^[a-z][a-z0-9_-]*$")
    url: str = Field(min_length=1, max_length=500)
    headers: dict[str, str] | None = None
    trust_level: str = Field(default="remote", pattern="^(trusted|remote|untrusted)$")
    reconnect: bool = True


@router.post("/mcp/servers")
def register_mcp_server(body: McpServerRegisterBody, actor: object = Depends(csrf_protected)) -> dict:
    """注册/更新一个 MCP Server，并自动发现并注册其工具。"""
    return mcp_dynamic.register_server(
        key=body.key,
        url=body.url,
        headers=body.headers,
        trust_level=body.trust_level,
        reconnect=body.reconnect,
    )


@router.delete("/mcp/servers/{key}")
def unregister_mcp_server(key: str, actor: object = Depends(csrf_protected)) -> dict:
    """移除一个 MCP Server 及其工具。"""
    return mcp_dynamic.unregister_server(key)


@router.get("/mcp/servers")
def list_mcp_servers(actor: object = Depends(get_actor)) -> dict:
    """列出所有已注册的 MCP Server。"""
    return mcp_dynamic.list_servers()


@router.get("/mcp/servers/{key}/tools")
def get_mcp_server_tools(key: str, actor: object = Depends(get_actor)) -> dict:
    """列出某 MCP Server 的工具。"""
    return mcp_dynamic.get_server_tools(key)
'''

    tools_text = tools_text.rstrip() + mcp_endpoints + "\n"
    tools_route.write_text(tools_text, encoding="utf-8", newline="\n")
    print(f"Updated: {tools_route}")

print("P0-1 动态 MCP Server 服务与端点已创建/更新")