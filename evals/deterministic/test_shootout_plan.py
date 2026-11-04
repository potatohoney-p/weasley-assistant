"""Reject invalid benchmark plans before trials or report writes, offline."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from scripts import shootout as S
from weasley_assistant.ops import coding_eval as C


@pytest.fixture
def cli(monkeypatch, tmp_path):
    cases = [{"id": "first", "input": "one"}, {"id": "second", "input": "two"}]
    calls = []
    monkeypatch.setattr(S, "DATASET", cases)
    monkeypatch.setattr(C, "load_coding_cases", lambda: cases)
    monkeypatch.setattr(C, "pi_available", lambda: True)
    monkeypatch.setattr(S, "load_settings", lambda: SimpleNamespace(home=tmp_path))

    def tool_run(provider, model, selected, trials=1):
        calls.append((provider, model, [c["id"] for c in selected], trials))
        return {"provider": provider, "model": model, "passed": 0,
                "total": len(selected) * trials, "trials": trials, "cases": [],
                "cost_usd": 0, "avg_latency_s": 0}

    def coding_run(runs, selected, trials):
        calls.append((runs, [c["id"] for c in selected], trials))
        return "coding report"

    monkeypatch.setattr(S, "run_one", tool_run)
    monkeypatch.setattr(S, "coding_shootout", coding_run)
    return calls, tmp_path


def invoke(monkeypatch, coding, *args):
    monkeypatch.setattr(sys, "argv", ["shootout.py", *args, *(["--coding"] if coding else [])])
    S.main()


@pytest.mark.parametrize("coding", [False, True])
@pytest.mark.parametrize("trials", ["0", "-1", "-3", "1.5"])
def test_cli_rejects_bad_trial_count_before_work(cli, monkeypatch, capsys, coding, trials):
    calls, home = cli
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, coding, "anthropic:model", "--trials", trials)
    assert exc.value.code == 2
    assert "--trials" in capsys.readouterr().err
    assert calls == []
    assert not (home / "shootout").exists()


@pytest.mark.parametrize("coding", [False, True])
@pytest.mark.parametrize("selection", ["first,typo", "typo,first,missing", "typo"])
def test_cli_rejects_every_unknown_case(cli, monkeypatch, capsys, coding, selection):
    calls, home = cli
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, coding, "anthropic:model", "--cases", selection)
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "typo" in error and "first" in error and "second" in error
    if "missing" in selection:
        assert "missing" in error
    assert calls == []
    assert not (home / "shootout").exists()


@pytest.mark.parametrize("coding", [False, True])
@pytest.mark.parametrize("bad_spec", ["typo:model", ":model", "typo"])
def test_cli_validates_all_contestants_before_first_trial(
        cli, monkeypatch, capsys, coding, bad_spec):
    calls, home = cli
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, coding, "anthropic:model", bad_spec)
    assert exc.value.code == 2
    assert "provider" in capsys.readouterr().err
    assert calls == []
    assert not (home / "shootout").exists()


@pytest.mark.parametrize("coding", [False, True])
@pytest.mark.parametrize("selection,expected", [
    ("", ["first", "second"]),
    (" , , ", ["first", "second"]),
    (" second, first,second ", ["first", "second"]),
    (" second ", ["second"]),
])
def test_valid_case_selection_retains_order_and_deduplication(
        cli, monkeypatch, coding, selection, expected):
    calls, home = cli
    invoke(monkeypatch, coding, "anthropic", "--cases", selection, "--trials", "2")
    expected_call = (["anthropic"], expected, 2) if coding else ("anthropic", "", expected, 2)
    assert calls == [expected_call]
    assert len(list((home / "shootout").glob("*.md"))) == 1
    assert len(list((home / "shootout").glob("*.json"))) == (0 if coding else 1)


def test_model_ids_remain_opaque_and_default_trials_stay_three(cli, monkeypatch):
    calls, _ = cli
    invoke(monkeypatch, False, "openrouter:vendor/model:free")
    assert calls == [("openrouter", "vendor/model:free", ["first", "second"], 3)]


@pytest.mark.parametrize("coding", [False, True])
def test_empty_battery_is_rejected_before_work(cli, monkeypatch, coding):
    calls, home = cli
    monkeypatch.setattr(S, "DATASET", [])
    monkeypatch.setattr(C, "load_coding_cases", list)
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, coding, "anthropic")
    assert exc.value.code == 2
    assert calls == []
    assert not (home / "shootout").exists()


@pytest.mark.parametrize("runner", [S.run_one, S.coding_shootout])
@pytest.mark.parametrize("trials", [0, -1, 1.5, True, "2"])
def test_imported_runners_reject_invalid_trials(runner, trials, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid trial counts must not start model work")

    monkeypatch.setattr(C, "run_coding_case", unexpected)
    with pytest.raises(ValueError, match="trials.*positive integer"):
        if runner is S.run_one:
            runner("anthropic", "model", [], trials=trials)
        else:
            runner(["anthropic:model"], [{"id": "one", "input": "one"}], trials)


def test_coding_plan_is_checked_before_pi_availability(cli, monkeypatch, capsys):
    monkeypatch.setattr(C, "pi_available", lambda: pytest.fail("plan must be checked first"))
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, True, "anthropic", "--cases", "first,typo")
    assert exc.value.code == 2
    assert "typo" in capsys.readouterr().err
