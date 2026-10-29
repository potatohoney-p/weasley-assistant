"""Real bridge-thread timeout lifecycle, with synthetic async MCP sessions."""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from weasley_assistant.tools.mcp_client import MCPBridge


class Session:
    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.active = 0
        self.completed = 0

    async def call_tool(self, tool, args):
        if tool == 'ping':
            return SimpleNamespace(content=[SimpleNamespace(text='pong')])
        if tool == 'server_timeout':
            raise TimeoutError('server deadline detail')
        if tool == 'error':
            raise RuntimeError('fixture failure')
        self.active += 1
        self.started.set()
        try:
            await asyncio.sleep(3600)
            self.completed += 1
            return SimpleNamespace(content=[])
        except asyncio.CancelledError:
            self.cancelled.set()
            if args.get('ignore_cancel'):
                self.completed += 1
                return SimpleNamespace(content=[SimpleNamespace(text='late completion')])
            raise
        finally:
            self.active -= 1
            self.finished.set()


class Bridge(MCPBridge):
    def __init__(self, path, session):
        super().__init__(path, timeout=2)
        self.session = session

    async def _connect_server(self, spec, stack):
        return ([{'name': 'wait', 'inputSchema': {'type': 'object'}}], self.session)


@pytest.fixture
def bridge(tmp_path):
    path = tmp_path / 'mcp.json'
    path.write_text(json.dumps({'servers': [{'name': 'fixture', 'command': 'unused'}]}))
    instance = Bridge(path, Session())
    instance.start()
    instance.timeout = 0.05
    try:
        yield instance
    finally:
        instance.close()
        assert not instance._thread.is_alive()
        assert instance._loop.is_closed()


def test_timeout_cancels_local_request_and_retains_uncertainty(bridge):
    result = bridge.call('fixture', 'wait', {})
    assert bridge.session.started.is_set()
    assert bridge.session.finished.wait(1), 'timed-out coroutine remained active'
    assert bridge.session.cancelled.is_set()
    assert bridge.session.active == 0
    assert bridge.session.completed == 0
    assert 'timed out' in result.lower()
    assert 'unknown' in result.lower()
    assert 'retry' in result.lower()
    assert bridge.call('fixture', 'ping', {}) == 'pong'


def test_repeated_timeouts_do_not_accumulate_local_tasks(bridge):
    for _ in range(3):
        bridge.session.finished.clear()
        bridge.session.cancelled.clear()
        bridge.call('fixture', 'wait', {})
        assert bridge.session.finished.wait(1)
        assert bridge.session.cancelled.is_set()
        assert bridge.session.active == 0
    assert bridge.session.completed == 0
    assert bridge.call('fixture', 'ping', {}) == 'pong'


def test_timeout_does_not_cancel_a_concurrent_healthy_call(bridge):
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(bridge.call, 'fixture', 'wait', {})
        assert bridge.session.started.wait(1)
        assert bridge.call('fixture', 'ping', {}) == 'pong'
        assert 'timed out' in waiting.result(timeout=2).lower()
    assert bridge.session.finished.wait(1)


def test_tool_failure_and_missing_server_keep_existing_diagnostics(bridge):
    assert 'fixture failure' in bridge.call('fixture', 'error', {})
    assert 'not connected' in bridge.call('absent', 'ping', {})
    assert not bridge.session.cancelled.is_set()
    assert bridge.call('fixture', 'ping', {}) == 'pong'


def test_closed_bridge_rejects_calls_without_starting_work(bridge):
    bridge.close()
    assert 'closed' in bridge.call('fixture', 'wait', {})
    assert not bridge.session.started.is_set()


def test_cancellation_is_not_a_remote_rollback_guarantee(bridge):
    result = bridge.call('fixture', 'wait', {'ignore_cancel': True})
    assert bridge.session.finished.wait(1)
    assert bridge.session.cancelled.is_set()
    assert bridge.session.completed == 1
    assert 'unknown' in result.lower()
    assert 'cancellation requested' in result.lower()
    assert 'late completion' not in result
    assert bridge.call('fixture', 'ping', {}) == 'pong'


def test_server_timeout_retains_diagnostic_detail(bridge):
    result = bridge.call('fixture', 'server_timeout', {})
    assert 'server deadline detail' in result
    assert bridge.call('fixture', 'ping', {}) == 'pong'
