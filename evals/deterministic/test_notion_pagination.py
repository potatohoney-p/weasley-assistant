"""Bounded offline pagination regressions; no Notion credentials or network."""
from types import SimpleNamespace

import pytest

from weasley_assistant.memory.episodic.notion_store import NotionEpisodeStore


def scripted_store(responses):
    store = object.__new__(NotionEpisodeStore)
    store.data_source_id = "episodes"
    calls = []

    def query(**kwargs):
        calls.append(kwargs)
        if len(calls) > len(responses):
            raise AssertionError("pagination continued without progress")
        return responses[len(calls) - 1]

    store.client = SimpleNamespace(data_sources=SimpleNamespace(query=query))
    return store, calls


@pytest.mark.parametrize("cursor", [None, "", "  ", 0, [], {}])
def test_more_pages_require_nonblank_string_cursor(cursor):
    store, calls = scripted_store([
        {"results": [{"id": "first"}], "has_more": True, "next_cursor": cursor},
    ])
    with pytest.raises(ValueError, match="pagination cursor"):
        store._query_all()
    assert calls == [{"data_source_id": "episodes"}]


@pytest.mark.parametrize("cursors", [["a", "a"], ["a", "b", "a"]])
def test_repeated_or_cyclic_cursor_stops_before_repeated_request(cursors):
    store, calls = scripted_store([
        {"results": [{"id": str(i)}], "has_more": True, "next_cursor": cursor}
        for i, cursor in enumerate(cursors)
    ])
    with pytest.raises(ValueError, match="pagination cursor"):
        store._query_all()
    assert len(calls) == len(cursors)
    assert len({call.get("start_cursor") for call in calls}) == len(calls)


def test_valid_pages_keep_order_even_when_middle_page_is_empty():
    store, calls = scripted_store([
        {"results": [{"id": "one"}], "has_more": True, "next_cursor": "opaque a"},
        {"results": [], "has_more": True, "next_cursor": "opaque b"},
        {"results": [{"id": "two"}], "has_more": False, "next_cursor": "opaque b"},
    ])
    assert store._query_all() == [{"id": "one"}, {"id": "two"}]
    assert calls == [
        {"data_source_id": "episodes"},
        {"data_source_id": "episodes", "start_cursor": "opaque a"},
        {"data_source_id": "episodes", "start_cursor": "opaque b"},
    ]


def test_empty_terminal_page_needs_no_cursor():
    store, calls = scripted_store([{"results": [], "has_more": False}])
    assert store._query_all() == []
    assert len(calls) == 1


@pytest.mark.parametrize("method", ["recent", "search", "list"])
def test_public_readers_do_not_return_partial_results(method):
    store, calls = scripted_store([{"results": [], "has_more": True}])
    args = ("demo",) if method == "search" else ()
    with pytest.raises(ValueError, match="pagination cursor"):
        getattr(store, method)(*args)
    assert len(calls) == 1
