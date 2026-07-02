from __future__ import annotations

import pytest

from backend.config import MCPServerConfig
from backend.mcp.client import MCPManager


def test_manager_lists_configured_servers():
    mgr = MCPManager({"filesystem": MCPServerConfig(command="npx", args=["x"], transport="stdio")})
    assert mgr.configured() == ["filesystem"]


def test_manager_state_exists():
    mgr = MCPManager({"fs": MCPServerConfig(command="npx", transport="stdio")})
    st = mgr.state("fs")
    assert st is not None
    assert st.connected is False


def test_unknown_connect_raises():
    mgr = MCPManager({})
    with pytest.raises(Exception):
        import asyncio

        asyncio.run(mgr.connect("nope"))


def test_stdio_requires_command():
    mgr = MCPManager({"bad": MCPServerConfig(transport="stdio", command=None)})
    import asyncio

    ok = asyncio.run(mgr.connect("bad"))
    assert ok is False
    assert mgr.state("bad").error is not None


def test_tool_schemas_for_agent_empty_when_disconnected():
    mgr = MCPManager({"fs": MCPServerConfig(command="npx", transport="stdio")})
    assert mgr.tool_schemas_for_agent() == []


def test_tool_schemas_for_agent_with_mock_state():
    mgr = MCPManager({"fs": MCPServerConfig(command="npx", transport="stdio")})
    mgr._states["fs"].connected = True
    mgr._states["fs"].tools = [
        {"name": "search", "description": "search things", "inputSchema": {"type": "object"}}
    ]
    schemas = mgr.tool_schemas_for_agent()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "mcp_fs_search"
    assert "search things" in schemas[0]["function"]["description"]
