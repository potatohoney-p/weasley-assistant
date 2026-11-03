"""Real SQLite transaction modes must produce durable all-or-nothing batches."""
import json
import sqlite3

import pytest

from evals.helpers import ScriptedClient, response, text_block
from weasley_assistant.db import connect
from weasley_assistant.memory import Memory
from weasley_assistant.memory.consolidation import consolidate_if_due
from weasley_assistant.memory.episodic.store import SqliteEpisodeStore
from weasley_assistant.memory.semantic.store import SqliteFactStore


@pytest.mark.parametrize('mode', ['legacy', 'isolation_none', 'native'])
@pytest.mark.parametrize('failure', [None, 'episode', 'commit', 'interrupt'])
def test_batch_is_durable_or_fully_discarded(tmp_path, mode, failure):
    conn = connect(tmp_path)
    memory = Memory.__new__(Memory)
    memory.conn = conn
    memory.log_chat('question', 'answer')
    if mode == 'native':
        if not hasattr(conn, 'autocommit'):
            pytest.skip('native autocommit requires Python 3.12')
        conn.autocommit = True
    elif mode == 'isolation_none':
        conn.isolation_level = None
    conn.execute('PRAGMA busy_timeout=1')
    if failure == 'episode':
        conn.execute("CREATE TRIGGER reject_episode BEFORE INSERT ON episodes BEGIN SELECT RAISE(ABORT,'reject'); END")
    reader = sqlite3.connect(tmp_path / 'state.db')
    if failure == 'commit':
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM facts').fetchall()
    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    if failure == 'interrupt':
        def stop(*args, **kwargs):
            raise KeyboardInterrupt()
        episodes.add = stop
    client = ScriptedClient([response([text_block(json.dumps({
        'facts': [{'subject': 'alex', 'content': 'morning meetings'}], 'episode': 'planned meeting'
    }))])])
    try:
        if failure == 'interrupt':
            with pytest.raises(KeyboardInterrupt):
                consolidate_if_due(conn, client, 'offline', 1, facts, episodes)
        else:
            result = consolidate_if_due(conn, client, 'offline', 1, facts, episodes)
            assert result == (1 if failure is None else 0)
        assert not conn.in_transaction
        reader.rollback()
        # Unrelated later commits must never publish the rejected batch.
        conn.execute("INSERT INTO calendar_events(title,start) VALUES ('later','2026-11-03')")
        conn.commit()
        expected = int(failure is None)
        assert reader.execute('SELECT count(*) FROM facts').fetchone()[0] == expected
        assert reader.execute('SELECT count(*) FROM episodes').fetchone()[0] == expected
        assert reader.execute('SELECT sum(consolidated) FROM chat_log').fetchone()[0] == 2 * expected
        assert len(facts.search('morning')) == expected
    finally:
        reader.close()
        conn.close()


def test_native_always_transactional_connection_preserves_caller_work(tmp_path):
    conn = connect(tmp_path)
    if not hasattr(conn, 'autocommit'):
        pytest.skip('native autocommit requires Python 3.12')
    memory = Memory.__new__(Memory)
    memory.conn = conn
    memory.log_chat('question', 'answer')
    conn.autocommit = False
    conn.execute("INSERT INTO calendar_events(title,start) VALUES ('caller','2026-11-03')")
    client = ScriptedClient([response([text_block('{"facts":[],"episode":""}')])])
    try:
        assert consolidate_if_due(conn, client, 'offline', 1,
                                  SqliteFactStore(conn), SqliteEpisodeStore(conn)) == 0
        assert conn.in_transaction
        assert conn.execute('SELECT sum(consolidated) FROM chat_log').fetchone()[0] == 0
        conn.rollback()
        assert conn.execute('SELECT count(*) FROM calendar_events').fetchone()[0] == 0
    finally:
        conn.close()
