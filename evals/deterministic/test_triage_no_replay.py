"""A graph fallback must never replay a tool-capable turn (offline SQLite)."""
from types import SimpleNamespace

import pytest

from evals.helpers import make_assistant, response, text_block, tool_block
from weasley_assistant.graph import run_graph as real_run_graph


class Client:
    def __init__(self, fail=True, route="full"):
        self.calls = 0
        self.loop_calls = 0
        self.failure = RuntimeError("provider disconnected after tool result")
        self.fail = fail
        self.route = route
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls += 1
        if "tools" not in kwargs:
            prompt = kwargs["messages"][0]["content"]
            if "triage gate" in prompt:
                return response([text_block('{"route": "' + self.route + '", "reason": "test"}')])
            if "warm, concise" in prompt:
                raise RuntimeError("quick model unavailable")
            return response([text_block('{"retrieve": false}')])
        self.loop_calls += 1
        if self.loop_calls % 2:
            return response([tool_block("save_note", {"subject": "fixture", "content": "one effect"})], "tool_use")
        if self.fail:
            raise self.failure
        return response([text_block("Saved once.")])


@pytest.mark.parametrize("graph", [False, True])
def test_failed_real_loop_does_not_repeat_committed_tool(tmp_path, graph):
    client = Client()
    app = make_assistant(tmp_path / "home", client=client, graph_workflows=graph)
    try:
        with pytest.raises(RuntimeError) as caught:
            app.respond("remember this")
        assert caught.value is client.failure
        assert client.loop_calls == 2
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        assert app.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 0
        assert app.session.history == []
    finally:
        app.close()
        app.conn.close()


@pytest.mark.parametrize("late_failure", ["raise", "lose_state"])
def test_completed_loop_survives_late_graph_failure_without_replay(tmp_path, monkeypatch, late_failure):
    from weasley_assistant import graph

    def broken(*args, **kwargs):
        real_run_graph(*args, **kwargs)
        if late_failure == "raise":
            raise RuntimeError("engine finalization failed")
        return {}

    monkeypatch.setattr(graph, "run_graph", broken)
    client = Client(fail=False)
    app = make_assistant(tmp_path / "home", client=client, graph_workflows=True)
    try:
        result = app.respond("remember this")
        assert result.reply == "Saved once."
        assert len(result.tool_calls) == 1
        assert client.loop_calls == 2
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        assert app.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 2
        # Memoization is turn-local, never a cross-request deduplication cache.
        app.respond("remember another")
        assert client.loop_calls == 4
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
    finally:
        app.close()
        app.conn.close()


def test_quick_failure_still_falls_back_to_one_full_loop(tmp_path):
    client = Client(fail=False, route="quick")
    app = make_assistant(tmp_path / "home", client=client, graph_workflows=True)
    try:
        assert app.respond("hello").reply == "Saved once."
        assert client.loop_calls == 2
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    finally:
        app.close()
        app.conn.close()


def test_late_engine_error_does_not_replace_original_loop_failure(tmp_path, monkeypatch):
    from weasley_assistant import graph

    def broken(*args, **kwargs):
        real_run_graph(*args, **kwargs)
        raise ValueError("later graph failure")

    monkeypatch.setattr(graph, "run_graph", broken)
    client = Client()
    app = make_assistant(tmp_path / "home", client=client, graph_workflows=True)
    try:
        with pytest.raises(RuntimeError) as caught:
            app.respond("remember this")
        assert caught.value is client.failure
        assert client.loop_calls == 2
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        # A separate explicit request may try again; effects are not rolled back.
        client.fail = False
        assert app.respond("try again").reply == "Saved once."
        assert client.loop_calls == 4
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
    finally:
        app.close()
        app.conn.close()


def test_engine_failure_before_full_turn_still_runs_one_fallback(tmp_path, monkeypatch):
    from weasley_assistant import graph

    def broken(*args, **kwargs):
        raise RuntimeError("engine could not start")

    monkeypatch.setattr(graph, "run_graph", broken)
    client = Client(fail=False)
    app = make_assistant(tmp_path / "home", client=client, graph_workflows=True)
    try:
        assert app.respond("remember this").reply == "Saved once."
        assert client.loop_calls == 2
        assert app.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    finally:
        app.close()
        app.conn.close()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_base_exception_propagates_without_fallback(tmp_path, monkeypatch, exception_type):
    app = make_assistant(tmp_path / "home", client=Client(), graph_workflows=True)
    failure = exception_type("stop this turn")
    attempts = []

    def stop(*args, **kwargs):
        attempts.append(True)
        raise failure

    monkeypatch.setattr(app, "_run_full_turn", stop)
    try:
        with pytest.raises(exception_type) as caught:
            app.respond("remember this")
        assert caught.value is failure
        assert attempts == [True]
    finally:
        app.close()
        app.conn.close()
