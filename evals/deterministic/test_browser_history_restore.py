"""Browser restarts and model switches must restore actual prompt context."""
from __future__ import annotations

import pytest

from evals.helpers import ScriptedClient, response, text_block
from weasley_assistant.db import connect
from weasley_assistant.ops import browser_agent
from weasley_assistant.runtime.session import Session


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("WEASLEY_ASSISTANT_HOME", str(tmp_path))
    monkeypatch.setenv("WEASLEY_ASSISTANT_PROVIDER", "anthropic")
    monkeypatch.setenv("WEASLEY_ASSISTANT_GRAPH_WORKFLOWS", "false")
    monkeypatch.setenv("WEASLEY_ASSISTANT_CONSOLIDATE_EVERY", "100")
    monkeypatch.setattr(browser_agent, "_agent", None)
    monkeypatch.setattr(browser_agent, "_dashboard_session", None)
    client = ScriptedClient([])
    monkeypatch.setattr("weasley_assistant.app.get_client", lambda settings: client)
    conn = connect(tmp_path)
    for i in range(4):
        for role, content in (("user", f"question {i}"), ("assistant", f"answer {i}")):
            conn.execute("INSERT INTO chat_log(role,content,session_id,source) VALUES (?,?,?,?)",
                         (role, content, "saved-thread", "dashboard"))
    conn.commit()
    conn.close()
    yield client
    if browser_agent.current() is not None:
        browser_agent.current().close()
        browser_agent.current().conn.close()


@pytest.mark.parametrize("turns", [0, -1, 1, 3])
@pytest.mark.parametrize("path", ["restart", "rebuild"])
def test_restored_prompt_uses_bounded_durable_history(browser, monkeypatch, turns, path):
    monkeypatch.setenv("WEASLEY_ASSISTANT_HISTORY_TURNS", str(turns))
    if path == "rebuild":
        first = browser_agent.get_agent()
        # A chosen thread can differ from the latest dashboard thread.
        first.session.switch("saved-thread")
        assert browser_agent.rebuild() is None
        first.conn.close()
    agent = browser_agent.get_agent()
    expected = []
    for i in range(4 - max(0, turns), 4):
        expected.extend([{"role": "user", "content": f"question {i}"},
                         {"role": "assistant", "content": f"answer {i}"}])
    assert agent.session.session_id == "saved-thread"
    assert agent.session.history == expected
    sent = []

    def create(**kwargs):
        sent.append(list(kwargs["messages"]))
        if len(sent) == 1:
            return response([text_block('{"retrieve": false, "query": "", "reason": "test"}')])
        return response([text_block("continued")])

    browser.messages.create = create
    agent.respond("continue", source="dashboard")
    assert sent[-1] == expected + [{"role": "user", "content": "continue"}]
    assert len(agent.memory.session_history("saved-thread")) == 5


@pytest.mark.parametrize("path", ["restart", "rebuild"])
def test_failed_restore_does_not_publish_partial_agent(browser, monkeypatch, path):
    old = browser_agent.get_agent() if path == "rebuild" else None
    prior_session = browser_agent._dashboard_session
    calls = []
    original = Session.switch

    def fail(self, session_id):
        calls.append(session_id)
        raise RuntimeError("history read failed")

    monkeypatch.setattr(Session, "switch", fail)
    if path == "restart":
        with pytest.raises(RuntimeError, match="history read failed"):
            browser_agent.get_agent()
    else:
        assert browser_agent.rebuild() == "history read failed"
    assert calls == ["saved-thread"]
    assert browser_agent.current() is old
    assert browser_agent._dashboard_session == prior_session
    monkeypatch.setattr(Session, "switch", original)
    assert browser_agent.get_agent().session.session_id == "saved-thread"


def test_rebuild_restores_selected_not_most_recent_thread(browser):
    agent = browser_agent.get_agent()
    agent.session.start_new("selected-thread")
    agent.session.add_exchange("selected question", "selected answer", source="cli")
    assert browser_agent.rebuild() is None
    fresh = browser_agent.current()
    assert fresh.session.session_id == "selected-thread"
    assert fresh.session.history == [
        {"role": "user", "content": "selected question"},
        {"role": "assistant", "content": "selected answer"},
    ]
    agent.conn.close()
