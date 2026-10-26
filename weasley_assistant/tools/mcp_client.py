"""Validated, failure-isolated Model Context Protocol bridge.

Configuration lives at ``WEASLEY_ASSISTANT_HOME/mcp.json`` and all writes use
the public Weasley Assistant home exclusively.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import threading
import warnings
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from weasley_assistant.tools.registry import Tool

_SERVER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}


class MCPConfigError(ValueError):
    """Raised when ``mcp.json`` does not match the shipped JSON schema."""


def _schema_path() -> Path:
    return Path(__file__).with_name("mcp.schema.json")


def _fallback_validate(config: Any) -> None:
    if not isinstance(config, dict) or set(config) - {"servers"}:
        raise MCPConfigError("top level must be an object containing only 'servers'")
    servers = config.get("servers")
    if not isinstance(servers, list):
        raise MCPConfigError("'servers' must be an array")
    if len(servers) > 32:
        raise MCPConfigError("at most 32 MCP servers may be configured")

    seen: set[str] = set()
    for index, spec in enumerate(servers):
        prefix = f"servers[{index}]"
        if not isinstance(spec, dict):
            raise MCPConfigError(f"{prefix} must be an object")
        if set(spec) - {"name", "command", "args", "env"}:
            raise MCPConfigError(f"{prefix} contains an unknown property")
        name, command = spec.get("name"), spec.get("command")
        if not isinstance(name, str) or not _SERVER_NAME.fullmatch(name):
            raise MCPConfigError(f"{prefix}.name must match {_SERVER_NAME.pattern}")
        if name in seen:
            raise MCPConfigError(f"duplicate MCP server name: {name}")
        seen.add(name)
        if not isinstance(command, str) or not command.strip():
            raise MCPConfigError(f"{prefix}.command must be a non-empty string")
        args = spec.get("args", [])
        if not isinstance(args, list) or len(args) > 256 or not all(
            isinstance(value, str) for value in args
        ):
            raise MCPConfigError(f"{prefix}.args must be an array of strings")
        env = spec.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()
        ):
            raise MCPConfigError(f"{prefix}.env must map strings to strings")


def validate_mcp_config(config: Any) -> list[dict[str, Any]]:
    """Validate config against ``mcp.schema.json`` and return server specs."""
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:  # tests/core installs still get the equivalent guard
        _fallback_validate(config)
    else:
        try:
            jsonschema.validate(config, schema)
        except jsonschema.ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "root"
            raise MCPConfigError(f"invalid mcp.json at {location}: {exc.message}") from exc
        _fallback_validate(config)  # duplicate-name constraint is outside JSON Schema
    return config["servers"]


class MCPBridge:
    def __init__(self, config_path: Path, timeout: float = 30.0):
        self.config_path = config_path
        self.timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="weasley-assistant-mcp",
            daemon=True,
        )
        self._stop_event: asyncio.Event | None = None
        self._supervisor: concurrent.futures.Future | None = None
        self._server_stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, Any] = {}
        self._closed = False
        self._close_lock = threading.Lock()

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def _load_servers(self) -> list[dict[str, Any]]:
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            config = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MCPConfigError(f"cannot read {self.config_path}: {exc}") from exc
        return validate_mcp_config(config)

    def start(self) -> list[Tool]:
        """Connect valid servers; a failed server cannot poison its siblings."""
        if self._closed:
            raise RuntimeError("MCP bridge is closed")
        servers = self._load_servers()  # validate before any thread is started
        if not servers:
            return []

        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._thread.start()
        self._supervisor = asyncio.run_coroutine_threadsafe(
            self._supervise(servers, ready), self._loop
        )
        try:
            listed = ready.result(self.timeout * 2)
        except Exception:
            self.close()
            raise

        if not self._sessions:
            self.close()
            return []

        tools: list[Tool] = []
        for server, metas in listed.items():
            for meta in metas:
                input_schema = meta.get("inputSchema")
                if not isinstance(input_schema, dict):
                    input_schema = _DEFAULT_INPUT_SCHEMA
                tools.append(
                    Tool(
                        name=f"{server}_{meta['name']}",
                        description=f"[MCP:{server}] {meta.get('description', '') or ''}",
                        input_schema=input_schema,
                        fn=lambda server=server, tool=meta["name"], **kwargs: self.call(
                            server, tool, kwargs
                        ),
                    )
                )
        return tools

    async def _supervise(
        self,
        servers: list[dict[str, Any]],
        ready: concurrent.futures.Future,
    ) -> None:
        self._stop_event = asyncio.Event()
        try:
            listed = await self._connect_all(servers)
            ready.set_result(listed)
            await self._stop_event.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            for name, stack in reversed(list(self._server_stacks.items())):
                try:
                    await stack.aclose()
                except Exception as exc:
                    warnings.warn(f"MCP server '{name}' cleanup failed: {exc}", stacklevel=2)
            self._server_stacks.clear()
            self._sessions.clear()

    async def _connect_all(self, servers: list[dict[str, Any]]) -> dict[str, list[dict]]:
        listed: dict[str, list[dict]] = {}
        for spec in servers:
            name = spec["name"]
            stack = AsyncExitStack()
            try:
                metas, session = await self._connect_server(spec, stack)
            except Exception as exc:
                await stack.aclose()
                warnings.warn(f"MCP server '{name}' failed to connect: {exc}", stacklevel=2)
                continue
            self._server_stacks[name] = stack
            self._sessions[name] = session
            listed[name] = metas
        return listed

    async def _connect_server(
        self, spec: dict[str, Any], stack: AsyncExitStack
    ) -> tuple[list[dict], Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=spec["command"],
            args=spec.get("args", []),
            env=spec.get("env") or None,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        discovered = (await session.list_tools()).tools
        metas = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in discovered
        ]
        return metas, session

    def call(self, server: str, tool: str, args: dict) -> str:
        if self._closed or not self._thread.is_alive():
            return f"MCP call {server}_{tool} failed: bridge is closed"
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._acall(server, tool, args), self._loop
            )
            return future.result(self.timeout)
        except concurrent.futures.TimeoutError as exc:
            # result(timeout) only stops the synchronous wait. Without cancelling
            # its future, each timed-out request continues occupying the loop.
            # This requests local cancellation, not rollback of remote effects.
            future.cancel()
            detail = f" ({exc})" if str(exc) else ""
            return (f"MCP call {server}_{tool} timed out{detail}; local cancellation requested. "
                    "Remote completion is unknown; do not automatically retry.")
        except Exception as exc:
            return f"MCP call {server}_{tool} failed: {exc}"

    async def _acall(self, server: str, tool: str, args: dict) -> str:
        session = self._sessions.get(server)
        if session is None:
            return f"MCP server '{server}' is not connected."
        result = await session.call_tool(tool, args)
        parts = [getattr(block, "text", None) or "[non-text content]" for block in result.content]
        return "\n".join(parts) or "(no output)"

    def close(self) -> None:
        """Idempotently stop sessions, the event loop, and its daemon thread."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

            if self._thread.is_alive():
                if self._stop_event is not None:
                    self._loop.call_soon_threadsafe(self._stop_event.set)
                if self._supervisor is not None:
                    try:
                        self._supervisor.result(timeout=min(self.timeout, 10.0))
                    except Exception:
                        self._supervisor.cancel()
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            elif not self._loop.is_closed():
                self._loop.close()
