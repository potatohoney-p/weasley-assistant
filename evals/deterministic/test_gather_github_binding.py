"""Real gather binding, with only external reads and the model stubbed out.

Keep the CLI -> JSON rows -> binder -> graph -> synthesis path intact. No gh
process, GitHub credentials, web request, or paid model call is used here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from weasley_assistant.graph import run_graph
from weasley_assistant.ops import gather
from weasley_assistant.tools import github

REPO = "potatohoney-p/weasley-assistant"


def _rows(count):
    return [{"number": n, "title": f"Item {n}", "author": {"login": "octocat"},
             "updatedAt": "2026-10-23T00:00:00Z"} for n in range(1, count + 1)]


@pytest.fixture
def bound_gather(monkeypatch, tmp_path):
    """Exercise the same binding used by both CLI and dashboard, including draft."""
    monkeypatch.setattr(gather, "_web", lambda settings: "offline web")
    monkeypatch.setattr(gather, "_calendar", lambda settings: {
        "cal_text": "(nothing today)", "cal_event_count": 0})
    monkeypatch.setattr(gather, "_memory", lambda settings: "offline memory")

    def run(pr_result, issue_result, repo=REPO):
        calls = []
        requests = []

        def fake_gh(argv, **kwargs):
            calls.append(argv)
            assert kwargs == {"capture_output": True, "text": True,
                              "timeout": 20, "check": False}
            result = {"pr": pr_result, "issue": issue_result}[argv[1]]
            if isinstance(result, Exception):
                raise result
            code, stdout, stderr = result
            return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)

        def synthesize(**kwargs):
            requests.append(kwargs)
            assert "tools" not in kwargs
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="DIGEST")])

        monkeypatch.setattr(github.subprocess, "run", fake_gh)
        assistant = SimpleNamespace(
            settings=SimpleNamespace(home=tmp_path, model="offline", gh_repo=repo, gh_tool=False),
            client=SimpleNamespace(messages=SimpleNamespace(create=synthesize)))
        state = run_graph(gather.build_bound_graph(assistant), {})
        assert state["digest"] == "DIGEST"
        assert len(requests) == 1
        assert state["gh_text"] in requests[0]["messages"][0]["content"]
        assert not state.get("errors")
        expected = ["--repo", repo] if repo else []
        if repo == "invalid/repo/extra":
            assert calls == []
        else:
            assert calls == [
                ["gh", kind, "list", *expected, "--limit", "20", "--json",
                 "number,title,author,updatedAt,url"] for kind in ("pr", "issue")
            ]
        if state.get("draft_path"):
            draft = Path(state["draft_path"])
            assert draft.parent == tmp_path / "outbox"
            assert draft.read_text(encoding="utf-8") == "DIGEST\n"
        else:
            assert not (tmp_path / "outbox").exists()
        return state

    return run


@pytest.mark.parametrize("failed", ["pr", "issue"])
@pytest.mark.parametrize("failure,expected", [
    (FileNotFoundError(), "not installed"),
    (subprocess.TimeoutExpired(cmd="gh", timeout=20), "timed out after 20s"),
    (OSError("offline executable error"), "could not run gh (offline executable error)"),
    ((1, "", "gh auth login required"), "Run `gh auth login`"),
    ((1, "", "HTTP 403: API rate limit exceeded"), "HTTP 403: API rate limit exceeded"),
    ((0, "<html>offline failure</html>", ""), "not JSON: <html>offline failure</html>"),
])
def test_partial_failure_keeps_diagnostics_and_successful_rows(bound_gather, failed,
                                                              failure, expected):
    success = (0, json.dumps(_rows(2)), "")
    state = bound_gather(failure if failed == "pr" else success,
                         failure if failed == "issue" else success)
    label = "Open PRs" if failed == "pr" else "Open issues"
    assert f"{label}: unavailable" in state["gh_text"]
    assert expected in state["gh_text"]
    assert "unavailable — []" not in state["gh_text"]
    assert "#2 Item 2 — @octocat" in state["gh_text"]
    assert state["gh_open_prs"] == (0 if failed == "pr" else 2)
    assert state["gh_open_issues"] == (0 if failed == "issue" else 2)
    assert "draft_path" in state


def test_both_failures_reach_synthesis_without_counting_errors_as_rows(bound_gather):
    state = bound_gather((1, "", "gh auth login required"),
                         subprocess.TimeoutExpired(cmd="gh", timeout=20))
    assert "Open PRs: unavailable — gh is not authenticated" in state["gh_text"]
    assert "Open issues: unavailable — gh timed out after 20s" in state["gh_text"]
    assert state["gh_open_prs"] == state["gh_open_issues"] == 0
    assert "draft_path" not in state


@pytest.mark.parametrize("count", [0, 20])
@pytest.mark.parametrize("repo", ["", REPO])
def test_success_keeps_fetched_counts_and_empty_result_distinct(bound_gather, count, repo):
    result = (0, json.dumps(_rows(count)), "")
    state = bound_gather(result, result, repo)
    assert state["gh_open_prs"] == state["gh_open_issues"] == count
    assert "unavailable" not in state["gh_text"]
    assert ("draft_path" in state) == bool(count)
    if count:
        assert state["gh_text"].count("#20 Item 20 — @octocat") == 2
    else:
        assert state["gh_text"] == "Open PRs:\n(none)\n\nOpen issues:\n(none)"


def test_diagnostics_keep_the_tool_error_output_bound(bound_gather):
    state = bound_gather((1, "", "x" * 500 + "UNSEEN_TAIL"), (0, "[]", ""))
    assert "Open PRs: unavailable — " + "x" * 200 in state["gh_text"]
    assert "x" * 201 not in state["gh_text"]
    assert "UNSEEN_TAIL" not in state["gh_text"]
    assert state["gh_open_prs"] == 0


def test_invalid_repository_preserves_validation_reason_without_spawning_gh(bound_gather):
    state = bound_gather(None, None, "invalid/repo/extra")
    assert state["gh_text"].count("not a valid repo") == 2
    assert state["gh_open_prs"] == state["gh_open_issues"] == 0
    assert "draft_path" not in state
