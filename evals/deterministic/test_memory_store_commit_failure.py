"""Failed direct memory edits must not leak into a later successful commit."""
import sqlite3

import pytest

from weasley_assistant.db import connect
from weasley_assistant.memory.episodic.store import SqliteEpisodeStore
from weasley_assistant.memory.semantic.store import SqliteFactStore


@pytest.mark.parametrize('operation', ['fact_add', 'fact_update', 'fact_subject', 'fact_delete',
                                       'episode_add', 'episode_delete'])
@pytest.mark.parametrize('caller_pending', [False, True])
@pytest.mark.parametrize('native_autocommit', [False, True])
def test_failed_commit_discards_only_memory_edit(
    tmp_path, operation, caller_pending, native_autocommit
):
    conn = connect(tmp_path)
    if native_autocommit:
        if not hasattr(conn, 'autocommit'):
            conn.close()
            pytest.skip('Python 3.12 native autocommit mode')
        conn.autocommit = True
    conn.execute('PRAGMA busy_timeout=1')
    facts, episodes = SqliteFactStore(conn), SqliteEpisodeStore(conn)
    facts.add('original', 'original memory')
    episodes.add('original episode', '2026-11-01')
    reader = sqlite3.connect(tmp_path / 'state.db')
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM facts').fetchall()
    try:
        if caller_pending:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO chat_log(role, content) VALUES ('user', 'pending caller')")
        actions = {
            'fact_add': lambda: facts.add('new', 'new memory'),
            'fact_update': lambda: facts.update(1, 'new memory'),
            'fact_subject': lambda: facts.update(1, 'new memory', 'new'),
            'fact_delete': lambda: facts.delete(1),
            'episode_add': lambda: episodes.add('new episode', '2026-11-02'),
            'episode_delete': lambda: episodes.delete(1),
        }
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            actions[operation]()
        assert conn.in_transaction is caller_pending
        reader.rollback()
        if conn.in_transaction:
            conn.execute("COMMIT")  # later success must not persist the failed edit
        assert [(r['subject'], r['content']) for r in facts.list()] == [('original', 'original memory')]
        assert [r['summary'] for r in episodes.list()] == ['original episode']
        assert len(facts.search('original')) == 1
        assert facts.search('new') == []
        assert len(episodes.search('original')) == 1
        assert episodes.search('new') == []
        assert conn.execute('SELECT count(*) FROM chat_log').fetchone()[0] == int(caller_pending)
        # The same requested operation can subsequently succeed.
        actions[operation]()
        assert not conn.in_transaction
    finally:
        reader.close()
        conn.close()


@pytest.mark.parametrize('caller_pending', [False, True])
@pytest.mark.parametrize('autocommit', [False, True])
def test_deferred_constraint_and_statement_failure(tmp_path, caller_pending, autocommit):
    conn = connect(tmp_path)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
        CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE child(id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED);
        CREATE TRIGGER invalid_fact AFTER INSERT ON facts BEGIN
            INSERT INTO child VALUES (1);
        END;
    ''')
    if autocommit:
        conn.isolation_level = None
    if caller_pending:
        conn.execute('BEGIN')
        conn.execute("INSERT INTO chat_log(role,content) VALUES ('user','keep')")
    try:
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            SqliteFactStore(conn).add('bad', 'rejected fact')
        assert conn.in_transaction is caller_pending
        assert conn.execute('SELECT count(*) FROM facts').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM child').fetchone()[0] == 0
        conn.commit()
        assert conn.execute('SELECT count(*) FROM chat_log').fetchone()[0] == int(caller_pending)
        assert SqliteFactStore(conn).search('rejected') == []
    finally:
        conn.close()


def test_database_rollback_trigger_preserves_original_error(tmp_path):
    conn = connect(tmp_path)
    conn.executescript('''
        CREATE TRIGGER stop_fact BEFORE INSERT ON facts BEGIN
            SELECT RAISE(ROLLBACK, 'reject memory edit');
        END;
    ''')
    try:
        with pytest.raises(sqlite3.IntegrityError, match='reject memory edit'):
            SqliteFactStore(conn).add('bad', 'rejected')
        assert not conn.in_transaction
        assert SqliteFactStore(conn).list() == []
    finally:
        conn.close()
