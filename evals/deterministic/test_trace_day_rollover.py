"""A long-lived gateway rotates daily traces between, never within, turns."""

import json
from datetime import datetime

import pytest

from weasley_assistant.config import Settings
from weasley_assistant.ops import tracing


@pytest.fixture
def clock(tmp_path, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 2, 23, 59)

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.replace(tzinfo=tz)

    monkeypatch.setattr(tracing, "datetime", Clock)
    settings = Settings(home=tmp_path, otel_endpoint="")
    settings.ensure_home()
    return Clock, tracing.Tracer(settings)


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def complete(tracer, message):
    with tracer.turn(message):
        tracer.event("gate", {"decision": "skip"})
    tracer.end_turn("reply", 1)


def test_next_turn_rotates_and_keeps_existing_daily_records(clock, tmp_path):
    timer, tracer = clock
    complete(tracer, "yesterday")
    old = tracer.path
    old_bytes = old.read_bytes()
    new = tmp_path / "traces" / "2026-11-03.jsonl"
    new.write_text('{"type":"existing"}\n', encoding="utf-8")
    timer.current = datetime(2026, 11, 3, 0, 1)
    complete(tracer, "today")
    assert tracer.path == new
    assert old.read_bytes() == old_bytes
    assert [r["type"] for r in records(new)] == ["existing", "turn_start", "gate", "turn_end"]
    assert records(new)[1]["user_message"] == "today"


def test_midnight_does_not_split_an_inflight_turn(clock, tmp_path):
    timer, tracer = clock
    old = tracer.path
    with tracer.turn("long turn"):
        timer.current = datetime(2026, 11, 3, 0, 1)
        tracer.event("tool", {"tool": "example"})
    tracer.end_turn("done", 1)
    assert [r["type"] for r in records(old)] == ["turn_start", "tool", "turn_end"]
    assert not (tmp_path / "traces" / "2026-11-03.jsonl").exists()
    complete(tracer, "next")
    assert tracer.path.name == "2026-11-03.jsonl"


def test_new_day_revalidates_encoding_and_can_retry(clock, tmp_path):
    timer, tracer = clock
    complete(tracer, "first")
    old_bytes = tracer.path.read_bytes()
    new = tmp_path / "traces" / "2026-11-03.jsonl"
    corrupt = b'\xff\xfe legacy\n'
    new.write_bytes(corrupt)
    timer.current = datetime(2026, 11, 3, 0, 1)
    with pytest.raises(tracing.TraceEncodingError):
        complete(tracer, "must not write")
    assert new.read_bytes() == corrupt
    assert (tmp_path / "traces" / "2026-11-02.jsonl").read_bytes() == old_bytes
    # Simulate the operator preserving/replacing the invalid file, then retry.
    new.write_text('', encoding="utf-8")
    complete(tracer, "retry")
    assert records(new)[0]["user_message"] == "retry"


def test_clock_backward_reselects_and_revalidates_old_day(clock, tmp_path):
    timer, tracer = clock
    complete(tracer, "first")
    old = tracer.path
    timer.current = datetime(2026, 11, 3)
    complete(tracer, "second")
    old.write_bytes(b'\xff')
    timer.current = datetime(2026, 11, 2, 23, 58)
    with pytest.raises(tracing.TraceEncodingError):
        complete(tracer, "clock corrected")
    assert old.read_bytes() == b'\xff'
