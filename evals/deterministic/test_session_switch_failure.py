"""A failed transcript load must not change the active conversation."""

import copy
import sqlite3
from types import SimpleNamespace

import pytest

from evals.helpers import ScriptedClient, make_assistant, response, text_block
from weasley_assistant.config import Settings
from weasley_assistant.runtime.session import Session


@pytest.mark.parametrize('failure', ['call', 'iteration', 'pair'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_failed_switch_preserves_active_state(tmp_path, failure, error_type):
    error = error_type('cannot load')
    observed = []

    def history(target):
        assert target == 'new'
        observed.append((session.session_id, session.history))
        if failure == 'call':
            raise error
        if failure == 'iteration':
            def rows():
                yield ('first', 'reply')
                raise error
            return rows()
        return [('first', 'reply'), ('invalid',)]

    session = Session(Settings(home=tmp_path), memory=SimpleNamespace(session_history=history),
                      session_id='old')
    session.history = [{'role': 'user', 'content': 'old question'},
                       {'role': 'assistant', 'content': 'old answer'}]
    original = session.history
    before = copy.deepcopy(original)
    with pytest.raises(ValueError if failure == 'pair' else error_type) as caught:
        session.switch('new')
    if failure != 'pair':
        assert caught.value is error
    assert observed == [('old', original)]
    assert session.session_id == 'old'
    assert session.history is original
    assert session.history == before


def test_sqlite_read_failure_keeps_next_turn_in_original_session(tmp_path):
    client = ScriptedClient([response([text_block('{"retrieve": false}')]),
                             response([text_block('next answer')])])
    app = make_assistant(tmp_path, client=client, consolidate_every=1000)
    app.session.start_new('old')
    app.session.add_exchange('old question', 'old answer')
    app.memory.log_chat('target question', 'target answer', session_id='new')
    before = copy.deepcopy(app.session.history)
    original = app.session.history

    def deny_chat_reads(action, table, column, db, trigger):
        if action == sqlite3.SQLITE_READ and table == 'chat_log':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    app.conn.set_authorizer(deny_chat_reads)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            app.session.switch('new')
    finally:
        app.conn.set_authorizer(None)
    assert app.session.session_id == 'old'
    assert app.session.history is original
    assert app.session.history == before
    seen = []
    create = client.messages.create

    def capture(**kwargs):
        seen.append(copy.deepcopy(kwargs))
        return create(**kwargs)

    client.messages.create = capture
    assert app.respond('next question').reply == 'next answer'
    assert seen[-1]['messages'] == before + [{'role': 'user', 'content': 'next question'}]
    assert app.memory.session_history('old') == [('old question', 'old answer'),
                                               ('next question', 'next answer')]
    assert app.memory.session_history('new') == [('target question', 'target answer')]
    app.session.switch('new')  # retry succeeds after the read failure is gone
    assert app.session.session_id == 'new'
    assert app.session.history == [{'role': 'user', 'content': 'target question'},
                                   {'role': 'assistant', 'content': 'target answer'}]


@pytest.mark.parametrize('mode', ['empty', 'memory_free', 'tail'])
def test_success_publishes_new_history_without_mutating_old_list(tmp_path, mode):
    rows = [('first', 'one'), ('second', 'two')]
    session = Session(Settings(home=tmp_path, history_turns=1), session_id='old',
                      memory=None if mode == 'memory_free' else SimpleNamespace(
                          session_history=lambda _: rows if mode == 'tail' else []))
    session.history = [{'role': 'user', 'content': 'old'}]
    old = session.history
    session.switch('new')
    assert session.session_id == 'new'
    assert old == [{'role': 'user', 'content': 'old'}]
    assert session.history is not old
    assert session.history == ([{'role': 'user', 'content': 'second'},
                                {'role': 'assistant', 'content': 'two'}]
                               if mode == 'tail' else [])
