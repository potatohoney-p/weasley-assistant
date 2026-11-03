"""Chat durability and failure cleanup across actual SQLite transaction modes."""
import sqlite3

import pytest

from weasley_assistant.db import connect
from weasley_assistant.memory import Memory


@pytest.mark.parametrize('mode', ['legacy', 'isolation_none', 'native'])
@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('locked', [False, True])
def test_chat_commit_and_cleanup(tmp_path, mode, pending, locked):
    conn = connect(tmp_path)
    memory = Memory.__new__(Memory)
    memory.conn = conn
    if mode == 'native':
        if not hasattr(conn, 'autocommit'):
            pytest.skip('native autocommit requires Python 3.12')
        conn.autocommit = True
    elif mode == 'isolation_none':
        conn.isolation_level = None
    conn.execute('PRAGMA busy_timeout=1')
    reader = sqlite3.connect(tmp_path / 'state.db')
    if locked:
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM chat_log').fetchall()
    if pending:
        conn.execute('BEGIN')
        conn.execute("INSERT INTO calendar_events(title,start) VALUES ('caller','2026-11-03')")
    try:
        if locked:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                memory.log_chat('question', 'answer')
            assert conn.in_transaction is pending
        else:
            memory.log_chat('question', 'answer')
            assert not conn.in_transaction
        reader.rollback()
        if conn.in_transaction:
            conn.execute('COMMIT')
        assert reader.execute('SELECT count(*) FROM chat_log').fetchone()[0] == (0 if locked else 2)
        assert reader.execute('SELECT count(*) FROM calendar_events').fetchone()[0] == int(pending)
    finally:
        reader.close()
        conn.close()


@pytest.mark.parametrize('locked', [False, True])
def test_native_always_transactional_connection(tmp_path, locked):
    conn = connect(tmp_path)
    if not hasattr(conn, 'autocommit'):
        pytest.skip('native autocommit requires Python 3.12')
    conn.autocommit = False
    conn.execute('PRAGMA busy_timeout=1')
    memory = Memory.__new__(Memory)
    memory.conn = conn
    reader = sqlite3.connect(tmp_path / 'state.db')
    if locked:
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM chat_log').fetchall()
    conn.execute("INSERT INTO calendar_events(title,start) VALUES ('caller','2026-11-03')")
    try:
        if locked:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                memory.log_chat('question', 'answer')
        else:
            memory.log_chat('question', 'answer')
        assert conn.in_transaction  # native False always opens a new transaction
        reader.rollback()
        conn.commit()
        assert reader.execute('SELECT count(*) FROM chat_log').fetchone()[0] == (0 if locked else 2)
        assert reader.execute('SELECT count(*) FROM calendar_events').fetchone()[0] == 1
    finally:
        reader.close()
        conn.close()
