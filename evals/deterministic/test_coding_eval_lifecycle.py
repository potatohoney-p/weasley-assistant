"""Coding sandboxes and their direct child processes belong to one evaluation."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from weasley_assistant.ops import coding_eval as ce


@pytest.fixture
def sandboxes(tmp_path, monkeypatch):
    paths = []
    mkdtemp = tempfile.mkdtemp

    def allocate(suffix=None, prefix=None, dir=None):
        path = Path(mkdtemp(suffix=suffix, prefix=prefix, dir=tmp_path))
        paths.append(path)
        return str(path)

    monkeypatch.setattr(ce.tempfile, "mkdtemp", allocate)
    monkeypatch.setattr(ce.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(ce, "_key_for", lambda provider: "test-only")
    yield paths
    # Also clean up after the regression is run against unmodified main.
    for path in paths:
        ce.shutil.rmtree(path, ignore_errors=True)


def run(kind, *, verify=None, files=None, on_line=lambda line: None):
    if kind == "stream":
        return ce.run_coding_stream("anthropic", "test", "task", files, verify, on_line)
    return ce.run_coding_case("anthropic", "test", {
        "input": "task", "files": files, "verify": verify,
    })


@pytest.mark.parametrize("kind", ["case", "stream"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_verifier_uses_live_workspace_then_removes_it(kind, exit_code, sandboxes):
    verifier = ["{python}", "-c", (
        "from pathlib import Path; import sys; "
        "assert Path('seed.txt').read_text() == 'seed'; "
        "Path('generated.txt').write_text('output'); "
        f"sys.exit({exit_code})"
    )]
    passed, _, _ = run(kind, files={"seed.txt": "seed"}, verify=verifier)
    assert passed is (exit_code == 0)
    assert len(sandboxes) == 1
    assert not sandboxes[0].exists()


@pytest.mark.parametrize("kind", ["case", "stream"])
def test_no_verifier_reclaims_workspace_without_changing_verdict(kind, sandboxes):
    passed, _, _ = run(kind, files={"seed.txt": "seed"})
    assert passed is (None if kind == "stream" else True)
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


@pytest.mark.parametrize("kind", ["case", "stream"])
def test_seed_failure_reclaims_workspace(kind, sandboxes):
    with pytest.raises(FileNotFoundError):
        run(kind, files={"first.txt": "written", "missing/second.txt": "fails"})
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


@pytest.mark.parametrize("kind", ["case", "stream"])
def test_launch_failure_reclaims_workspace(kind, sandboxes, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected launch failure")

    monkeypatch.setattr(ce.subprocess, "Popen" if kind == "stream" else "run", fail)
    passed, why, _ = run(kind)
    assert passed is False and "injected launch failure" in why
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


@pytest.mark.parametrize("kind", ["case", "stream"])
@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_verifier_failure_reclaims_workspace(kind, failure, sandboxes, monkeypatch):
    def fail(verify, workdir):
        assert workdir.exists()
        if failure == "timeout":
            raise subprocess.TimeoutExpired(verify, 120)
        raise OSError("injected verify failure")

    monkeypatch.setattr(ce, "_run_verify", fail)
    if failure == "timeout":
        passed, why, _ = run(kind, verify=["fake-verifier"])
        assert passed is False and why == "verify timed out"
    else:
        with pytest.raises(OSError, match="injected verify failure"):
            run(kind, verify=["fake-verifier"])
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


def test_nonstream_timeout_reclaims_workspace(sandboxes, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300)

    monkeypatch.setattr(ce.subprocess, "run", timeout)
    passed, why, _ = run("case")
    assert passed is False and why == "pi timed out after 300s"
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


def test_initial_observer_failure_reclaims_workspace(sandboxes):
    def fail(line):
        raise ValueError("observer failed before launch")

    with pytest.raises(ValueError, match="observer failed before launch"):
        run("stream", on_line=fail)
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_stream_observer_failure_reaps_child_before_removing_workspace(
    error_type, sandboxes, monkeypatch,
):
    popen = subprocess.Popen
    children = []
    verified = []

    def launch(*args, **kwargs):
        child = popen([sys.executable, "-u", "-c", (
            "import time; print('ready', flush=True); time.sleep(60)"
        )], **kwargs)
        children.append(child)
        return child

    def fail(line):
        if line == "ready":
            raise error_type("observer interrupted")

    monkeypatch.setattr(ce.subprocess, "Popen", launch)
    monkeypatch.setattr(ce, "_run_verify", lambda *args: verified.append(True))
    try:
        with pytest.raises(error_type, match="observer interrupted"):
            run("stream", on_line=fail, verify=["never-run"])
        assert len(children) == 1
        assert children[0].returncode is not None  # wait(), not just kill(), has completed
        assert children[0].stdout.closed
        assert not sandboxes[0].exists()
        assert not verified
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()


def test_stream_watchdog_reaps_child_and_cleans_sandbox(sandboxes, monkeypatch):
    popen = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(ce.subprocess, "Popen", launch)
    # Keep the existing unscored verdict: this change owns resource lifetime,
    # not the scoring policy for a watchdog-terminated, free-form run.
    passed, why, _ = ce.run_coding_stream(
        "anthropic", "test", "task", None, None, lambda line: None, timeout=0.05,
    )
    assert passed is None and why == "ran (no test)"
    assert len(children) == 1 and children[0].returncode is not None
    assert children[0].stdout.closed
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


@pytest.mark.parametrize("verify", [None, ["{python}", "-c", "pass"]])
def test_final_observer_failure_reclaims_workspace(verify, sandboxes):
    def fail(line):
        if line.startswith(("[done", "[verify]")):
            raise ValueError("final observer failed")

    with pytest.raises(ValueError, match="final observer failed"):
        run("stream", on_line=fail, verify=verify)
    assert len(sandboxes) == 1 and not sandboxes[0].exists()


def test_cleanup_leaves_preexisting_siblings_and_records_alone(tmp_path, sandboxes):
    sibling = tmp_path / "code-existing"
    sibling.mkdir()
    record = sibling / "output.txt"
    record.write_text("keep")
    saved = tmp_path / "compare.jsonl"
    saved.write_text("saved result")
    for _ in range(3):
        run("case")
    assert len(sandboxes) == 3 and all(not path.exists() for path in sandboxes)
    assert record.read_text() == "keep"
    assert saved.read_text() == "saved result"
