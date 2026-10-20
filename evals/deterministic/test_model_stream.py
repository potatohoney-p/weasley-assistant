"""Offline wire-contract tests for streamed OpenAI-compatible tool turns."""

import json
from types import SimpleNamespace

import openai
import pytest
from openai import _base_client

from weasley_assistant.loop.models import OpenAICompatClient

# Use the HTTP implementation shipped with the installed SDK; current releases
# use httpx2, while the supported older SDKs use httpx.
httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx


class ResponseStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = 0

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self.chunks)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def close(self):
        self.closed += 1


def chunk(*, text=None, calls=None, usage=None):
    delta = SimpleNamespace(content=text, tool_calls=calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta)] if text or calls else [],
        usage=usage,
    )


def call(index, *, call_id=None, name=None, arguments=None, extra=None):
    return SimpleNamespace(
        index=index, id=call_id, extra_content=extra,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def client_with_stream(chunks):
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    source = ResponseStream(chunks)
    client._call = lambda kwargs, **extra: source
    return client, source


def stream_turn(client):
    return client._stream(model="test-model", messages=[], max_tokens=10)


@pytest.mark.parametrize("signature_first", [True, False])
def test_streamed_tool_metadata_survives_fragmentation_and_next_request(signature_first):
    signature = {"google": {"thought_signature": "opaque-signature"}}
    client, _ = client_with_stream([
        chunk(text="Checking "),
        chunk(calls=[call(0, call_id="t1", name="lookup", arguments='{"city":',
                         extra=signature if signature_first else None)]),
        chunk(calls=[call(1, call_id="t2", name="lookup", arguments='{"city":"Paris"}')]),
        chunk(calls=[call(0, arguments='"Seoul"}',
                         extra=None if signature_first else signature)]),
        chunk(text="now."),
        chunk(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=11)),
    ])
    with stream_turn(client) as stream:
        assert list(stream.text_stream) == ["Checking ", "now."]
        final = stream.get_final_message()

    assert final.stop_reason == "tool_use"
    assert final.usage.input_tokens == 7
    assert final.usage.output_tokens == 11
    kwargs = client._to_openai(model="test-model", max_tokens=10, messages=[
        {"role": "assistant", "content": final.content},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "Seoul result"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "Paris result"},
        ]},
    ])
    first, second = kwargs["messages"][0]["tool_calls"]
    assert first["id"] == "t1"
    assert first["function"] == {"name": "lookup", "arguments": '{"city": "Seoul"}'}
    assert first["extra_content"] == signature
    assert second["id"] == "t2"
    assert "extra_content" not in second
    assert [message["tool_call_id"] for message in kwargs["messages"][1:]] == ["t1", "t2"]


def test_stream_closes_response_on_normal_completion():
    client, source = client_with_stream([chunk(text="Done")])
    with stream_turn(client) as stream:
        assert list(stream.text_stream) == ["Done"]
        assert stream.get_final_message().content[0].text == "Done"
        assert source.closed == 1
    assert source.closed == 1


@pytest.mark.parametrize("consumer_error", [False, True])
def test_stream_closes_response_when_consumer_stops_early(consumer_error):
    client, source = client_with_stream([chunk(text="First"), chunk(text="Second")])
    iterator = None
    try:
        with stream_turn(client) as stream:
            iterator = stream.text_stream
            assert next(iterator) == "First"
            if consumer_error:
                raise RuntimeError("consumer failed")
    except RuntimeError as error:
        assert consumer_error and str(error) == "consumer failed"
    assert source.closed == 1
    iterator.close()
    assert source.closed == 1


def test_stream_closes_response_on_iterator_failure():
    client, source = client_with_stream([chunk(text="First"), RuntimeError("transport failed")])
    with pytest.raises(RuntimeError, match="transport failed"), stream_turn(client) as stream:
        list(stream.text_stream)
    assert source.closed == 1


def test_usage_only_trailer_is_not_an_error():
    client, _ = client_with_stream([
        chunk(text="Done"), chunk(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3)),
    ])
    with stream_turn(client) as stream:
        assert list(stream.text_stream) == ["Done"]
        final = stream.get_final_message()
    assert final.usage.input_tokens == 2
    assert final.usage.output_tokens == 3


class SSEBody(httpx.SyncByteStream):
    """A real SDK response body without a socket or provider credential."""

    def __init__(self, deltas):
        self.deltas = deltas
        self.closed = 0

    def __iter__(self):
        for delta in self.deltas:
            payload = {
                "id": "test-completion", "object": "chat.completion.chunk",
                "created": 0, "model": "test-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            yield f"data: {json.dumps(payload)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    def close(self):
        self.closed += 1


def sdk_client(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=body,
    ))
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client._client = openai.OpenAI(api_key="offline-test", http_client=httpx.Client(
        transport=transport,
    ))
    return client


def test_real_sdk_preserves_streamed_signature_without_network():
    signature = {"google": {"thought_signature": "synthetic-signature"}}
    body = SSEBody([
        {"tool_calls": [{"index": 0, "id": "t1", "type": "function",
                         "function": {"name": "lookup", "arguments": "{}"},
                         "extra_content": signature}]},
    ])
    client = sdk_client(body)
    try:
        with stream_turn(client) as stream:
            assert list(stream.text_stream) == []
            final = stream.get_final_message()
        request = client._to_openai(model="test-model", max_tokens=10, messages=[
            {"role": "assistant", "content": final.content},
        ])
        assert request["messages"][0]["tool_calls"][0]["extra_content"] == signature
    finally:
        client._client.close()


def test_real_sdk_response_closes_before_early_exit_returns():
    body = SSEBody([{"content": "First"}, {"content": "Second"}])
    client = sdk_client(body)
    try:
        with stream_turn(client) as stream:
            iterator = stream.text_stream
            assert next(iterator) == "First"
            assert body.closed == 0
        # Keep the generator alive: collection is not a reliable release point.
        assert body.closed == 1
        iterator.close()
        assert body.closed == 1
    finally:
        client._client.close()
