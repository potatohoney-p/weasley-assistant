"""Keep a model's edits to the answer separate from edits to its seeded verifier."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from weasley_assistant.ops import coding_eval as ce


@pytest.fixture(params=["direct", "stream"])
def runner(request, monkeypatch, tmp_path):
    """Run a real offline Python child instead of pi, then the real verifier."""
    monkeypatch.setattr(ce.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
    real_run, real_popen = subprocess.run, subprocess.Popen
    code = {"source": "pass"}
    homes = []
    launches = []
    verifications = []
    real_mkdtemp = ce.tempfile.mkdtemp

    monkeypatch.setattr(ce.tempfile, "tempdir", str(tmp_path))

    def home(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        homes.append(Path(path))
        return path

    def command(argv):
        if "--provider" in argv:
            launches.append(argv)
            return [sys.executable, "-c", code["source"]]
        return argv

    def run(argv, **kwargs):
        if "--provider" not in argv:
            verifications.append(argv)
        return real_run(command(argv), **kwargs)

    def popen(argv, **kwargs):
        return real_popen(command(argv), **kwargs)

    monkeypatch.setattr(ce.tempfile, "mkdtemp", home)
    monkeypatch.setattr(ce.subprocess, "run", run)
    monkeypatch.setattr(ce.subprocess, "Popen", popen)

    def invoke(case, source="pass"):
        code["source"] = source
        lines = []
        if request.param == "direct":
            result = ce.run_coding_case("anthropic", "offline", case)
        else:
            kwargs = {}
            if "unchanged_files" in case:
                kwargs["unchanged_files"] = case["unchanged_files"]
            result = ce.run_coding_stream(
                "anthropic", "offline", case["input"], case.get("files"),
                case.get("verify"), lines.append, **kwargs)
        return result, lines, homes[-1]

    invoke.launches = launches
    invoke.verifications = verifications
    return invoke


@pytest.fixture
def case():
    return {
        "id": "bugfix", "input": "Fix calc.py; do not modify check.py.",
        "files": {
            "calc.py": "def add(a, b):\n    return a - b\n",
            "check.py": "from calc import add\nassert add(2, 3) == 5\n",
        },
        "verify": ["{python}", "check.py"],
        "unchanged_files": ["check.py"],
    }


FIX = "from pathlib import Path; Path('calc.py').write_text('def add(a, b): return a + b\\n')"


@pytest.mark.parametrize("edit", [
    "Path('check.py').write_text('pass\\n')",
    "Path('check.py').unlink()",
    "Path('check.py').unlink(); Path('check.py').mkdir()",
    "Path('check.py').write_bytes(b'\\xff')",
    "Path('check.py').write_text(Path('check.py').read_text().replace('== 5', '!= 5'))",
])
def test_changed_verifier_fails_before_execution(runner, case, edit):
    case["verify"] = ["{python}", "-c", "from pathlib import Path; Path('verified').touch()"]
    (passed, why, _), lines, home = runner(case, "from pathlib import Path; " + edit)
    assert passed is False
    assert "unchanged file" in why and "check.py" in why
    assert not (home / "verified").exists()
    assert runner.verifications == []
    if lines:
        assert lines[-1] == f"[verify] FAILED — {why}"


def test_bundled_bugfix_cannot_pass_by_replacing_its_checker(runner):
    case = copy.deepcopy(next(c for c in ce.load_coding_cases() if c["id"] == "code-bugfix"))
    (passed, why, _), _, _ = runner(case, "from pathlib import Path; Path('check.py').write_text('pass')")
    assert passed is False and "check.py" in why


def test_correct_fix_preserves_checker_and_passes(runner, case):
    (passed, why, _), _, _ = runner(case, FIX)
    assert passed is True and why == "tests pass"
    assert len(runner.verifications) == 1


def test_wrong_code_still_fails_the_unchanged_checker(runner, case):
    (passed, why, _), _, _ = runner(case)
    assert passed is False and "AssertionError" in why


def test_even_correct_fix_cannot_change_the_checker(runner, case):
    (passed, why, _), _, _ = runner(case, FIX + "; Path('check.py').write_text('pass')")
    assert passed is False and "unchanged file" in why


def test_checker_cannot_modify_itself_during_verification(runner, case):
    case["files"]["check.py"] = "from pathlib import Path\nPath('check.py').write_text('pass')\n"
    (passed, why, _), _, _ = runner(case)
    assert passed is False and "unchanged file" in why


def test_empty_or_absent_contract_preserves_existing_behavior(runner, case):
    case.pop("unchanged_files")
    (passed, why, _), _, _ = runner(case, "from pathlib import Path; Path('check.py').write_text('pass')")
    assert passed is True and why == "tests pass"


def test_symlink_with_identical_content_is_not_an_unchanged_file(runner, case, tmp_path):
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path / "target")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    (passed, why, _), _, _ = runner(case, FIX + "; "
        "Path('check.py').rename('old-check.py'); Path('check.py').symlink_to('old-check.py')")
    assert passed is False and "unchanged file" in why


@pytest.mark.parametrize("names", ["check.py", ["missing.py"], [None], [7]])
def test_invalid_contract_is_rejected_before_model_work(runner, case, names):
    case["unchanged_files"] = names
    with pytest.raises(ValueError, match="unchanged_files"):
        runner(case)
    assert runner.launches == []


def test_multiple_and_repeated_protected_names(runner, case):
    case["files"]["readme.txt"] = "Keep this café note.\n"
    case["unchanged_files"] = ["check.py", "readme.txt", "check.py"]
    (passed, why, _), _, _ = runner(case, FIX)
    assert passed is True and why == "tests pass"


def test_missing_verifier_still_enforces_the_file_contract(runner, case):
    case.pop("verify")
    (passed, why, _), _, _ = runner(case, "from pathlib import Path; Path('check.py').write_text('pass')")
    assert passed is False and "unchanged file" in why


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "C:/outside.py", "C:outside.py", "dir\\check.py"])
def test_protected_paths_must_stay_relative_before_seeding(tmp_path, name):
    with pytest.raises(ValueError, match="unchanged_files"):
        ce._seed_files(tmp_path, {name: "pass"}, [name])
    assert list(tmp_path.iterdir()) == []


def test_parent_symlink_replacement_is_not_unchanged(tmp_path):
    workdir = tmp_path / "work"
    nested = workdir / "nested"
    nested.mkdir(parents=True)
    snapshots = ce._seed_files(workdir, {"nested/check.py": "pass"}, ["nested/check.py"])
    nested.rename(workdir / "moved")
    try:
        nested.symlink_to(workdir / "moved", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    assert "unchanged file modified" in ce._unchanged_error(workdir, snapshots)


def test_regular_file_read_error_fails_closed(tmp_path, monkeypatch):
    snapshots = ce._seed_files(tmp_path, {"check.py": "pass"}, ["check.py"])

    def fail(_self):
        raise PermissionError("cannot read")

    monkeypatch.setattr(Path, "read_bytes", fail)
    assert ce._unchanged_error(tmp_path, snapshots) == "unchanged file missing or unreadable: check.py"


def test_no_file_contract_accepts_empty_snapshot(tmp_path):
    assert ce._seed_files(tmp_path, None, []) == {}
    assert ce._unchanged_error(tmp_path, {}) is None
