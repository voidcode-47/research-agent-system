"""Protocol failures must remain failures across the streaming boundary."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import APIError, APIStatusError, APITimeoutError

from llm.openai_llm import OpenAICompatibleLLM
from llm.protocol_llm import CustomProtocolLLM

MESSAGES = [{"role": "user", "content": "Explain the method."}]


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def __iter__(self):
        yield self.payload

    def close(self):
        self.closed = True


def _custom(protocol, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=60)
    with patch("llm.protocol_llm.httpx.Client", return_value=client):
        llm = CustomProtocolLLM(protocol, "test-key", "https://example.invalid", "test-model", "test")
    return llm, client


def _events(protocol, *, finish=True, truncated=False, error=False):
    if protocol == "anthropic":
        frames = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Evidence."}}]
        if error:
            frames.append({"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
        elif finish:
            frames.extend([
                {"type": "message_delta", "delta": {"stop_reason": "max_tokens" if truncated else "end_turn"}},
                {"type": "message_stop"},
            ])
    else:
        candidate = {"content": {"parts": [{"text": "Evidence."}]}}
        if finish and not error:
            candidate["finishReason"] = "MAX_TOKENS" if truncated else "STOP"
        frames = [{"candidates": [candidate]}]
        if error:
            frames.append({"error": {"code": 503, "message": "busy"}})
    return ("\n\n".join("data: " + json.dumps(frame) for frame in frames) + "\n\n").encode()


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
def test_custom_chat_honors_request_timeout(protocol):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        payload = {"content": [{"type": "text", "text": "Evidence."}], "stop_reason": "end_turn"} \
            if protocol == "anthropic" else {"candidates": [{"content": {"parts": [{"text": "Evidence."}]}, "finishReason": "STOP"}]}
        return httpx.Response(200, json=payload)

    llm, client = _custom(protocol, handler)
    with client:
        assert llm.chat(MESSAGES, timeout=180).content == "Evidence."
    assert seen[0]["read"] == 180


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
@pytest.mark.parametrize("failure", [401, 429, 503, "timeout"])
def test_custom_stream_transport_failures_raise_instead_of_yielding_text(protocol, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(failure, json={"error": {"message": "simulated provider failure"}})

    llm, client = _custom(protocol, handler)
    with client, pytest.raises((httpx.HTTPError, RuntimeError)):
        list(llm.stream_chat(MESSAGES))


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
@pytest.mark.parametrize("failure", ["missing-finish", "truncated", "provider-event"])
def test_custom_stream_incomplete_or_error_events_cannot_finish_successfully(protocol, failure):
    stream = TrackingStream(_events(protocol, finish=failure != "missing-finish",
                                    truncated=failure == "truncated", error=failure == "provider-event"))
    llm, client = _custom(protocol, lambda request: httpx.Response(200, stream=stream))
    with client, pytest.raises((RuntimeError, httpx.HTTPError)):
        list(llm.stream_chat(MESSAGES))
    assert stream.closed


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
def test_custom_stream_success_closes_response_and_honors_timeout(protocol):
    stream = TrackingStream(_events(protocol))
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, stream=stream)

    llm, client = _custom(protocol, handler)
    with client:
        assert list(llm.stream_chat(MESSAGES, timeout=180)) == ["Evidence."]
    assert stream.closed
    assert seen[0]["read"] == 180


class OpenAIStream:
    def __init__(self, finish_reason="stop"):
        self.finish_reason = finish_reason
        self.closed = False

    def __iter__(self):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Evidence."), finish_reason=None)])
        if self.finish_reason:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason=self.finish_reason)])

    def close(self):
        self.closed = True


def _openai(create):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch("llm.openai_llm.OpenAI", return_value=client):
        return OpenAICompatibleLLM("test-key", "https://example.invalid/v1", "test-model")


@pytest.mark.parametrize("failure", [401, 429, 503, "timeout"])
def test_openai_stream_transport_failures_raise_instead_of_yielding_text(failure):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")

    def create(**kwargs):
        if failure == "timeout":
            raise APITimeoutError(request=request)
        raise APIStatusError("simulated provider failure", response=httpx.Response(failure, request=request), body={})

    llm = _openai(create)
    with pytest.raises((APIError, RuntimeError)):
        list(llm.stream_chat(MESSAGES))


@pytest.mark.parametrize("finish_reason", [None, "length", "content_filter"])
def test_openai_stream_requires_successful_finish_and_closes_response(finish_reason):
    stream = OpenAIStream(finish_reason)
    llm = _openai(lambda **kwargs: stream)
    with pytest.raises(RuntimeError):
        list(llm.stream_chat(MESSAGES))
    assert stream.closed


def test_openai_stream_success_closes_response():
    stream = OpenAIStream()
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return stream

    llm = _openai(create)
    assert list(llm.stream_chat(MESSAGES, timeout=180)) == ["Evidence."]
    assert seen["timeout"] == 180
    assert stream.closed


def test_openai_stream_caller_close_closes_provider_response():
    stream = OpenAIStream()
    llm = _openai(lambda **kwargs: stream)
    generator = llm.stream_chat(MESSAGES)
    assert next(generator) == "Evidence."
    generator.close()
    assert stream.closed
