from __future__ import annotations

import json

import pytest

from weasley_assistant.tools.mcp_client import MCPBridge, MCPConfigError, validate_mcp_config


class _Session:
    async def call_tool(self, _tool, _args):
        return type("Result", (), {"content": [type("Block", (), {"text": "ok"})()]})()


class _Bridge(MCPBridge):
    async def _connect_server(self, spec, stack):
        del stack
        if spec["name"] == "broken":
            raise RuntimeError("isolated failure")
        return ([{"name": "ping", "description": "test", "inputSchema": {"type": "object"}}], _Session())


def _write(path, servers):
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")


def test_invalid_config_fails_before_starting_a_thread(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"servers":[{"name":"bad name","command":"x"}]}', encoding="utf-8")
    bridge = MCPBridge(path)

    with pytest.raises(MCPConfigError):
        bridge.start()
    assert not bridge._thread.is_alive()
    bridge.close()


def test_schema_rejects_duplicate_names_and_unknown_fields():
    with pytest.raises(MCPConfigError):
        validate_mcp_config(
            {"servers": [{"name": "one", "command": "x"}, {"name": "one", "command": "y"}]}
        )
    with pytest.raises(MCPConfigError):
        validate_mcp_config({"servers": [], "unsafe": True})


def test_server_failure_is_isolated_and_close_joins_thread(tmp_path):
    path = tmp_path / "mcp.json"
    _write(
        path,
        [
            {"name": "broken", "command": "ignored"},
            {"name": "healthy", "command": "ignored"},
        ],
    )
    bridge = _Bridge(path, timeout=1)

    with pytest.warns(UserWarning, match="broken"):
        tools = bridge.start()
    assert [tool.name for tool in tools] == ["healthy_ping"]
    assert bridge.call("healthy", "ping", {}) == "ok"
    assert bridge._thread.is_alive()

    bridge.close()
    bridge.close()
    assert not bridge._thread.is_alive()

