"""Working history must not advertise a chat exchange whose write failed."""

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from evals.helpers import ScriptedClient, make_assistant, response, text_block
from weasley_assistant.config import Settings
from weasley_assistant.runtime.session import Session


@pytest.mark.parametrize("error", [RuntimeError("disk"), KeyboardInterrupt(), SystemExit(2)])
def test_failed_log_preserves_history_and_exception(tmp_path, error):
    def fail(*args, **kwargs):
        raise error

    session = Session(Settings(home=tmp_path), memory=SimpleNamespace(log_chat=fail))
    session.history = [{"role": "user", "content": "prior"}]
    original = session.history
    before = copy.deepcopy(original)
    with pytest.raises(type(error)) as caught:
        session.add_exchange("new", "reply")
    assert caught.value is error
    assert session.history is original
    assert session.history == before


@pytest.mark.parametrize("failure", ["metadata", "user_insert", "assistant_insert"])
def test_real_sqlite_failure_does_not_enter_next_prompt(tmp_path, failure):
    client = ScriptedClient([
        response([text_block('{"retrieve": false}')]),
        response([text_block("next answer")]),
    ])
    assistant = make_assistant(tmp_path, client=client, consolidate_every=1000)
    session = assistant.session
    session.start_new("conversation")
    session.add_exchange("prior question", "prior answer")
    before = copy.deepcopy(session.history)
    if failure != "metadata":
        role = "user" if failure == "user_insert" else "assistant"
        assistant.conn.execute(
            f"CREATE TEMP TRIGGER fail_chat BEFORE INSERT ON chat_log "
            f"WHEN NEW.role = '{role}' BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    meta = {"bad": object()} if failure == "metadata" else None
    with pytest.raises((TypeError, sqlite3.IntegrityError)):
        session.add_exchange("failed question", "failed answer", meta=meta)
    assert session.history == before
    # Main's durable-write atomicity is a separate concern (#10): discard its
    # pending partial transaction explicitly before simulating a later request.
    assistant.conn.rollback()
    if failure != "metadata":
        assistant.conn.execute("DROP TRIGGER fail_chat")
    seen = []
    original_create = client.messages.create

    def capture(**kwargs):
        seen.append(copy.deepcopy(kwargs))
        return original_create(**kwargs)

    client.messages.create = capture
    result = assistant.respond("next question")
    assert result.reply == "next answer"
    assert seen[-1]["messages"] == before + [{"role": "user", "content": "next question"}]
    assert session.history == before + [
        {"role": "user", "content": "next question"},
        {"role": "assistant", "content": "next answer"},
    ]
    assert list(assistant.memory.session_history("conversation")) == [
        ("prior question", "prior answer"), ("next question", "next answer")]


@pytest.mark.parametrize("with_memory", [False, True])
@pytest.mark.parametrize("with_tools", [False, True])
def test_success_preserves_record_metadata_and_list_identity(tmp_path, with_memory, with_tools):
    writes = []
    session = Session(Settings(home=tmp_path), session_id="thread")
    if with_memory:
        def log_chat(*args, **kwargs):
            assert session.history == []  # not visible until persistence succeeds
            writes.append((args, kwargs))
        session.memory = SimpleNamespace(log_chat=log_chat)
    original = session.history
    tools = [{"tool": "save_note", "args": {"content": "서울"}, "output": "saved"}]
    record = "answer"
    if with_tools:
        record += "\n[tools used: save_note({'content': '서울'}) -> saved]"
    meta = {"iterations": 1}
    session.add_exchange("question", "answer", tool_calls=tools if with_tools else None,
                         source="dashboard", meta=meta)
    assert session.history is original
    assert session.history == [{"role": "user", "content": "question"},
                               {"role": "assistant", "content": record}]
    assert writes == ([(('question', record),
                       {"session_id": "thread", "source": "dashboard", "meta": meta})]
                      if with_memory else [])
