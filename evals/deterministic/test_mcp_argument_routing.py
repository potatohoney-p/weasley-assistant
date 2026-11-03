"""MCP tool arguments are payload, never bridge routing parameters."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from weasley_assistant.tools.mcp_client import MCPBridge
from weasley_assistant.tools.registry import ToolRegistry


class Session:
    def __init__(self, name):
        self.name = name
        self.calls = []

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        return SimpleNamespace(content=[SimpleNamespace(text=f"{self.name}/{tool}")])


class Bridge(MCPBridge):
    def __init__(self, path):
        super().__init__(path, timeout=2)
        self.test_sessions = {name: Session(name) for name in ("first", "second")}

    async def _connect_server(self, spec, stack):
        metas = [
            {"name": name, "description": name, "inputSchema": {"type": "object"}}
            for name in ("echo", "other")
        ]
        return metas, self.test_sessions[spec["name"]]


@pytest.fixture
def routed_tools(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [
        {"name": name, "command": "unused"} for name in ("first", "second")
    ]}), encoding="utf-8")
    bridge = Bridge(path)
    registry = ToolRegistry()
    tools = bridge.start()
    for tool in tools:
        registry.register(tool)
    try:
        yield bridge, registry, tools
    finally:
        bridge.close()
        assert not bridge._thread.is_alive()
        assert bridge._loop.is_closed()


@pytest.mark.parametrize("args", [
    {},
    {"message": "hello"},
    {"server": "second"},
    {"tool": "other"},
    {"server": "missing"},
    {"server": {"host": "example"}, "tool": ["literal", "values"]},
    {"server": "second", "tool": "other", "kwargs": "payload", "self": "payload",
     "args": {"tool": "nested"}},
])
def test_argument_names_cannot_replace_bound_route(routed_tools, args):
    bridge, registry, _ = routed_tools

    assert registry.execute("first_echo", args) == "first/echo"
    assert bridge.test_sessions["first"].calls == [("echo", args)]
    assert bridge.test_sessions["second"].calls == []


def test_each_discovered_callable_keeps_its_own_route(routed_tools):
    bridge, _, tools = routed_tools
    # Call in reverse order and then repeat: no closure may retain the final
    # discovery-loop iteration, even when payload names shadow route names.
    for tool in [*reversed(tools), tools[0]]:
        server, name = tool.name.split("_", 1)
        args = {"server": "payload server", "tool": "payload tool"}
        assert tool.fn(**args) == f"{server}/{name}"
        assert bridge.test_sessions[server].calls[-1] == (name, args)
    assert len(bridge.test_sessions["first"].calls) == 3
    assert len(bridge.test_sessions["second"].calls) == 2


def test_registered_callable_still_honors_closed_bridge(routed_tools):
    bridge, registry, _ = routed_tools
    bridge.close()

    assert "bridge is closed" in registry.execute("first_echo", {"server": "second"})
    assert all(not session.calls for session in bridge.test_sessions.values())
