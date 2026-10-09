"""动态 MCP Server 管理（P0-1）。

运行时注册/更新/移除 MCP Server，并将其工具即时注册到 tool_registry。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from ..adapters.mcp import McpClient
from ..config import settings
from .tool_registry import ToolRegistryService, tool_registry


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
        from .mcp import TRUST_REMOTE, TRUST_TRUSTED, TRUST_UNTRUSTED, McpTrustPolicy, ReconnectPolicy

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
            except Exception:
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
