"""A published streamed response must not silently become a second completion."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from weasley_assistant.loop.agent import run_loop
from weasley_assistant.tools.registry import ToolRegistry


def response(text="fallback"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


class Messages:
    def __init__(self, failure):
        self.failure = failure
        self.creates = 0
        self.closed = False

    @contextmanager
    def stream(self, **kwargs):
        try:
            if self.failure == "enter":
                raise RuntimeError("stream failed")
            yield self
            if self.failure in ("exit", "exit_empty"):
                raise RuntimeError("stream failed")
        finally:
            self.closed = True

    @property
    def text_stream(self):
        if self.failure == "before_text":
            raise RuntimeError("stream failed")
        if self.failure == "exit_empty":
            return
        yield "published"
        if self.failure == "after_text":
            raise RuntimeError("stream failed")

    def get_final_message(self):
        if self.failure == "final":
            raise RuntimeError("stream failed")
        return response("published")

    def create(self, **kwargs):
        self.creates += 1
        return response()


def run(api, history, observer=None):
    return run_loop(SimpleNamespace(messages=api), "offline", "", history,
                    ToolRegistry(), observer=observer, stream=True)


@pytest.mark.parametrize("failure", ["after_text", "final", "exit", "observer"])
def test_failure_after_publication_does_not_request_another_completion(failure):
    api = Messages(failure)
    history = []
    events = []

    def observe(kind, event):
        events.append((kind, event))
        if failure == "observer":
            raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="failed"):
        run(api, history, observe)
    assert api.creates == 0
    assert history == []
    assert api.closed
    assert events == [("text", {"delta": "published"})]


@pytest.mark.parametrize("failure", ["enter", "before_text"])
def test_stream_failure_before_publication_keeps_single_call_fallback(failure):
    api = Messages(failure)
    history = []
    assert run(api, history).reply == "fallback"
    assert api.creates == 1
    assert api.closed
    assert len(history) == 1


def test_successful_stream_does_not_fallback():
    api = Messages(None)
    history = []
    assert run(api, history).reply == "published"
    assert api.creates == 0
    assert api.closed
    assert len(history) == 1


def test_non_streaming_client_keeps_single_call_fallback():
    calls = []
    api = SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response())
    assert run(api, []).reply == "fallback"
    assert len(calls) == 1


def test_cleanup_failure_after_empty_completed_response_does_not_replay():
    api = Messages("exit_empty")
    history = []
    with pytest.raises(RuntimeError, match="stream failed"):
        run(api, history)
    assert api.creates == 0
    assert api.closed
    assert history == []


def test_partial_stream_without_observer_also_does_not_replay():
    api = Messages("after_text")
    with pytest.raises(RuntimeError, match="stream failed"):
        run(api, [])
    assert api.creates == 0
    assert api.closed
