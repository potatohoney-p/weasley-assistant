"""Benchmark settings must not inherit the chat agent's personal integrations."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.deterministic.test_shootout import shootout
from evals.helpers import ScriptedClient, response, text_block, tool_block
from weasley_assistant import app as app_module
from weasley_assistant.config import Settings
from weasley_assistant.ops import arena

CASE = {
    "id": "isolated-calendar",
    "input": "Book an isolated benchmark event for Alex",
    "setup_fact": {"subject": "alex", "content": "Alex prefers mornings"},
    "expect_tool": "create_event",
}

AMBIENT = {
    "SEMANTIC_STORE": "supabase",
    "EPISODIC_STORE": "notion",
    "APPLE_CALENDAR": "1",
    "GOOGLE_CALENDAR": "1",
    "APPLE_TOOLS": "1",
    "GH_TOOL": "1",
    "EXPERIMENTAL": "1",
}


@pytest.fixture
def benchmark(monkeypatch, tmp_path):
    for key, value in AMBIENT.items():
        monkeypatch.setenv(f"WEASLEY_ASSISTANT_{key}", value)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://telemetry.invalid")
    # Provider configuration is still intentional; no test uses this endpoint.
    monkeypatch.setenv("WEASLEY_ASSISTANT_API_KEY", "offline-test-key")
    monkeypatch.setenv("WEASLEY_ASSISTANT_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("WEASLEY_ASSISTANT_MAX_ITERATIONS", "8")
    monkeypatch.setenv("WEASLEY_ASSISTANT_GRAPH_WORKFLOWS", "0")
    homes = []
    mkdtemp = tempfile.mkdtemp

    def sandbox(**options):
        home = Path(mkdtemp(dir=tmp_path, **options))
        homes.append(home)
        return str(home)

    monkeypatch.setattr(tempfile, "mkdtemp", sandbox)
    monkeypatch.setattr(arena.scoring, "case_for_message", lambda _: CASE)
    monkeypatch.setattr(arena, "load_settings", lambda: SimpleNamespace(home=tmp_path / "history"))

    def run(entry, *, trials=1, **options):
        if entry == "shootout":
            return shootout.run_one("anthropic", "offline-model", [CASE], trials=trials)
        events = []
        arena.compare_stream(CASE["input"], [f"anthropic:offline-{n}" for n in range(trials)],
                             lambda kind, data: events.append((kind, data)), **options)
        return events

    return run, homes


@pytest.mark.parametrize("entry", ["shootout", "arena"])
@pytest.mark.parametrize("field,expected", [
    ("semantic_store", "sqlite"),
    ("episodic_store", "sqlite"),
    ("apple_calendar", False),
    ("google_calendar", False),
    ("apple_tools", False),
    ("gh_tool", False),
    ("experimental", False),
    ("otel_endpoint", ""),
])
def test_ambient_personal_integrations_are_not_inherited(monkeypatch, benchmark, entry, field, expected):
    seen = []

    class Assistant:
        def __init__(self, settings):
            seen.append(settings)
            self.memory = SimpleNamespace(facts=SimpleNamespace(add=lambda *_: None))

        def respond(self, *_, **__):
            return SimpleNamespace(reply="Done", iterations=1, tool_calls=[])

    monkeypatch.setattr(app_module, "WeasleyAssistant", Assistant)
    run, _ = benchmark
    before = dict(os.environ)
    run(entry)
    assert len(seen) == 1
    assert getattr(seen[0], field) == expected
    assert dict(os.environ) == before


@pytest.mark.parametrize("apple,coding", [(False, False), (True, False), (False, True), (True, True)])
def test_arena_explicit_opt_ins_and_provider_settings_survive(monkeypatch, benchmark, apple, coding):
    seen = []

    class Assistant:
        def __init__(self, settings):
            seen.append(settings)
            self.memory = SimpleNamespace(facts=SimpleNamespace(add=lambda *_: None))

        def respond(self, *_, **__):
            return SimpleNamespace(reply="Done", iterations=1, tool_calls=[])

    monkeypatch.setattr(app_module, "WeasleyAssistant", Assistant)
    run, _ = benchmark
    run("arena", apple=apple, coding=coding)
    settings = seen[0]
    assert settings.apple_calendar is apple
    assert settings.experimental is coding
    assert settings.google_calendar is False
    assert settings.apple_tools is False
    assert settings.provider == "anthropic" and settings.model == "offline-0"
    assert settings.small_model == ""
    assert settings.api_key == "offline-test-key"
    assert settings.base_url == "https://provider.invalid"
    assert settings.max_iterations == 8
    # Creating a benchmark must not change the ordinary chat configuration.
    normal = Settings()
    assert normal.semantic_store == "supabase" and normal.episodic_store == "notion"
    assert normal.apple_tools and normal.gh_tool and normal.experimental
    assert normal.otel_endpoint == "http://telemetry.invalid"


@pytest.mark.parametrize("entry", ["shootout", "arena"])
def test_real_assistant_keeps_seed_memory_and_tool_writes_local(monkeypatch, benchmark, entry):
    from weasley_assistant.memory.episodic import notion_store
    from weasley_assistant.memory.semantic import supabase_store
    from weasley_assistant.ops.tracing import Tracer
    from weasley_assistant.tools import apple, calendar, experimental, github

    forbidden = []

    def reject(name):
        def fail(*_, **__):
            forbidden.append(name)
            raise AssertionError(f"personal integration reached: {name}")
        return fail

    monkeypatch.setattr(supabase_store, "SupabaseFactStore", reject("supabase"))
    monkeypatch.setattr(notion_store, "NotionEpisodeStore", reject("notion"))
    monkeypatch.setattr(apple, "make_tools", reject("apple_tools"))
    monkeypatch.setattr(github, "make_tool", reject("github"))
    monkeypatch.setattr(experimental, "make_tools", reject("experimental"))
    monkeypatch.setattr(calendar, "sync_to_google_calendar", reject("google_write"))
    monkeypatch.setattr(calendar, "sync_to_apple_calendar", reject("apple_write"))

    init_otel = Tracer._init_otel

    def guarded_otel(self, settings):
        if settings.otel_endpoint:
            return reject("otel")()
        return init_otel(self, settings)

    monkeypatch.setattr(Tracer, "_init_otel", guarded_otel)

    def client(_settings):
        return ScriptedClient([
            response([text_block('{"retrieve": true, "query": "alex", "reason": "seed"}')]),
            response([
                tool_block("create_event", {"title": "Benchmark event", "start": "2026-11-05T09:00"}, "event"),
                tool_block("save_note", {"subject": "trial", "content": "Only in this trial"}, "note"),
            ], "tool_use"),
            response([text_block("Booked locally")]),
        ])

    monkeypatch.setattr(app_module, "get_client", client)
    run, homes = benchmark
    before = dict(os.environ)
    report = run(entry, trials=2)
    assert forbidden == []
    assert dict(os.environ) == before
    assert len(homes) == len(set(homes)) == 2
    for home in homes:
        import sqlite3
        with sqlite3.connect(home / "state.db") as conn:
            assert conn.execute("SELECT subject, content FROM facts ORDER BY id").fetchall() == [
                ("alex", "Alex prefers mornings"), ("trial", "Only in this trial"),
            ]
            assert conn.execute("SELECT title FROM calendar_events").fetchall() == [("Benchmark event",)]
            assert conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 2
        assert "SUMMARY:Benchmark event" in (home / "calendar.ics").read_text()
        assert list((home / "traces").glob("*.jsonl"))
    if entry == "shootout":
        assert report["passed"] == report["total"] == 2
    else:
        results = [data for kind, data in report if kind == "result"]
        assert len(results) == 2 and all(r["completion"]["passed"] for r in results)
        assert report[-1] == ("done", {})


@pytest.mark.parametrize("entry", ["shootout", "arena"])
@pytest.mark.parametrize("graph", [False, True])
def test_normal_harness_configuration_is_preserved(monkeypatch, benchmark, entry, graph):
    seen = []
    monkeypatch.setenv("WEASLEY_ASSISTANT_GRAPH_WORKFLOWS", "1" if graph else "0")
    monkeypatch.setenv("WEASLEY_ASSISTANT_MAX_TOKENS", "1234")
    monkeypatch.setenv("WEASLEY_ASSISTANT_RETRIEVAL_TOP_K", "3")

    class Assistant:
        def __init__(self, settings):
            seen.append(settings)
            self.memory = SimpleNamespace(facts=SimpleNamespace(add=lambda *_: None))

        def respond(self, *_, **__):
            return SimpleNamespace(reply="Done", iterations=1, tool_calls=[])

    monkeypatch.setattr(app_module, "WeasleyAssistant", Assistant)
    run, _ = benchmark
    run(entry)
    settings = seen[0]
    assert settings.graph_workflows is graph
    assert settings.max_tokens == 1234 and settings.retrieval_top_k == 3
    assert settings.max_iterations == 8
    assert settings.provider == "anthropic"
    assert settings.api_key == "offline-test-key"
    assert settings.base_url == "https://provider.invalid"
    assert settings.small_model == ""
