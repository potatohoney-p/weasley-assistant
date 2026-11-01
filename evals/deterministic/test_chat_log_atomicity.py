"""A failed exchange must never leave half a turn for a later commit."""

import json
import sqlite3

import pytest

from weasley_assistant.db import connect
from weasley_assistant.memory import Memory


@pytest.fixture
def memory(tmp_path):
    conn = connect(tmp_path)
    # log_chat and session_history depend only on the connection, not providers.
    memory = Memory.__new__(Memory)
    memory.conn = conn
    yield memory
    conn.close()


@pytest.mark.parametrize("meta", [{"bad": object()}, {"bad": {1, 2}}])
def test_invalid_metadata_does_not_leave_orphan(memory, meta):
    with pytest.raises(TypeError):
        memory.log_chat("failed", "answer", meta=meta)
    memory.log_chat("next", "ok")
    assert memory.session_history("default") == [("next", "ok")]
    assert memory.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 2


@pytest.mark.parametrize("action", ["ABORT", "ROLLBACK"])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("autocommit", [False, True])
def test_assistant_insert_failure_rolls_back_exchange(memory, pending, autocommit, action):
    conn = memory.conn
    if autocommit:
        conn.isolation_level = None
    conn.execute("CREATE TABLE unrelated (value TEXT)")
    conn.commit()
    if pending:
        conn.execute("INSERT INTO unrelated VALUES ('preserve')")
    # A real SQLite failure on the second insert, not a mocked connection.
    conn.execute(f"""CREATE TEMP TRIGGER reject_answer BEFORE INSERT ON chat_log
                    WHEN NEW.role = 'assistant' AND NEW.content = 'fail'
                    BEGIN SELECT RAISE({action}, 'rejected answer'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="rejected answer"):
        memory.log_chat("orphan", "fail")
    assert conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 0
    # A transaction-wide SQLite ROLLBACK also discards earlier pending work;
    # ABORT must leave it alone. Autocommit work was already durable.
    if pending and not autocommit and action == "ABORT":
        assert conn.in_transaction
    expected = int(pending and (autocommit or action == "ABORT"))
    assert conn.execute("SELECT COUNT(*) FROM unrelated").fetchone()[0] == expected
    memory.log_chat("later", "success")
    assert memory.session_history("default") == [("later", "success")]
    assert conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 2


def test_success_retains_session_source_metadata_and_durability(memory):
    memory.log_chat("hello", "reply", "one", "voice", {"tools": ["search"], "ms": 5})
    memory.log_chat("other", "answer", "two", "cli")
    assert memory.session_history("one") == [("hello", "reply")]
    assert memory.session_history("two") == [("other", "answer")]
    rows = memory.conn.execute("SELECT role, source, meta FROM chat_log ORDER BY id").fetchall()
    assert rows[0]["source"] == rows[1]["source"] == "voice"
    assert rows[0]["meta"] is None
    assert json.loads(rows[1]["meta"]) == {"tools": ["search"], "ms": 5}
    assert rows[3]["meta"] is None
    assert not memory.conn.in_transaction
    path = memory.conn.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as reader:
        assert reader.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 4


def test_statement_failure_preserves_explicit_outer_transaction(memory):
    conn = memory.conn
    conn.isolation_level = None
    conn.execute("BEGIN")
    conn.execute("INSERT INTO chat_log (role, content) VALUES ('user', 'earlier')")
    with pytest.raises(sqlite3.ProgrammingError):
        memory.log_chat("orphan", object())  # unsupported parameter in second insert
    assert conn.in_transaction
    assert conn.execute("SELECT content FROM chat_log").fetchall()[0][0] == "earlier"
    assert conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 1
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 0


def test_first_insert_failure_cleans_up_savepoint(memory):
    with pytest.raises(sqlite3.ProgrammingError):
        memory.log_chat(object(), "reply")
    assert not memory.conn.in_transaction
    assert memory.conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 0
    memory.log_chat("retry", "success")
    assert memory.session_history("default") == [("retry", "success")]


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("autocommit", [False, True])
def test_commit_busy_removes_failed_exchange_before_retry(memory, pending, autocommit):
    conn = memory.conn
    if autocommit:
        conn.isolation_level = None
    conn.execute("PRAGMA busy_timeout=1")
    conn.execute("CREATE TABLE unrelated (value TEXT)")
    conn.commit()
    if pending:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO unrelated VALUES ('preserve')")
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    reader = sqlite3.connect(path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM chat_log").fetchall()
        # A reader allows both INSERTs but prevents the final durable commit.
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            memory.log_chat("failed", "answer")
        assert memory.session_history("default") == []
        assert conn.in_transaction is pending
        assert conn.execute("SELECT COUNT(*) FROM unrelated").fetchone()[0] == int(pending)
    finally:
        reader.rollback()
        reader.close()
    memory.log_chat("retry", "success")
    assert memory.session_history("default") == [("retry", "success")]
    with sqlite3.connect(path) as observer:
        assert observer.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0] == 2


@pytest.mark.parametrize("pending", [False, True])
def test_deferred_constraint_commit_failure_preserves_prior_work(memory, pending):
    conn = memory.conn
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (parent_id INTEGER REFERENCES parent(id)
                            DEFERRABLE INITIALLY DEFERRED);
        CREATE TABLE unrelated (value TEXT);
        CREATE TRIGGER deferred_failure AFTER INSERT ON chat_log
        WHEN NEW.content = 'failed'
        BEGIN INSERT INTO child VALUES (99); END;
    """)
    if pending:
        conn.execute("INSERT INTO unrelated VALUES ('preserve')")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        memory.log_chat("failed", "answer")
    assert memory.session_history("default") == []
    assert conn.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM unrelated").fetchone()[0] == int(pending)
    assert conn.in_transaction is pending
    memory.log_chat("retry", "success")
    assert memory.session_history("default") == [("retry", "success")]
