"""Committed replies survive optional memory housekeeping failures; no live calls."""
from __future__ import annotations

import json
import sqlite3

import pytest

from evals.helpers import ScriptedClient, make_assistant, response, text_block, tool_block


def assistant(tmp_path):
    client = ScriptedClient([
        response([text_block('{"retrieve": false}')]),
        response([tool_block("save_note", {"subject": "Ada", "content": "likes tea"})],
                 stop_reason="tool_use"),
        response([text_block("Saved your note.")]),
    ])
    return make_assistant(tmp_path, client=client, consolidate_every=99)


def records(agent):
    return [json.loads(line) for line in agent.tracer.path.read_text().splitlines()]


@pytest.mark.parametrize("failed", ["consolidation", "export_markdown", "both"])
def test_real_storage_failure_preserves_reply_and_effect_once(tmp_path, failed):
    agent = assistant(tmp_path)
    # Deny consolidation's pending-log read, not the required INSERT or tool write.
    if failed in ("consolidation", "both"):
        agent.conn.set_authorizer(lambda action, table, *rest:
                                 sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
                                 and table == "chat_log" else sqlite3.SQLITE_OK)
    if failed in ("export_markdown", "both"):
        (tmp_path / "MEMORY.md").mkdir()
    observed = []
    result = agent.respond("Remember Ada likes tea", observer=lambda k, e: observed.append((k, e)))
    agent.conn.set_authorizer(None)
    assert result.reply == "Saved your note."
    assert [c["tool"] for c in result.tool_calls] == ["save_note"]
    assert agent.conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 1
    assert agent.conn.execute("SELECT count(*) FROM chat_log").fetchone()[0] == 2
    assert len(agent.session.history) == 2
    assert agent.client._script == []
    errors = [e for k, e in observed if k == "maintenance_error"]
    expected = ["consolidation", "export_markdown"] if failed == "both" else [failed]
    assert [e["operation"] for e in errors] == expected
    assert all(e["chat_persisted"] is True for e in errors)
    assert all("error_type" in e and "error" not in e for e in errors)
    trace = records(agent)
    assert [r["operation"] for r in trace if r["type"] == "maintenance_error"] == expected
    assert trace[-1]["type"] == "turn_end"
    assert trace[-1]["reply"] == result.reply
    if failed == "consolidation":
        assert "likes tea" in (tmp_path / "MEMORY.md").read_text()
    # Recover storage and make a new, explicit request: no replay of the tool.
    if failed in ("export_markdown", "both"):
        (tmp_path / "MEMORY.md").rmdir()
    agent.client._script.extend([
        response([text_block('{"retrieve": false}')]), response([text_block("You're welcome.")]),
    ])
    assert agent.respond("Thanks").reply == "You're welcome."
    assert agent.conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 1
    assert agent.conn.execute("SELECT count(*) FROM chat_log").fetchone()[0] == 4
    assert "likes tea" in (tmp_path / "MEMORY.md").read_text()


def test_healthy_maintenance_has_no_error_event(tmp_path):
    agent = assistant(tmp_path)
    assert agent.respond("Remember Ada likes tea").reply == "Saved your note."
    assert not any(r["type"] == "maintenance_error" for r in records(agent))
    assert "likes tea" in (tmp_path / "MEMORY.md").read_text()


def test_required_persistence_failure_still_propagates(tmp_path, monkeypatch):
    agent = assistant(tmp_path)
    error = RuntimeError("required persistence")
    calls = []
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(agent.memory, "log_chat", fail)
    monkeypatch.setattr(agent.memory, "maybe_consolidate", lambda **kw: calls.append("consolidation"))
    monkeypatch.setattr(agent.memory, "export_markdown", lambda: calls.append("export"))
    with pytest.raises(RuntimeError) as exc:
        agent.respond("Remember Ada likes tea")
    assert exc.value is error
    assert calls == []
    assert not any(r["type"] == "turn_end" for r in records(agent))


@pytest.mark.parametrize("operation", ["maybe_consolidate", "export_markdown"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_propagate(tmp_path, monkeypatch, operation, error_type):
    agent = assistant(tmp_path)
    error = error_type("stop")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(agent.memory, operation, fail)
    with pytest.raises(error_type) as exc:
        agent.respond("Remember Ada likes tea")
    assert exc.value is error
    assert not any(r["type"] == "maintenance_error" for r in records(agent))


def test_exception_details_are_not_published(tmp_path, monkeypatch):
    agent = assistant(tmp_path)
    def fail():
        raise OSError("sensitive backend details")
    monkeypatch.setattr(agent.memory, "export_markdown", fail)
    observed = []
    agent.respond("Remember Ada likes tea", observer=lambda k, e: observed.append((k, e)))
    assert "sensitive backend details" not in json.dumps(observed)
    assert "sensitive backend details" not in agent.tracer.path.read_text()
    assert next(e for k, e in observed if k == "maintenance_error")["error_type"] == "OSError"


def test_consolidation_observer_failure_is_not_reclassified(tmp_path, monkeypatch):
    agent = assistant(tmp_path)
    error = ValueError("observer failure")
    def consolidate(notify):
        notify("consolidation", {"new_facts": 1})
    def observer(kind, event):
        if kind == "consolidation":
            raise error
    monkeypatch.setattr(agent.memory, "maybe_consolidate", consolidate)
    with pytest.raises(ValueError) as exc:
        agent.respond("Remember Ada likes tea", observer=observer)
    assert exc.value is error
    assert not any(r["type"] == "maintenance_error" for r in records(agent))


def test_error_observer_failure_still_propagates(tmp_path, monkeypatch):
    agent = assistant(tmp_path)
    error = ValueError("observer failure")
    def export():
        raise OSError("mirror unavailable")
    def observer(kind, event):
        if kind == "maintenance_error":
            raise error
    monkeypatch.setattr(agent.memory, "export_markdown", export)
    with pytest.raises(ValueError) as exc:
        agent.respond("Remember Ada likes tea", observer=observer)
    assert exc.value is error


def test_cli_explains_committed_reply_without_retrying(capsys):
    from weasley_assistant.gateway.cli import _observer

    _observer("maintenance_error", {"operation": "export_markdown", "error_type": "OSError",
                                    "chat_persisted": True})
    output = " ".join(capsys.readouterr().out.split())
    assert "export_markdown failed (OSError)" in output
    assert "chat saved, reply preserved" in output
    assert "Do not repeat the request" in output


def test_successful_consolidation_keeps_existing_event(tmp_path, monkeypatch):
    agent = assistant(tmp_path)
    def consolidate(notify):
        notify("consolidation", {"new_facts": 2})
    monkeypatch.setattr(agent.memory, "maybe_consolidate", consolidate)
    observed = []
    assert agent.respond("Remember Ada likes tea", observer=lambda k, e: observed.append((k, e))).reply
    assert ("consolidation", {"new_facts": 2}) in observed
    assert not any(k == "maintenance_error" for k, _ in observed)
