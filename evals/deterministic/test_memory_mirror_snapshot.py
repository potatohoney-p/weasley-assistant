"""A generated mirror must describe a single SQLite snapshot."""
from __future__ import annotations

import sqlite3

import pytest

from weasley_assistant.config import Settings
from weasley_assistant.db import connect
from weasley_assistant.memory import Memory


class BetweenReads(sqlite3.Connection):
    before_episodes = None

    def execute(self, sql, parameters=(), /):
        if sql.lstrip().startswith("SELECT happened_at, summary") and self.before_episodes:
            callback, self.before_episodes = self.before_episodes, None
            callback()
        return super().execute(sql, parameters)


@pytest.fixture
def memory(tmp_path):
    seed = connect(tmp_path)
    seed.execute("PRAGMA journal_mode=WAL")
    seed.execute("INSERT INTO facts(subject, content) VALUES('person', 'fact-old')")
    seed.execute("INSERT INTO episodes(happened_at, summary) VALUES('2026-10-28', 'episode-old')")
    seed.commit()
    seed.close()
    conn = sqlite3.connect(tmp_path / "state.db", factory=BetweenReads)
    conn.row_factory = sqlite3.Row
    obj = Memory(conn, Settings(home=tmp_path), client=None)
    yield obj
    conn.close()


@pytest.mark.parametrize("replacement", ["update", "delete", "insert"])
def test_wal_writer_between_table_reads_never_mixes_snapshots(memory, replacement):
    def write():
        with sqlite3.connect(memory.settings.home / "state.db") as other:
            if replacement == "update":
                other.execute("UPDATE facts SET content='fact-new'")
                other.execute("UPDATE episodes SET summary='episode-new'")
            elif replacement == "delete":
                other.execute("DELETE FROM facts")
                other.execute("DELETE FROM episodes")
            else:
                other.execute("INSERT INTO facts(subject,content) VALUES('other','fact-new')")
                other.execute("INSERT INTO episodes(happened_at,summary) VALUES('2026-10-29','episode-new')")
    memory.conn.before_episodes = write
    memory.export_markdown()
    first = (memory.settings.home / "MEMORY.md").read_text()
    assert "fact-old" in first and "episode-old" in first
    assert "fact-new" not in first and "episode-new" not in first
    assert not memory.conn.in_transaction
    memory.export_markdown()
    second = (memory.settings.home / "MEMORY.md").read_text()
    if replacement == "delete":
        assert "fact-old" not in second and "episode-old" not in second
    else:
        assert "fact-new" in second and "episode-new" in second


def test_existing_transaction_is_not_committed_and_own_pending_changes_visible(memory):
    memory.conn.execute("UPDATE facts SET content='pending'")
    memory.export_markdown()
    assert memory.conn.in_transaction
    assert "pending" in (memory.settings.home / "MEMORY.md").read_text()
    memory.conn.rollback()
    assert memory.conn.execute("SELECT content FROM facts").fetchone()[0] == "fact-old"


@pytest.mark.parametrize("outer", [False, True])
def test_read_failure_preserves_file_and_transaction_ownership(memory, outer):
    target = memory.settings.home / "MEMORY.md"
    target.write_text("prior complete mirror")
    if outer:
        memory.conn.execute("UPDATE facts SET content='pending'")
    failure = RuntimeError("read interrupted")
    memory.conn.before_episodes = lambda: (_ for _ in ()).throw(failure)
    with pytest.raises(RuntimeError) as caught:
        memory.export_markdown()
    assert caught.value is failure
    assert memory.conn.in_transaction is outer
    assert target.read_text() == "prior complete mirror"
    memory.conn.rollback()
    memory.export_markdown()
    assert "episode-old" in target.read_text()


def test_snapshot_released_before_file_replacement(memory, monkeypatch):
    import os
    original = os.replace
    def replace(src, dst):
        assert not memory.conn.in_transaction
        original(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    memory.export_markdown()


def test_nested_same_named_savepoint_keeps_outer_transaction(memory):
    memory.conn.execute("SAVEPOINT memory_mirror")
    memory.conn.execute("UPDATE facts SET content='pending'")
    memory.export_markdown()
    memory.conn.execute("ROLLBACK TO memory_mirror")
    memory.conn.execute("RELEASE memory_mirror")
    assert memory.conn.execute("SELECT content FROM facts").fetchone()[0] == "fact-old"
