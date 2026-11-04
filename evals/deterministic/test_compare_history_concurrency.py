"""Arena edits preserve races completed while the referee is still working.

All provider calls are synthetic. Real files exercise the persisted scoreboard;
event-controlled threads exercise two dashboard requests sharing one process.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from weasley_assistant.ops import arena
from weasley_assistant.ops import compare_history as ch


def _result(spec="test:a", quality=None):
    return {"spec": spec, "provider": "test", "model": spec.split(":")[1],
            "reply": "A useful answer", "tools": [], "quality": quality}


def _grade(score):
    return {"score": score, "reason": "Synthetic grade", "judge": "test:judge"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(arena, "load_settings", lambda: SimpleNamespace(home=tmp_path))
    ch.append_run(tmp_path, "original", [_result(), _result("test:b")], ts="same second")
    return tmp_path


def test_regrade_preserves_race_completed_during_grading(home, monkeypatch):
    def judge(*args, **kwargs):
        ch.append_run(home, "new race", [_result()], ts="later")
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    response = arena.compare_regrade({"spec": "test:a"})
    runs = ch.load_runs(home)
    assert [r["message"] for r in runs] == ["original", "new race"]
    assert runs[0]["results"][0]["quality"] == _grade(8)
    assert len(response["runs"]) == 2
    assert response["graded_results"][0]["quality"] == _grade(8)
    assert response["runs"][0]["message"] == "new race"


def test_regrade_does_not_resurrect_cleared_history(home, monkeypatch):
    def judge(*args, **kwargs):
        ch.clear(home)
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    assert arena.compare_regrade({"spec": "test:a"}) == {
        "runs": [], "aggregate": [], "graded_results": [],
    }
    assert ch.load_runs(home) == []
    assert not (home / "compare/history.jsonl").exists()


def test_regrade_does_not_resurrect_deleted_race(home, monkeypatch):
    def judge(*args, **kwargs):
        arena.compare_delete_run({"ts": "same second"})
        ch.append_run(home, "replacement", [_result()])
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert [r["message"] for r in ch.load_runs(home)] == ["replacement"]


def test_regrade_does_not_resurrect_trimmed_race(home, monkeypatch):
    monkeypatch.setattr(ch, "MAX_RUNS", 1)

    def judge(*args, **kwargs):
        ch.append_run(home, "replacement", [_result()])
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert [r["message"] for r in ch.load_runs(home)] == ["replacement"]


def test_concurrent_regrades_merge_different_cards(home, monkeypatch):
    inside = False

    def judge(*args, **kwargs):
        nonlocal inside
        if inside:
            return _grade(9)
        inside = True
        arena.compare_regrade({"spec": "test:b"})
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert [r["quality"] for r in ch.load_runs(home)[0]["results"]] == [_grade(8), _grade(9)]


def test_stale_regrade_does_not_replace_a_newer_grade(home, monkeypatch):
    inside = False

    def judge(*args, **kwargs):
        nonlocal inside
        if inside:
            return _grade(9)
        inside = True
        arena.compare_regrade({"spec": "test:a"})
        return _grade(3)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert ch.load_runs(home)[0]["results"][0]["quality"] == _grade(9)


def test_identical_replacement_is_not_the_deleted_race(home, monkeypatch):
    original = ch.load_runs(home)[0]

    def judge(*args, **kwargs):
        ch.clear(home)
        ch.append_run(home, original["message"], [_result(), _result("test:b")],
                      ts=original["ts"])
        return _grade(8)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert ch.load_runs(home)[0]["results"][0]["quality"] is None


def test_legacy_history_without_id_can_still_be_graded(home, monkeypatch):
    runs = ch.load_runs(home)
    runs[0].pop("id", None)
    ch.save_runs(home, runs)
    monkeypatch.setattr(arena.judge_mod, "judge_reply", lambda *a, **kw: _grade(7))
    arena.compare_regrade({"spec": "test:a"})
    assert ch.load_runs(home)[0]["results"][0]["quality"] == _grade(7)


def test_ambiguous_legacy_records_are_not_guessed(home, monkeypatch):
    run = ch.load_runs(home)[0]
    run.pop("id", None)
    ch.save_runs(home, [run, deepcopy(run)])
    monkeypatch.setattr(arena.judge_mod, "judge_reply", lambda *a, **kw: _grade(7))
    arena.compare_regrade({"spec": "test:a"})
    assert all(r["results"][0]["quality"] is None for r in ch.load_runs(home))


@pytest.mark.parametrize("first_action", ["append", "delete"])
def test_concurrent_edits_preserve_later_append(tmp_path, monkeypatch, first_action):
    original_load = ch.load_runs
    first_loaded, release_first, second_started = (threading.Event() for _ in range(3))
    first_thread = None

    def load(*args, **kwargs):
        runs = original_load(*args, **kwargs)
        if threading.get_ident() == first_thread:
            first_loaded.set()
            assert release_first.wait(5)
        return runs

    def first():
        nonlocal first_thread
        first_thread = threading.get_ident()
        if first_action == "append":
            ch.append_run(tmp_path, "first", [_result()])
        else:
            arena.compare_delete_run({"ts": "not present"})

    def second():
        second_started.set()
        ch.append_run(tmp_path, "second", [_result()])

    monkeypatch.setattr(ch, "load_runs", load)
    monkeypatch.setattr(arena, "load_settings", lambda: SimpleNamespace(home=tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert first_loaded.wait(5)
        b = pool.submit(second)
        assert second_started.wait(5)
        try:
            # Before the fix, the second write finishes against the stale first
            # snapshot. With serialization, it waits for the first operation.
            b.result(timeout=0.2)
        except TimeoutError:
            pass
        finally:
            release_first.set()
        a.result(timeout=5)
        b.result(timeout=5)
    expected = {"first", "second"} if first_action == "append" else {"second"}
    assert {r["message"] for r in original_load(tmp_path)} == expected


def test_failed_replace_preserves_old_bytes_and_cleans_staging(home, monkeypatch):
    path = home / "compare/history.jsonl"
    before = path.read_bytes()

    def fail(self, target):
        raise OSError("replacement denied")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replacement denied"):
        ch.append_run(home, "not committed", [_result()])
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["history.jsonl"]


def test_readers_see_complete_snapshot_before_publication(home, monkeypatch):
    path = home / "compare/history.jsonl"
    before = path.read_bytes()
    original_replace = Path.replace
    observed = []

    def replace(self, target):
        observed.append(path.read_bytes())
        assert self.parent == path.parent
        assert self.read_text(encoding="utf-8").count("\n") == 2
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    ch.append_run(home, "new race", [_result()])
    assert observed == [before]
    assert len(ch.load_runs(home)) == 2
    assert [p.name for p in path.parent.iterdir()] == ["history.jsonl"]


@pytest.mark.parametrize("failure", [OSError("write failed"), KeyboardInterrupt("interrupted")])
def test_failed_staging_preserves_previous_history(home, monkeypatch, failure):
    import tempfile

    path = home / "compare/history.jsonl"
    before = path.read_bytes()
    real_temp = tempfile.NamedTemporaryFile

    class BrokenWrite:
        def __init__(self, *args, **kwargs):
            self.file = real_temp(*args, **kwargs)
            self.name = self.file.name

        def __enter__(self):
            return self

        def write(self, text):
            self.file.write(text[:10])
            raise failure

        def __exit__(self, *args):
            self.file.close()

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", BrokenWrite)
    with pytest.raises(type(failure), match=str(failure)):
        ch.append_run(home, "failed", [_result()])
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["history.jsonl"]


def test_serialization_failure_keeps_old_history(home):
    path = home / "compare/history.jsonl"
    before = path.read_bytes()
    with pytest.raises(TypeError):
        ch.save_runs(home, [{"bad": object()}])
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["history.jsonl"]


def test_concurrent_race_batch_retains_unique_ids_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "MAX_RUNS", 12)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: ch.append_run(tmp_path, f"run {i}", [_result()], ts="same"),
                      range(40)))
    runs = ch.load_runs(tmp_path)
    assert len(runs) == 12
    assert len({r["message"] for r in runs}) == 12
    assert len({r["id"] for r in runs}) == 12
    assert all(r["ts"] == "same" for r in runs)


def test_failed_referee_keeps_scores_without_rewriting_file(home, monkeypatch):
    path = home / "compare/history.jsonl"
    before = path.stat().st_mtime_ns
    monkeypatch.setattr(arena.judge_mod, "judge_reply", lambda *a, **kw: None)
    response = arena.compare_regrade({})
    assert all(r["quality"] is None for r in response["graded_results"])
    assert path.stat().st_mtime_ns == before


def test_stale_grade_is_rejected_after_newer_grade_changes_back(home, monkeypatch):
    runs = ch.load_runs(home)
    runs[0]["results"][0]["quality"] = _grade(5)
    ch.save_runs(home, runs)
    nested_scores = iter([9, 5])
    inside = False

    def judge(*args, **kwargs):
        nonlocal inside
        if inside:
            return _grade(next(nested_scores))
        inside = True
        arena.compare_regrade({"spec": "test:a"})
        arena.compare_regrade({"spec": "test:a"})
        return _grade(3)

    monkeypatch.setattr(arena.judge_mod, "judge_reply", judge)
    arena.compare_regrade({"spec": "test:a"})
    assert ch.load_runs(home)[0]["results"][0]["quality"] == _grade(5)
