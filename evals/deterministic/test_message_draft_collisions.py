"""Every successful outbox call must retain its own draft, even in one second."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

from weasley_assistant.tools import messages
from weasley_assistant.tools.registry import ToolRegistry

STAMP = "20261103T190000"


class FrozenDatetime:
    @staticmethod
    def now(tz):
        assert tz is UTC
        return datetime(2026, 11, 3, 19, 0, tzinfo=UTC)


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    (tmp_path / "outbox").mkdir()
    monkeypatch.setattr(messages, "datetime", FrozenDatetime)
    return tmp_path / "outbox"


def contents(outbox):
    return {p.read_text(encoding="utf-8") for p in outbox.glob("*.txt")}


def test_first_draft_keeps_existing_filename_and_receipt(outbox):
    result = messages.make_tool(outbox.parent).fn("Alex", "Hello")
    assert f"({STAMP}-Alex.txt)" in result
    assert "Nothing was sent" in result
    assert contents(outbox) == {"To: Alex\n\nHello\n"}


@pytest.mark.parametrize("recipients", [
    ("Alex", "Alex"),
    ("alex@example.com", "alex.example.com"),
    ("x" * 40 + "a", "x" * 40 + "b"),
    ("", ""),
    ("./../", "-----"),
])
def test_same_second_calls_preserve_each_draft(outbox, recipients):
    tool = messages.make_tool(outbox.parent)
    receipts = [tool.fn(to, f"Body {i}") for i, to in enumerate(recipients)]
    assert len(set(receipts)) == 2
    assert len(list(outbox.glob("*.txt"))) == 2
    assert contents(outbox) == {
        f"To: {to}\n\nBody {i}\n" for i, to in enumerate(recipients)
    }


def test_new_tool_instance_does_not_replace_existing_draft(outbox):
    messages.make_tool(outbox.parent).fn("Alex", "First")
    messages.make_tool(outbox.parent).fn("Alex", "Second")
    assert contents(outbox) == {"To: Alex\n\nFirst\n", "To: Alex\n\nSecond\n"}


def test_existing_suffixes_are_preserved(outbox):
    for suffix in ("", "-1", "-2"):
        (outbox / f"{STAMP}-Alex{suffix}.txt").write_text(f"Kept{suffix}", encoding="utf-8")
    receipt = messages.make_tool(outbox.parent).fn("Alex", "New")
    assert f"({STAMP}-Alex-3.txt)" in receipt
    assert contents(outbox) == {"Kept", "Kept-1", "Kept-2", "To: Alex\n\nNew\n"}


def test_concurrent_calls_preserve_all_drafts(outbox):
    count = 24
    barrier = Barrier(count)

    def draft(index):
        tool = messages.make_tool(outbox.parent)
        barrier.wait(timeout=10)
        return tool.fn("Alex", f"Draft {index}")

    with ThreadPoolExecutor(max_workers=count) as pool:
        receipts = list(pool.map(draft, range(count)))
    assert len(set(receipts)) == count
    assert len(list(outbox.glob("*.txt"))) == count
    assert contents(outbox) == {f"To: Alex\n\nDraft {i}\n" for i in range(count)}


def test_utf8_and_multiline_body_remain_intact(outbox):
    body = "안녕하세요 👋\nNew line\n"
    messages.make_tool(outbox.parent).fn("홍길동", body)
    messages.make_tool(outbox.parent).fn("홍길동", body)
    paths = list(outbox.glob("*.txt"))
    assert len(paths) == 2
    assert all(p.read_bytes() == f"To: 홍길동\n\n{body}\n".encode() for p in paths)


def test_noncollision_error_is_reported_without_retry(outbox, monkeypatch):
    calls = []

    def denied_open(path, *args, **kwargs):
        calls.append(path)
        raise PermissionError("outbox unavailable")

    monkeypatch.setattr(Path, "open", denied_open)
    registry = ToolRegistry()
    registry.register(messages.make_tool(outbox.parent))
    result = registry.execute("send_message", {"to": "Alex", "body": "No receipt"})
    assert result == "Error running send_message: outbox unavailable"
    assert len(calls) == 1
