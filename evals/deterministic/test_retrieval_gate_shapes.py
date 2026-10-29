"""Malformed model decisions must not crash real memory retrieval or lose facts."""
from __future__ import annotations

import json

import pytest

from evals.helpers import ScriptedClient, make_assistant, response, text_block
from weasley_assistant.memory.retrieval_gate import should_retrieve

MESSAGE = 'What does Alex prefer?'
FACT = 'Alex prefers morning meetings.'
INVALID = [
    {'retrieve': True, 'query': value} for value in (None, [], {}, 7, False, '', '  \t ')
] + [
    {'retrieve': value, 'query': 'Alex'} for value in ('false', 'true', 0, 1, None, [])
] + [
    {'retrieve': True, 'query': 'Alex', 'reason': value} for value in (None, [], {}, 1)
] + [{'retrieve': False, 'query': None}, {'retrieve': False, 'query': '', 'reason': {}}]


class RecordingClient(ScriptedClient):
    def __init__(self, decision):
        super().__init__([response([text_block(json.dumps(decision))]),
                          response([text_block('Morning meetings.')])])
        self.requests = []

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return super()._create(**kwargs)


@pytest.mark.parametrize('decision', INVALID)
def test_invalid_gate_fails_open_through_real_sqlite_turn(tmp_path, decision):
    client = RecordingClient(decision)
    app = make_assistant(tmp_path / 'home', client=client, graph_workflows=False)
    app.memory.facts.add('alex', FACT)
    events = []
    try:
        result = app.respond(MESSAGE, observer=lambda k, ev: events.append((k, ev)))
        assert result.reply == 'Morning meetings.'
        assert len(client.requests) == 2, 'no extra model retry'
        assert FACT in client.requests[1]['system']
        gate_event = next(ev for kind, ev in events if kind == 'gate')
        assert gate_event['decision'] == 'retrieve'
        assert isinstance(gate_event['reason'], str)
        assert 'failed open' in gate_event['reason']
        assert app.conn.execute('SELECT COUNT(*) FROM chat_log').fetchone()[0] == 2
        assert app.conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0] == 1
    finally:
        app.close()
        app.conn.close()


@pytest.mark.parametrize('decision,expected', [
    ({'retrieve': True, 'query': 'Alex', 'reason': 'personal'}, (True, 'Alex', 'personal')),
    ({'retrieve': True}, (True, MESSAGE, '')),
    ({'retrieve': True, 'query': '  서울 Alex  ', 'extra': 'ignored'},
     (True, '  서울 Alex  ', '')),
    ({'retrieve': False, 'query': '', 'reason': 'general'}, (False, '', 'general')),
    ({}, (False, MESSAGE, '')),
    ({'reason': 'omitted decision'}, (False, MESSAGE, 'omitted decision')),
])
def test_valid_and_legacy_optional_fields_preserved(decision, expected):
    client = ScriptedClient([response([text_block(json.dumps(decision))])])
    assert should_retrieve(client, 'synthetic', MESSAGE) == expected


def test_valid_false_never_searches_stores(tmp_path, monkeypatch):
    client = RecordingClient({'retrieve': False, 'query': '', 'reason': 'general'})
    app = make_assistant(tmp_path / 'home', client=client)
    def unexpected(*args, **kwargs):
        raise AssertionError('skip decision must not query memory')
    monkeypatch.setattr(app.memory.facts, 'search', unexpected)
    monkeypatch.setattr(app.memory.episodes, 'search', unexpected)
    try:
        assert app.respond(MESSAGE).reply == 'Morning meetings.'
        assert len(client.requests) == 2
    finally:
        app.close()
        app.conn.close()
