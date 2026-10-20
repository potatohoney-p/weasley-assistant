"""DETERMINISTIC EVAL — working memory is a bounded sliding window.

a maintainer's insight while testing Telegram: its one always-on session accumulated
history forever, and every turn resent the whole thing (unbounded context ->
cost/latency climb -> eventual context-limit break). Working memory must be a
fixed window; older turns live in state.db + consolidation, not the prompt."""

from __future__ import annotations

import pytest

from evals.helpers import ScriptedClient, make_assistant, response, text_block
from weasley_assistant.config import Settings
from weasley_assistant.runtime.session import Session


def _gate_skip():
    return response([text_block('{"retrieve": false, "query": "", "reason": "t"}')])


def test_prompt_history_is_windowed(tmp_path, monkeypatch):
    monkeypatch.setenv("WEASLEY_ASSISTANT_HISTORY_TURNS", "3")   # keep only last 3 turns
    sent = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            # snapshot the message count NOW — run_loop mutates the same list
            # (appends the assistant reply) after this call returns
            sent.append(list(kwargs.get("messages", [])))
            return self._script.pop(0)

    # 5 turns; each turn = gate call (skip) + one loop call
    script = []
    for _ in range(5):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_assistant(tmp_path / "home", client=Recorder(script))
    for i in range(5):
        app.respond(f"message number {i}")

    # the LAST loop call's messages: at most 3 turns * 2 rows + the new user
    # message = 7, and it must NOT contain the oldest turns
    last = sent[-1]
    assert len(last) <= 3 * 2 + 1, f"window not applied: {len(last)} messages"
    text_blob = " ".join(str(m.get("content", "")) for m in last)
    assert "message number 0" not in text_blob
    assert "message number 4" in text_blob   # the newest turn is present


def test_default_window_is_generous_but_finite(tmp_path, monkeypatch):
    monkeypatch.delenv("WEASLEY_ASSISTANT_HISTORY_TURNS", raising=False)
    app = make_assistant(tmp_path / "home", client=ScriptedClient([]))
    assert app.settings.history_turns == 12


@pytest.mark.parametrize("turns", [0, -1, 1, 3])
def test_live_context_honors_window_boundaries_and_keeps_durable_history(tmp_path, turns):
    sent = []

    class Recorder(ScriptedClient):
        def _create(self, **kwargs):
            sent.append(list(kwargs.get("messages", [])))
            return super()._create(**kwargs)

    script = []
    for _ in range(5):
        script += [_gate_skip(), response([text_block("ok")])]
    app = make_assistant(tmp_path / "home", client=Recorder(script), history_turns=turns)
    try:
        for i in range(5):
            app.respond(f"message {i}")

        expected_turns = max(0, turns)
        expected = []
        for i in range(4 - expected_turns, 4):
            expected += [{"role": "user", "content": f"message {i}"},
                         {"role": "assistant", "content": "ok"}]
        expected += [{"role": "user", "content": "message 4"}]
        assert sent[-1] == expected
        # A context budget changes model input, not the saved conversation.
        assert app.memory.session_history("default") == [(f"message {i}", "ok") for i in range(5)]
    finally:
        app.close()
        app.conn.close()


@pytest.mark.parametrize("turns", [0, -1, 1, 3])
def test_resumed_context_honors_window_boundaries_without_erasing_saved_turns(tmp_path, turns):
    app = make_assistant(tmp_path / "home", client=ScriptedClient([]), history_turns=turns)
    try:
        for i in range(5):
            app.session.add_exchange(f"message {i}", f"reply {i}")
        app.session.start_new("other")
        app.session.add_exchange("other thread", "other reply")
        app.session.switch("default")

        expected = []
        for i in range(5 - max(0, turns), 5):
            expected += [{"role": "user", "content": f"message {i}"},
                         {"role": "assistant", "content": f"reply {i}"}]
        assert app.session.session_id == "default"
        assert app.session.history == expected
        assert len(app.memory.session_history("default")) == 5
        assert app.memory.session_history("other") == [("other thread", "other reply")]
    finally:
        app.close()
        app.conn.close()


@pytest.mark.parametrize("turns", [0, -1])
def test_disabled_resume_context_does_not_load_the_saved_transcript(turns):
    class NoHistoryReads:
        def session_history(self, session_id):
            raise AssertionError("disabled working history should not read the durable transcript")

    session = Session(Settings(history_turns=turns), memory=NoHistoryReads())
    session.history = [{"role": "user", "content": "previous thread"}]
    session.switch("saved-thread")
    assert session.session_id == "saved-thread"
    assert session.history == []
