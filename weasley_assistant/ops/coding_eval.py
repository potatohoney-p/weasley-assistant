"""Cross-model coding eval — pi as the fixed harness, the brain swapped underneath.

The agentic battery (evals/dataset.jsonl) scores whether a model drives WeasleyAssistant's
OWN tools. A coding case is a different animal: it hands a real programming job to
**pi** (the same sub-agent `delegate_task` uses), but pointed at the CONTESTANT's
model, then scores by RUNNING the produced code — the `verify` command's exit code
is the verdict, SWE-bench style, not a judge's opinion.

pi natively speaks every provider we pin, so one harness auditions every brain:

    pi --provider <p> --model <m> --api-key <k> -p "<task>"

WeasleyAssistant stays the orchestrator; pi stays the contractor — we just get to compare
contractors. Coding cases live in `evals/coding.jsonl` (separate from the agentic
dataset so they never run through the tool-calling tier by mistake).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from weasley_assistant.loop.models import PROVIDERS

_CODING = Path(__file__).resolve().parents[2] / "evals" / "coding.jsonl"

# WeasleyAssistant provider id -> pi's built-in provider id (see `pi --list-models`).
PI_PROVIDER = {
    "anthropic": "anthropic", "openai": "openai", "gemini": "google",
    "kimi": "moonshotai", "xai": "xai", "glm": "zai",
    "deepseek": "deepseek", "minimax": "minimax", "openrouter": "openrouter",
    "opencode_zen": "opencode_zen", "opencode_go": "opencode_go",
}


def load_coding_cases() -> list[dict]:
    """Every coding case in file order; empty list if the file is missing."""
    if not _CODING.exists():
        return []
    lines = _CODING.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def pi_available() -> bool:
    return shutil.which("pi") is not None


def _run_verify(verify: str | list[str], workdir: Path) -> subprocess.CompletedProcess:
    """Run a coding-case verifier in its worktree.

    String verifiers remain supported for cases that need shell syntax.  An
    argv verifier avoids the shell entirely and may use ``{python}`` as its
    first item to mean the exact interpreter running Weasley Assistant.  That
    keeps the bundled Python cases portable across Windows, macOS, and Linux.
    """
    if isinstance(verify, str):
        return subprocess.run(
            verify, shell=True, cwd=workdir, capture_output=True,
            text=True, timeout=120, check=False,
        )
    argv = [sys.executable if part == "{python}" else str(part) for part in verify]
    return subprocess.run(
        argv, shell=False, cwd=workdir, capture_output=True,
        text=True, timeout=120, check=False,
    )


def _seed_files(workdir: Path, files: dict | None, unchanged_files: list[str] | None) -> dict[str, bytes]:
    """Seed a trial and snapshot explicitly protected inputs before pi runs."""
    files = files or {}
    names = [] if unchanged_files is None else unchanged_files
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not name or name not in files
        or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
        or "\\" in name or ".." in PurePosixPath(name).parts or name == "."
        for name in names
    ):
        raise ValueError("unchanged_files must list relative paths present in files")
    for name, content in files.items():
        (workdir / name).write_text(content, encoding="utf-8")
    # Snapshot actual bytes, including platform newline conversion, not the text
    # from the dataset. Duplicate names require only one snapshot.
    return {name: (workdir / name).read_bytes() for name in names}


def _unchanged_error(workdir: Path, snapshots: dict[str, bytes]) -> str | None:
    """Check observable file identity, not an OS sandbox or a hostile-code guard."""
    for name, original in snapshots.items():
        path = workdir / name
        try:
            # A symlink (also in a parent directory) is a replacement, even if
            # its target happens to contain the original bytes right now.
            if any(part.is_symlink() for part in (path, *path.parents) if part == workdir
                   or workdir in part.parents):
                return f"unchanged file modified: {name}"
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != len(original):
                return f"unchanged file modified: {name}"
            if path.read_bytes() != original:
                return f"unchanged file modified: {name}"
        except OSError:
            return f"unchanged file missing or unreadable: {name}"
    return None


def coding_case_for_message(message: str, cases: list[dict] | None = None) -> dict | None:
    """The coding case whose input matches this prompt (trimmed exact match), so
    the arena knows to seed its files + score by its `verify`. None for a
    free-form coding prompt ("build snake and run it") — pi still runs and
    streams, there's just no test to score against."""
    msg = (message or "").strip()
    for c in (cases if cases is not None else load_coding_cases()):
        if (c.get("input") or "").strip() == msg:
            return c
    return None


def run_coding_stream(provider: str, model: str, task: str, files: dict | None,
                      verify: str | list[str] | None, on_line,
                      timeout: int = 300, *, unchanged_files: list[str] | None = None) -> tuple:
    """Like run_coding_case, but STREAMS pi's stdout line-by-line to `on_line` as
    it works — so the arena can show the terminal doing the job live. Returns
    (passed, why, seconds); `passed` is None when there's no verify (a free-form
    prompt just runs, nothing to score). ``unchanged_files`` lists seeded files
    whose bytes must survive both pi and verification unchanged."""
    pi_bin = shutil.which("pi")
    if not pi_bin:
        on_line("pi is not installed")
        return (False, "pi not installed", 0.0)
    pi_prov = PI_PROVIDER.get(provider)
    if not pi_prov:
        return (False, f"pi has no provider mapping for '{provider}'", 0.0)
    key = _key_for(provider)
    if not key:
        prov = PROVIDERS.get(provider)
        return (False, f"no api key ({prov.key_env if prov else provider})", 0.0)

    workdir = Path(tempfile.mkdtemp(prefix=f"code-{provider}-"))
    snapshots = _seed_files(workdir, files, unchanged_files)
    on_line(f"$ pi --provider {pi_prov} --model {model} -p …")

    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [pi_bin, "--provider", pi_prov, "--model", model, "--api-key", key,
             "-p", task, "-a", "--no-session"],
            cwd=workdir, stdin=subprocess.DEVNULL,   # no TTY under the server: pi
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # must not block on stdin
            text=True, bufsize=1, env=os.environ.copy())
    except OSError as exc:
        return (False, f"couldn't launch pi: {exc}", round(time.perf_counter() - t0, 1))

    killer = threading.Timer(timeout, proc.kill)   # watchdog: kill a hung pi
    killer.start()
    try:
        for line in proc.stdout:                    # blocks per line until pi exits
            on_line(line.rstrip("\n"))
        proc.wait()
    finally:
        killer.cancel()
    secs = round(time.perf_counter() - t0, 1)

    if why := _unchanged_error(workdir, snapshots):
        on_line(f"[verify] FAILED — {why}")
        return (False, why, secs)
    if not verify:
        on_line("[done — no test to score]")
        return (None, "ran (no test)", secs)
    try:
        v = _run_verify(verify, workdir)
    except subprocess.TimeoutExpired:
        on_line("[verify timed out]")
        return (False, "verify timed out", secs)
    if why := _unchanged_error(workdir, snapshots):
        on_line(f"[verify] FAILED — {why}")
        return (False, why, secs)
    if v.returncode == 0:
        on_line("[verify] tests pass")
        return (True, "tests pass", secs)
    tail = (v.stdout or v.stderr).strip().splitlines()
    why = tail[-1][:120] if tail else "tests failed"
    on_line(f"[verify] FAILED — {why}")
    return (False, why, secs)


def _key_for(provider: str) -> str:
    prov = PROVIDERS.get(provider)
    return os.getenv(prov.key_env, "") if prov else ""


def run_coding_case(provider: str, model: str, case: dict,
                    timeout: int = 300) -> tuple[bool, str, float]:
    """Run one coding case on (provider, model) through pi, then score by the
    case's `verify` command. Returns (passed, why, seconds).

    The judgment is the exit code of `verify`, run in the sandbox pi worked in —
    so "passed" means the code actually does what was asked, however the model
    got there. ``unchanged_files`` additionally enforces explicit no-edit
    instructions for seeded files. Nothing here trusts pi's prose."""
    pi_bin = shutil.which("pi")
    if not pi_bin:
        return (False, "pi not installed", 0.0)
    pi_prov = PI_PROVIDER.get(provider)
    if not pi_prov:
        return (False, f"pi has no provider mapping for '{provider}'", 0.0)
    key = _key_for(provider)
    if not key:
        prov = PROVIDERS.get(provider)
        return (False, f"no api key ({prov.key_env if prov else provider})", 0.0)

    workdir = Path(tempfile.mkdtemp(prefix=f"code-{provider}-"))
    snapshots = _seed_files(workdir, case.get("files"), case.get("unchanged_files"))

    t0 = time.perf_counter()
    try:
        # -a trusts project-local files; --no-session keeps the run ephemeral.
        subprocess.run(
            [pi_bin, "--provider", pi_prov, "--model", model, "--api-key", key,
             "-p", case["input"], "-a", "--no-session"],
            cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return (False, f"pi timed out after {timeout}s", round(time.perf_counter() - t0, 1))
    except OSError as exc:
        return (False, f"couldn't launch pi: {exc}", round(time.perf_counter() - t0, 1))

    # We DON'T gate on pi's own exit code — a nonzero exit can still have written
    # working code. Score its output with verify and the explicit file contract.
    if why := _unchanged_error(workdir, snapshots):
        return (False, why, round(time.perf_counter() - t0, 1))
    verify = case.get("verify")
    if not verify:
        return (True, "no verify (ran clean)", round(time.perf_counter() - t0, 1))
    try:
        v = _run_verify(verify, workdir)
    except subprocess.TimeoutExpired:
        return (False, "verify timed out", round(time.perf_counter() - t0, 1))
    secs = round(time.perf_counter() - t0, 1)
    if why := _unchanged_error(workdir, snapshots):
        return (False, why, secs)
    if v.returncode == 0:
        return (True, "tests pass", secs)
    tail = (v.stdout or v.stderr).strip().splitlines()
    return (False, (tail[-1][:120] if tail else "tests failed"), secs)
