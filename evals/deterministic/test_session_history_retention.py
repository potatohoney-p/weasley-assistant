"""Bound the live working-history cache, not only the outgoing prompt."""

from types import SimpleNamespace

import pytest

from evals.helpers import ScriptedClient, make_assistant
from weasley_assistant.config import Settings
from weasley_assistant.runtime.session import Session


@pytest.mark.parametrize("turns", [0, -1, 1, 3, 12])
def test_long_session_retains_only_recent_complete_exchanges(tmp_path, turns):
    app = make_assistant(tmp_path, client=ScriptedClient([]), history_turns=turns)
    try:
        original = app.session.history
        for i in range(100):
            app.session.add_exchange(f"question {i}", f"reply {i}")
            assert len(original) <= max(0, turns) * 2
        assert app.session.history is original
        expected = []
        for i in range(100 - max(0, turns), 100):
            expected += [{"role": "user", "content": f"question {i}"},
                         {"role": "assistant", "content": f"reply {i}"}]
        assert original == expected
        assert app.memory.session_history("default") == [
            (f"question {i}", f"reply {i}") for i in range(100)]
    finally:
        app.close()
        app.conn.close()


def test_failed_persistence_never_evicts_prior_working_history():
    def fail(*args, **kwargs):
        raise OSError("write failed")

    session = Session(Settings(history_turns=1))
    session.add_exchange("prior", "answer")
    before = list(session.history)
    session.memory = SimpleNamespace(log_chat=fail)
    with pytest.raises(OSError, match="write failed"):
        session.add_exchange("rejected", "reply")
    assert session.history == before


def test_memoryless_session_keeps_latest_tool_receipt_and_list_identity():
    session = Session(Settings(history_turns=1))
    original = session.history
    session.add_exchange("first", "first reply")
    calls = [{"tool": "save_note", "args": {"content": "서울"}, "output": "saved"}]
    session.add_exchange("second", "second reply", tool_calls=calls)
    assert session.history is original
    assert original == [
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "second reply\n[tools used: save_note({'content': '서울'}) -> saved]"},
    ]


def test_lowered_window_takes_effect_after_next_successful_exchange():
    settings = Settings(history_turns=3)
    session = Session(settings)
    for i in range(3):
        session.add_exchange(str(i), "reply")
    settings.history_turns = 1
    session.add_exchange("latest", "answer")
    assert session.history == [{"role": "user", "content": "latest"},
                               {"role": "assistant", "content": "answer"}]
    settings.history_turns = 0
    session.add_exchange("disabled", "answer")
    assert session.history == []
