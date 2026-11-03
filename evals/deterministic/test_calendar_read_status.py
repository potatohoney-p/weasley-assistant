"""Calendar read failures are unknown schedules, never empty event lists."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from weasley_assistant.db import connect
from weasley_assistant.tools import calendar, google_calendar


@pytest.fixture
def source(tmp_path, monkeypatch):
    conn = connect(tmp_path)
    # Presence selects the source; no actual credential content is read.
    (tmp_path / "google-token.json").touch()
    state = SimpleNamespace(items=[], error=None, calls=[], credentials=object())
    monkeypatch.setattr(google_calendar, "_load_credentials", lambda *args: state.credentials)

    def execute():
        if state.error:
            raise state.error
        return {"items": state.items}

    def list_events(**kwargs):
        state.calls.append(kwargs)
        return SimpleNamespace(execute=execute)

    discovery = ModuleType("googleapiclient.discovery")
    discovery.build = lambda *args, **kwargs: SimpleNamespace(
        events=lambda: SimpleNamespace(list=list_events)
    )
    package = ModuleType("googleapiclient")
    package.discovery = discovery
    monkeypatch.setitem(sys.modules, "googleapiclient", package)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    state.read = calendar.make_list_tool(conn, tmp_path).fn
    state.conn, state.home = conn, tmp_path
    yield state
    conn.close()


def add_local(source):
    source.conn.execute(
        'INSERT INTO calendar_events (title, start, "end") VALUES (?, ?, ?)',
        ("Local lunch", "2026-11-04T12:00", "2026-11-04T13:00"),
    )
    source.conn.commit()


@pytest.mark.parametrize("local", [False, True])
def test_failed_google_read_is_visible_with_or_without_local_events(source, local):
    source.error = TimeoutError("calendar request timed out")
    if local:
        add_local(source)
    output = source.read("2026-11-04", "2026-11-04")
    assert "Calendar results are incomplete" in output
    assert "Google Calendar unavailable (TimeoutError: calendar request timed out)" in output
    assert "Checked: Google Calendar" not in output
    if local:
        assert "From assistant's local calendar" in output and "Local lunch" in output
    else:
        assert "No events found" in output and "assistant's local calendar" in output
    assert len(source.calls) == 1


@pytest.mark.parametrize("failure", ["missing-extra", "credentials"])
@pytest.mark.parametrize("local", [False, True])
def test_setup_failures_are_diagnostics_not_event_rows(source, monkeypatch, failure, local):
    if local:
        add_local(source)
    if failure == "missing-extra":
        monkeypatch.setitem(sys.modules, "googleapiclient.discovery", None)
        expected = google_calendar._INSTALL_HINT
    else:
        source.credentials = None
        expected = google_calendar._SETUP_HINT
    output = source.read("2026-11-04", "2026-11-04")
    assert "Calendar results are incomplete" in output
    assert expected in output
    assert "From Google Calendar:" not in output
    assert "Checked: Google Calendar" not in output
    assert not source.calls


@pytest.mark.parametrize("title", [
    "No Google Calendar events between planning dates",
    "Google Calendar unavailable troubleshooting",
])
def test_event_titles_cannot_be_confused_with_status_messages(source, title):
    source.items = [{"summary": title, "start": {"dateTime": "2026-11-04T09:00:00Z"}}]
    output = source.read("2026-11-04", "2026-11-04")
    assert "From Google Calendar:\n" + title + " | 2026-11-04T09:00:00Z" in output
    assert "Calendar results are incomplete" not in output


def test_successfully_empty_google_and_local_remain_empty(source):
    output = source.read("2026-11-04", "2026-11-05")
    assert output == (
        "No events found between 2026-11-04 and 2026-11-05. "
        "Checked: Google Calendar, assistant's local calendar."
    )
    assert len(source.calls) == 1


def test_successful_google_rows_precede_local_rows(source):
    add_local(source)
    source.items = [
        {"summary": "Remote breakfast", "start": {"dateTime": "2026-11-04T08:00:00Z"},
         "attendees": [{"email": "guest@example.com"}]},
        {"start": {"date": "2026-11-04"}},
    ]
    output = source.read("2026-11-04", "2026-11-04", 7)
    assert output.index("Remote breakfast") < output.index("Local lunch")
    assert "guest@example.com" in output and "(no title) | 2026-11-04" in output
    assert "incomplete" not in output
    assert source.calls[0]["maxResults"] == 7


@pytest.mark.parametrize("with_home", [False, True])
def test_unconfigured_google_is_not_consulted(source, with_home):
    (source.home / "google-token.json").unlink()
    output = calendar.make_list_tool(source.conn, source.home if with_home else None).fn()
    assert output == "No events found. Checked: assistant's local calendar."
    assert not source.calls


@pytest.mark.parametrize("kind", ["empty", "events", "error", "credentials"])
def test_plain_text_google_reader_keeps_its_contract(source, kind):
    if kind == "events":
        source.items = [{"summary": "Breakfast", "start": {"date": "2026-11-04"}}]
        expected = "Breakfast | 2026-11-04"
    elif kind == "error":
        source.error = RuntimeError("offline")
        expected = "Google Calendar unavailable (RuntimeError: offline)"
    elif kind == "credentials":
        source.credentials = None
        expected = google_calendar._SETUP_HINT
    else:
        expected = "No Google Calendar events between 2026-11-04 and 2026-11-04."
    assert google_calendar.list_google_events(source.home, "2026-11-04", "2026-11-04") == expected
