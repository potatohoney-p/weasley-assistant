"""Real local assistant trials release resources without losing their receipts."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.helpers import ScriptedClient, response, text_block
from weasley_assistant import app as app_mod
from weasley_assistant.ops import arena

spec = importlib.util.spec_from_file_location(
    "shootout_cleanup", Path(__file__).resolve().parents[2] / "scripts" / "shootout.py")
shootout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shootout)

CASE = {"id": "cleanup", "input": "Reply briefly", "expect_tool": None,
        "setup_fact": {"subject": "Alex", "content": "likes tea"}}


@pytest.fixture
def tracked(monkeypatch, tmp_path):
    """Keep actual SQLite + loop behavior, replace only model calls and export."""
    for key in list(__import__("os").environ):
        if key.startswith("WEASLEY_ASSISTANT_") or key == "OTEL_EXPORTER_OTLP_ENDPOINT":
            monkeypatch.delenv(key)
    monkeypatch.setenv("WEASLEY_ASSISTANT_HOME", str(tmp_path / "saved"))
    homes, connections, clients, order = [], [], [], []
    connect = app_mod.connect
    close = app_mod.WeasleyAssistant.close

    class Connection:
        def __init__(self, conn, home):
            self.conn, self.home, self.closed = conn, home, False

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def close(self):
            assert self.home.exists()
            order.append((self.home, "database"))
            self.conn.close()
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.conn.execute("SELECT 1")
            self.closed = True

    def tracking_connect(home, **kwargs):
        homes.append(home)
        conn = Connection(connect(home, **kwargs), home)
        connections.append(conn)
        return conn

    class Client(ScriptedClient):
        def __init__(self, settings):
            final = response([text_block("Done")])
            final.usage.input_tokens, final.usage.output_tokens = 123, 45
            super().__init__([
                response([text_block('{"retrieve": false, "reason": "simple"}')]), final,
            ])
            self.home, self.closed = settings.home, False

        def close(self):
            assert self.home.exists()
            order.append((self.home, "client"))
            self.closed = True

    def client_for(settings):
        settings.model = settings.model or "resolved-model"
        client = Client(settings)
        clients.append(client)
        return client

    def close_app(self):
        assert self.settings.home.exists()
        assert self.conn.execute("SELECT 1").fetchone()[0] == 1
        order.append((self.settings.home, "assistant"))
        close(self)

    monkeypatch.setattr(app_mod, "connect", tracking_connect)
    monkeypatch.setattr(app_mod, "get_client", client_for)
    monkeypatch.setattr(app_mod.WeasleyAssistant, "close", close_app)
    monkeypatch.setattr(arena.scoring, "case_for_message", lambda message: CASE)
    return SimpleNamespace(homes=homes, connections=connections, clients=clients, order=order,
                           saved=tmp_path / "saved")


def assert_released(tracked):
    assert tracked.homes
    assert all(not home.exists() for home in tracked.homes)
    assert all(client.closed for client in tracked.clients)
    assert all(conn.closed for conn in tracked.connections)


def run(runner, emit=None, *, trials=1):
    if runner == "shootout":
        return shootout.run_one("anthropic", "", [CASE], trials=trials)
    events = []
    arena.compare_stream(CASE["input"], ["anthropic:"] * trials,
                         emit or (lambda kind, data: events.append((kind, data))))
    return events


@pytest.mark.parametrize("runner", ["shootout", "arena"])
def test_success_preserves_results_and_closes_before_removing_home(runner, tracked):
    result = run(runner, trials=3)
    assert_released(tracked)
    assert len(set(tracked.homes)) == 3
    for home in tracked.homes:
        assert [step for h, step in tracked.order if h == home] == [
            "assistant", "client", "database"]
    if runner == "shootout":
        row = result["cases"][0]
        assert (row["hits"], row["tokens_in"], row["tokens_out"]) == (3, 369, 135)
        assert result["model"] == "resolved-model"
    else:
        rows = [data for kind, data in result if kind == "result"]
        assert len(rows) == 3
        assert all(row["reply"] == "Done" and row["completion"]["passed"] for row in rows)
        assert all((row["tokens_in"], row["tokens_out"]) == (123, 45) for row in rows)
        assert (tracked.saved / "compare" / "history.jsonl").exists()


@pytest.mark.parametrize("runner", ["shootout", "arena"])
@pytest.mark.parametrize("phase", ["respond", "seed", "constructor", "ledger", "scoring", "client"])
def test_failure_paths_release_trial_resources(runner, phase, tracked, monkeypatch):
    error = RuntimeError(f"{phase} failed")

    def fail(*args, **kwargs):
        raise error

    if phase == "respond":
        monkeypatch.setattr(app_mod.WeasleyAssistant, "respond", fail)
    elif phase == "seed":
        from weasley_assistant.memory.semantic.store import SqliteFactStore
        monkeypatch.setattr(SqliteFactStore, "add", fail)
    elif phase == "constructor":
        from weasley_assistant.memory import Memory
        monkeypatch.setattr(Memory, "__init__", fail)
    elif phase == "client":
        monkeypatch.setattr(app_mod, "get_client", fail)
    elif phase == "scoring":
        monkeypatch.setattr(shootout if runner == "shootout" else arena.scoring, "check_case", fail)
    else:
        read_text = Path.read_text

        def broken_ledger(self, *args, **kwargs):
            if self.name == "usage.jsonl":
                raise error
            return read_text(self, *args, **kwargs)
        monkeypatch.setattr(Path, "read_text", broken_ledger)

    if runner == "shootout" and phase not in {"respond", "scoring"}:
        with pytest.raises(RuntimeError) as caught:
            run(runner)
        assert caught.value is error
    else:
        result = run(runner)
        if runner == "shootout":
            assert result["passed"] == 0 and result["cases"][0]["why"] == f"error: {error}"
        else:
            rows = [data for kind, data in result if kind == "result"]
            assert len(rows) == 1 and rows[0]["error"] == str(error)
    assert_released(tracked)


@pytest.mark.parametrize("runner", ["shootout", "arena"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_base_exceptions_do_not_leave_trial_resources(runner, error_type, tracked, monkeypatch):
    error = error_type("interrupted")

    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(app_mod.WeasleyAssistant, "respond", fail)
    if runner == "arena" and error_type is SystemExit:
        result = run(runner)
        assert next(data for kind, data in result if kind == "result")["error"] == "interrupted"
    else:
        with pytest.raises(error_type) as caught:
            run(runner)
        assert caught.value is error
    assert_released(tracked)


def test_arena_observer_failure_releases_resources(tracked):
    events = []

    def emit(kind, data):
        if kind == "gate":
            raise OSError("observer disconnected")
        events.append((kind, data))
    run("arena", emit)
    assert next(data for kind, data in events if kind == "result")["error"] == "observer disconnected"
    assert_released(tracked)


@pytest.mark.parametrize("runner", ["shootout", "arena"])
def test_cleanup_failure_is_visible_and_later_resources_still_close(runner, tracked, monkeypatch):
    def fail(self):
        raise RuntimeError("close failed")
    monkeypatch.setattr(app_mod.WeasleyAssistant, "close", fail)
    if runner == "shootout":
        with pytest.raises(RuntimeError, match="close failed"):
            run(runner)
    else:
        result = run(runner)
        rows = [data for kind, data in result if kind == "result"]
        assert len(rows) == 1 and rows[0]["error"] == "close failed"
    assert_released(tracked)


def test_cleanup_failure_preserves_original_base_exception(tracked, monkeypatch):
    error = KeyboardInterrupt("original")

    def fail_response(*args, **kwargs):
        raise error

    def fail_close(self):
        raise OSError("cleanup")
    monkeypatch.setattr(app_mod.WeasleyAssistant, "respond", fail_response)
    monkeypatch.setattr(app_mod.WeasleyAssistant, "close", fail_close)
    with pytest.raises(KeyboardInterrupt) as caught:
        run("shootout")
    assert caught.value is error
    assert error.__notes__ == ["Benchmark cleanup also failed: OSError"]
    assert_released(tracked)


def test_openai_compat_close_forwards_to_sdk():
    from weasley_assistant.loop.models import OpenAICompatClient
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    calls = []
    client._client = SimpleNamespace(close=lambda: calls.append("closed"))
    client.close()
    assert calls == ["closed"]


@pytest.mark.parametrize("runner", ["shootout", "arena"])
def test_missing_provider_key_closes_database(runner, tracked, monkeypatch):
    from weasley_assistant.loop.models import get_client
    monkeypatch.setattr(app_mod, "get_client", get_client)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if runner == "shootout":
        with pytest.raises(SystemExit, match="No API key"):
            run(runner)
    else:
        events = run(runner)
        row = next(data for kind, data in events if kind == "result")
        assert "No API key" in row["error"]
    assert_released(tracked)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_real_sdk_client_closes_without_a_network_call(provider, tmp_path, monkeypatch):
    import socket

    from weasley_assistant.config import Settings
    from weasley_assistant.ops.benchmark_trial import benchmark_trial

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    settings = Settings(provider=provider, api_key="offline", home=tmp_path,
                        semantic_store="sqlite", episodic_store="sqlite",
                        otel_endpoint="", experimental=False, apple_tools=False,
                        apple_calendar=False, google_calendar=False, gh_tool=False)
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with benchmark_trial(settings, prefix="sdk-cleanup-") as app:
        sdk = app.client if provider == "anthropic" else app.client._client
        assert not sdk.is_closed()
        home = settings.home
    assert sdk.is_closed()
    assert not home.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"
