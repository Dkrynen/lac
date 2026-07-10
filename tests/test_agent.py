from __future__ import annotations

import json

from backend.agent import AgentRunner, get_agent, list_agents
from backend.agent.permissions import FULL_PERMISSIONS, READONLY_PERMISSIONS, Permissions
from backend.plugin.builtins.tools import TOOL_HANDLERS, WRITE_TOOLS
from backend.provider.base import ChatDelta


def test_permissions_from_dict_roundtrip():
    d = FULL_PERMISSIONS.to_dict()
    p = Permissions.from_dict(d)
    assert p.can_write() and p.can_delete() and p.can_run_bash()
    assert p.can_fetch() and p.can_post() and p.can_mcp()


def test_readonly_denies_write():
    p = READONLY_PERMISSIONS
    assert not p.can_write()
    assert not p.can_run_bash()
    assert p.can_read()
    assert not p.allows_tool("write_file", WRITE_TOOLS, set(), set())


def test_full_allows_write():
    assert FULL_PERMISSIONS.allows_tool("write_file", WRITE_TOOLS, set(), set())


def test_list_agents_has_three():
    agents = list_agents()
    names = {a.name for a in agents}
    assert {"build", "plan", "explore"}.issubset(names)


def test_agent_permission_tiers():
    build = get_agent("build")
    plan = get_agent("plan")
    assert build.permissions.can_write()
    assert not plan.permissions.can_write()
    assert build.permissions.can_run_bash()
    assert not plan.permissions.can_run_bash()


def test_runner_no_tool_call(mock_provider, tool_registry):
    agent = get_agent("build")
    agent.model = "mock:1b"
    mock_provider.set_script([ChatDelta(content="hello there", done=True)])
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    async def go():
        return await runner.run("hi")

    import asyncio

    result = asyncio.run(go())
    assert result.error is None
    assert "hello there" in result.content
    assert result.messages[-1]["role"] == "assistant"


def test_runner_executes_tool_call(mock_provider, tool_registry):
    agent = get_agent("build")
    agent.model = "mock:1b"
    call = {"function": {"name": "list_files", "arguments": json.dumps({"path": "."})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="done listing", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("list files")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is True
    assert ".py" in tool_results[0]["result"] or "backend" in tool_results[0]["result"]


def test_runner_denies_tool_for_readonly_agent(mock_provider, tool_registry):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "write_file", "arguments": json.dumps({"path": "x", "content": "y"})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="ok", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("write a file")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is False
    assert "permission denied" in tool_results[0]["result"]


def test_runner_denies_tool_not_enabled_for_agent(mock_provider, tool_registry):
    agent = get_agent("plan")
    agent.model = "mock:1b"
    call = {"function": {"name": "web_search", "arguments": json.dumps({"query": "lac"})}}
    mock_provider.set_script(
        [
            ChatDelta(content="", tool_calls=[call], done=True),
            ChatDelta(content="ok", done=True),
        ]
    )
    runner = AgentRunner(
        mock_provider, agent, tool_registry["handlers"], tool_registry["schemas"]
    )

    import asyncio

    async def go():
        return await runner.run("search the web")

    result = asyncio.run(go())
    tool_results = [e for e in result.events if e["type"] == "tool_result"]
    assert tool_results
    assert tool_results[0]["ok"] is False
    assert "not enabled" in tool_results[0]["result"]
