from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from ..config import MCPServerConfig, resolve_config

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client

    _HAS_MCP = True
except Exception:
    _HAS_MCP = False


class MCPError(RuntimeError):
    pass


@dataclass
class MCPServerState:
    name: str
    config: MCPServerConfig
    session: Any = None
    connected: bool = False
    error: str | None = None
    tools: list[dict] = field(default_factory=list)


class MCPManager:
    def __init__(self, servers: dict[str, MCPServerConfig] | None = None):
        if not _HAS_MCP:
            raise MCPError("mcp package not installed. Run: uv pip install 'mcp>=1.28,<2'")
        cfg = resolve_config()
        self.servers = servers if servers is not None else cfg.mcp_servers
        self._states: dict[str, MCPServerState] = {
            name: MCPServerState(name=name, config=sc) for name, sc in self.servers.items()
        }
        self._stack: contextlib.AsyncExitStack | None = None

    def configured(self) -> list[str]:
        return list(self.servers)

    def state(self, name: str) -> MCPServerState | None:
        return self._states.get(name)

    async def connect(self, name: str) -> bool:
        state = self._states.get(name)
        if state is None:
            raise MCPError(f"unknown MCP server: {name!r}")
        if state.connected:
            return True
        if self._stack is None:
            self._stack = contextlib.AsyncExitStack()

        cfg = state.config
        try:
            if cfg.transport == "stdio":
                if not cfg.command:
                    raise MCPError(f"server {name}: stdio transport requires 'command'")
                params = StdioServerParameters(
                    command=cfg.command,
                    args=tuple(cfg.args),
                    env={**cfg.env} if cfg.env else None,
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
            elif cfg.transport == "sse":
                if not cfg.url:
                    raise MCPError(f"server {name}: sse transport requires 'url'")
                read, write = await self._stack.enter_async_context(sse_client(cfg.url))
            elif cfg.transport == "http":
                if not cfg.url:
                    raise MCPError(f"server {name}: http transport requires 'url'")
                read, write, _ = await self._stack.enter_async_context(
                    streamablehttp_client(cfg.url)
                )
            else:
                raise MCPError(f"unknown transport: {cfg.transport}")

            session = ClientSession(read, write)
            await self._stack.enter_async_context(session)
            await session.initialize()
            state.session = session
            state.connected = True
            state.error = None
            try:
                tools_result = await session.list_tools()
                state.tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.inputSchema or {"type": "object"},
                    }
                    for t in tools_result.tools
                ]
            except Exception:
                state.tools = []
            return True
        except Exception as e:
            state.connected = False
            state.error = str(e)
            return False

    async def connect_all(self) -> dict[str, bool]:
        results = {}
        for name in list(self.servers):
            results[name] = await self.connect(name)
        return results

    async def list_tools(self, name: str) -> list[dict]:
        state = self._states.get(name)
        if not state or not state.connected:
            return []
        return list(state.tools)

    async def all_tools(self) -> dict[str, list[dict]]:
        return {n: await self.list_tools(n) for n in self.servers}

    async def call_tool(self, name: str, tool_name: str, arguments: dict) -> Any:
        state = self._states.get(name)
        if not state or not state.connected or state.session is None:
            raise MCPError(f"server {name!r} not connected")
        result = await state.session.call_tool(tool_name, arguments)
        return result

    def tool_schemas_for_agent(self) -> list[dict]:
        schemas = []
        for name, state in self._states.items():
            for t in state.tools:
                schema = {
                    "type": "function",
                    "function": {
                        "name": f"mcp_{name}_{t['name']}",
                        "description": f"[{name}] {t['description']}",
                        "parameters": t.get("inputSchema", {"type": "object"}),
                    },
                }
                schemas.append(schema)
        return schemas

    async def close_all(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except RuntimeError:
                pass
            self._stack = None
        for state in self._states.values():
            state.session = None
            state.connected = False


def create_manager() -> MCPManager:
    return MCPManager()
