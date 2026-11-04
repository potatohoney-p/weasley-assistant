"""Offline regressions for the battery's tool-specific counts and ordering."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from weasley_assistant.ops import scoring


def calls(*names):
    return [{"tool": name, "args": {"title": "Sam", "content": "Pikachu vegetarian",
                                      "to": "Raj"}} for name in names]


def case(case_id):
    return next(c for c in scoring.load_cases() if c["id"] == case_id)


@pytest.mark.parametrize("names", [
    ("create_event", "save_note", "search_web"),
    ("create_event", "create_event", "search_web"),
    ("create_event",) * 4,
])
def test_three_sessions_requires_exactly_three_event_calls(names):
    passed, why = scoring.check_case(case("exact-count-sessions"), calls(*names))
    assert not passed, why


def test_three_sessions_allows_unrelated_receipts():
    receipts = calls("list_events", "create_event", "create_event", "create_event", "save_note")
    assert scoring.check_case(case("exact-count-sessions"), receipts)[0]


@pytest.mark.parametrize("case_id,names", [
    ("pokemon-team", ("search_web", "save_note", "create_event")),
    ("pokemon-team", ("save_note", "create_event", "create_event")),
    ("pokemon-team", ("search_web", "save_note", "create_event", "create_event", "create_event")),
    ("worldcup-final", ("save_note", "send_message", "create_event")),
    ("worldcup-final", ("search_web", "send_message", "create_event")),
    ("remember-and-book", ("create_event", "search_web")),
    ("read-before-write", ("search_web", "create_event")),
    ("read-before-write", ("create_event", "list_events")),
    ("read-before-write", ("create_event", "list_events", "create_event")),
])
def test_multistep_cases_reject_substitutes_and_late_reads(case_id, names):
    passed, why = scoring.check_case(case(case_id), calls(*names))
    assert not passed, why


@pytest.mark.parametrize("case_id,names", [
    ("pokemon-team", ("save_note", "search_web", "search_web", "create_event", "create_event")),
    ("worldcup-final", ("save_note", "search_web", "send_message")),
    ("remember-and-book", ("save_note", "create_event")),
    ("read-before-write", ("search_web", "list_events", "search_web", "create_event")),
])
def test_multistep_cases_accept_complete_receipts(case_id, names):
    assert scoring.check_case(case(case_id), calls(*names))[0]


@pytest.mark.parametrize("bounds,names,passed", [
    ({"min": 2}, ("create_event",), False),
    ({"min": 2}, ("create_event", "create_event", "create_event"), True),
    ({"max": 1}, ("create_event", "create_event"), False),
    ({"max": 1}, ("create_event",), True),
    ({"min": 1, "max": 1}, ("create_event", "save_note"), True),
    ({"max": 0}, ("save_note",), True),
    ({"max": 0}, ("save_note", "create_event"), False),
])
def test_tool_count_bounds(bounds, names, passed):
    contract = {"expect_tool": names[0], "expect_tool_counts": {"create_event": bounds}}
    actual, why = scoring.check_case(contract, calls(*names))
    assert actual is passed, why
    if not passed:
        assert "create_event" in why


@pytest.mark.parametrize("names", [
    ("create_event",),
    ("create_event", "list_events", "create_event"),
])
def test_order_uses_first_call_not_a_later_matching_subsequence(names):
    contract = {"expect_tool": "create_event", "expect_tool_order": ["list_events", "create_event"]}
    passed, why = scoring.check_case(contract, calls(*names))
    assert not passed and "list_events" in why and "create_event" in why


def test_old_total_count_contract_remains_unchanged():
    contract = {"expect_tool": "create_event", "expect_min_tool_calls": 3}
    assert scoring.check_case(contract, calls("create_event", "save_note", "search_web"))[0]


@pytest.mark.parametrize("names,passed", [
    (("create_event", "search_web", "save_note"), False),
    (("create_event", "create_event", "create_event"), True),
])
def test_live_dataset_tier_uses_same_completion_contract(monkeypatch, tmp_path, names, passed):
    # Invoke the live test body with an offline assistant. No credentials or model calls.
    from evals.deterministic import test_tool_trigger

    assistant = SimpleNamespace(respond=lambda _: SimpleNamespace(tool_calls=calls(*names)))
    monkeypatch.setattr(test_tool_trigger, "make_assistant", lambda _: assistant)
    if passed:
        test_tool_trigger.test_dataset_case(case("exact-count-sessions"), tmp_path)
    else:
        with pytest.raises(AssertionError, match="create_event"):
            test_tool_trigger.test_dataset_case(case("exact-count-sessions"), tmp_path)


@pytest.mark.parametrize("case_id,names,passed", [
    ("exact-count-sessions", ("create_event", "search_web", "save_note"), False),
    ("exact-count-sessions", ("create_event", "create_event", "create_event"), True),
    ("remember-and-book", ("search_web", "create_event"), False),
    ("remember-and-book", ("save_note", "create_event"), True),
    ("read-before-write", ("create_event", "list_events", "create_event"), False),
    ("read-before-write", ("list_events", "create_event"), True),
])
def test_all_three_consumers_agree_offline(monkeypatch, tmp_path, case_id, names, passed):
    import tempfile

    from evals.deterministic import test_shootout, test_tool_trigger
    from weasley_assistant import app as app_module
    from weasley_assistant.ops import arena, compare_history

    contract = case(case_id)
    receipts = calls(*names)
    result = SimpleNamespace(tool_calls=receipts, iterations=2, reply="Synthetic response")
    homes = []

    def sandbox(**_):
        home = tmp_path / f"sandbox-{len(homes)}"
        home.mkdir()
        homes.append(home)
        return str(home)

    class Assistant:
        def __init__(self, **_):
            pass

        def respond(self, *_, **__):
            return result

    monkeypatch.setattr(tempfile, "mkdtemp", sandbox)
    monkeypatch.setattr(app_module, "WeasleyAssistant", Assistant)
    monkeypatch.setattr(test_tool_trigger, "make_assistant", lambda _: Assistant())
    monkeypatch.setattr(arena, "load_settings", lambda: SimpleNamespace(home=tmp_path / "history"))

    report = test_shootout.shootout.run_one("anthropic", "offline", [contract], trials=1)
    assert report["cases"][0]["passed"] is passed
    assert report["passed"] == int(passed)

    events = []
    arena.compare_stream(contract["input"], ["anthropic:offline"],
                         lambda kind, data: events.append((kind, data)))
    completions = [data["completion"] for kind, data in events if kind == "result"]
    assert len(completions) == 1
    assert completions[0]["passed"] is passed
    stored = compare_history.load_runs(tmp_path / "history")
    assert stored[0]["results"][0]["completion"]["passed"] is passed
    if passed:
        test_tool_trigger.test_dataset_case(contract, tmp_path)
    else:
        with pytest.raises(AssertionError):
            test_tool_trigger.test_dataset_case(contract, tmp_path)
    assert receipts == calls(*names)  # scoring must not rewrite execution evidence
