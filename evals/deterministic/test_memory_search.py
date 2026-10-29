"""DETERMINISTIC EVAL — Unicode keyword retrieval against real SQLite FTS5."""

import pytest

from weasley_assistant.db import connect
from weasley_assistant.memory.episodic.store import SqliteEpisodeStore
from weasley_assistant.memory.semantic.store import SqliteFactStore


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path)
    yield connection
    connection.close()


@pytest.mark.parametrize("query", ["서울", "北京", "東京", "café", "프로젝트42", "abc서울"])
@pytest.mark.parametrize("field", ["subject", "content"])
@pytest.mark.parametrize("with_ids", [False, True])
def test_facts_find_unicode_keywords(conn, query, field, with_ids):
    facts = SqliteFactStore(conn)
    subject = query if field == "subject" else "target"
    content = query if field == "content" else "target"
    facts.add(subject, content)
    target_id = facts.list()[0]["id"]
    # A newer unrelated row catches search_with_ids falling back to list().
    facts.add("unrelated", "other memory")

    if with_ids:
        assert facts.search_with_ids(query, top_k=1) == [
            {"id": target_id, "subject": subject, "content": content}
        ]
    else:
        assert facts.search(query, top_k=1) == [f"[{subject}] {content}"]


@pytest.mark.parametrize("query", ["서울", "北京", "東京", "café", "프로젝트42", "abc서울"])
def test_episodes_find_unicode_keywords_instead_of_recent_rows(conn, query):
    episodes = SqliteEpisodeStore(conn)
    episodes.add(query, "2026-01-01")
    episodes.add("unrelated recent event", "2026-01-02")

    assert episodes.search(query, top_k=1) == [f"(2026-01-01) {query}"]


def test_missing_unicode_keywords_do_not_return_unrelated_memory(conn):
    facts = SqliteFactStore(conn)
    episodes = SqliteEpisodeStore(conn)
    facts.add("unrelated", "other memory")
    episodes.add("unrelated event", "2026-01-02")

    assert facts.search("서울") == []
    assert facts.search_with_ids("서울") == []
    assert episodes.search("서울") == []


@pytest.mark.parametrize("query", [
    '"서울" NOT (부산)*',
    "NEAR(서울, 부산)",
    "subject:서울 -부산",
    "서울'; DROP TABLE facts; -- 부산",
    "서울_부산",
])
def test_user_syntax_remains_literal_keywords_joined_by_or(conn, query):
    facts = SqliteFactStore(conn)
    facts.add("first", "서울")
    facts.add("second", "부산")
    facts.add("unrelated", "other memory")

    # Quotes, operators, column filters and punctuation must not alter MATCH
    # syntax, restrict it to one column, or turn it into a prefix query.
    assert set(facts.search(query)) == {"[first] 서울", "[second] 부산"}
    assert len(facts.list()) == 3


def test_ascii_case_numbers_and_or_behavior_remain_supported(conn):
    facts = SqliteFactStore(conn)
    facts.add("first", "alpha")
    facts.add("second", "42")
    facts.add("unrelated", "other memory")

    assert set(facts.search('ALPHA, "alpha"_42!')) == {"[first] alpha", "[second] 42"}


def test_fts_operators_are_searchable_as_words(conn):
    facts = SqliteFactStore(conn)
    facts.add("literal", "AND OR NOT NEAR")
    facts.add("unrelated", "other memory")

    assert facts.search("AND OR NOT NEAR") == ["[literal] AND OR NOT NEAR"]


def test_unicode_retrieval_still_requires_whole_tokens(conn):
    facts = SqliteFactStore(conn)
    facts.add("target", "서울역")

    # unicode61 indexes contiguous CJK text as a token; it does not segment
    # words or perform substring/prefix matching.
    assert facts.search("서울") == []
    assert facts.search("서울*") == []
    assert facts.search("서울역") == ["[target] 서울역"]


@pytest.mark.parametrize("query", ["", '"*():!_😀', "a 1", "집 山"])
def test_empty_or_short_queries_keep_existing_fallbacks(conn, query):
    facts = SqliteFactStore(conn)
    episodes = SqliteEpisodeStore(conn)
    facts.add("existing", "stored memory")
    episodes.add("stored event", "2026-01-01")

    assert facts.search(query) == []
    assert facts.search_with_ids(query) == facts.list()
    assert episodes.search(query) == episodes.recent()
